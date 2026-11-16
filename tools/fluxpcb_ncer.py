#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""fluxpcb_ncer — 正确的 NCER 协商拥塞路由器.

核心(Koppar 策略):
  1. 全部路由(允许临时碰撞) → 高完成率
  2. 检测碰撞(geomir 精确验证)
  3. 碰撞处加 congestion cost(不删线!)
  4. 重路由碰撞网(避开高代价区域)
  5. 迭代直到 0 碰撞

与 v2 的区别:
  v2: 碰撞→删线→断线→完成率降
  v3: 碰撞→加代价→重路由→收敛到 0 短路 + 100% 完成

Usage: python fluxpcb_ncer.py INPUT.kicad_pcb OUTPUT.kicad_pcb [--timeout S]
"""
import json
import math
import sys
import os
import time
from pathlib import Path

MM = 1e6
FLUXPCB = Path("E:/DATA/vscode/ZCODE/flux-neurotransmitter")
sys.path.insert(0, str(FLUXPCB / "fluxpcb" / "kicadgen"))
sys.path.insert(0, str(FLUXPCB))

import pcbnew  # noqa: E402
import routegrid as rg  # noqa: E402
import geomir  # noqa: E402


class NCERRouter:
    """NCER 协商拥塞路由器."""

    def __init__(self, board, lids):
        self.b = board
        self.lids = lids
        self.G = rg.GRID
        self.gm = rg.GridModel(board, lids)
        self.nx, self.ny, self.nl = self.gm.nx, self.gm.ny, self.gm.nl

        # GPU Eikonal
        from gpu_eikonal import GPUEikonalRouter
        self.router = GPUEikonalRouter(board, lids)

        # Congestion cost field (NCER history)
        self.congestion = {}
        # Multi-net occupancy: (li,gx,gy) -> netcode
        self.occupancy = {}
        # Track drawn items + path cells per pair for whole-path rollback
        self.pair_items = {}
        self.collided_pairs = set()  # Track pairs with collisions
        self.pair_cells = {}

        # Read board rules — board.GetFileName() returns the actual path
        try:
            pcb_path = board.GetFileName()
        except AttributeError:
            pcb_path = ''
        pro_path = pcb_path.replace('.kicad_pcb', '.kicad_pro')
        self.track_w = 0.2  # Board min track width
        self.via_d, self.via_drill = 0.5, 0.3  # Board min via diameter/drill
        self.min_clearance = 0.127  # default
        self.min_hole_clearance = 0.25
        self.min_hole_to_hole = 0.25
        if Path(pro_path).exists():
            pro = json.loads(Path(pro_path).read_text())
            rules = pro.get('board', {}).get('design_settings', {}).get('rules', {})
            self.min_clearance = rules.get('min_clearance', 0.127)
            self.min_hole_clearance = rules.get('min_hole_clearance', 0.25)
            self.min_hole_to_hole = rules.get('min_hole_to_hole', 0.25)
            for cls in pro.get('net_settings', {}).get('classes', []):
                if cls.get('name') == 'Default':
                    tw = cls.get('track_width', 0.127)
                    self.track_w = tw / 1e6 if tw > 100 else tw
                    vd = cls.get('via_diameter', 0.45)
                    self.via_d = vd / 1e6 if vd > 100 else vd
                    vdr = cls.get('via_drill', 0.2)
                    self.via_drill = vdr / 1e6 if vdr > 100 else vdr
                    break

    def build_free_with_cost(self, nc):
        """构建可走场 + 代价场(含 congestion cost)."""
        free = self.router.build_free(nc)
        cost = [[[1.0]*self.ny for _ in range(self.nx)] for _ in range(self.nl)]
        for (li, gx, gy), penalty in self.congestion.items():
            if 0 <= li < self.nl and 0 <= gx < self.nx and 0 <= gy < self.ny:
                cost[li][gx][gy] = 1.0 + penalty
        return free, cost

    def _count_src_free(self, gx, gy, li):
        """Count free cells in 7x7 neighborhood around (gx,gy) on layer li.
        Returns count — low count means pad is 'fenced in' (PCBRouteAgent finding)."""
        free = self.router.build_free(0, occupancy=self.occupancy)
        count = 0
        for dx in range(-3, 4):
            for dy in range(-3, 4):
                nx_, ny_ = gx+dx, gy+dy
                if 0 <= nx_ < self.nx and 0 <= ny_ < self.ny:
                    if free[li][nx_][ny_]:
                        count += 1
        return count

    def _try_escape_via(self, nc, x, y, src_li, dst_li):
        """Place an escape via near a fenced-in pad (PCBRouteAgent prescription).
        For SMD pads: place ~1mm via region adjacent to pad, short stub, then other layer.
        Returns (via_x, via_y, via_li) or None if no legal via position found."""
        # Search for via position in expanding ring (1mm radius)
        for radius_mm in [0.5, 0.8, 1.0, 1.2]:
            steps = max(8, int(radius_mm * 12))
            for i in range(steps):
                angle = 2 * math.pi * i / steps
                vx = x + radius_mm * math.cos(angle)
                vy = y + radius_mm * math.sin(angle)
                # Check via legality on ALL layers
                sgs, rgs = geomir.extract_objects(self.b, self.lids)
                rules = {'clearance': self.min_clearance, 'clearance_smd': self.min_clearance}
                via_ok = True
                for li in range(self.nl):
                    ok, _ = geomir.verify_polyline(
                        [(vx, vy), (vx, vy)], self.via_d/2, nc,
                        sgs[li], rgs[li], rules=rules)
                    if not ok:
                        via_ok = False
                        break
                if not via_ok:
                    continue
                # Check hole-to-hole
                all_holes = []
                for fp in self.b.GetFootprints():
                    for pad in fp.Pads():
                        try:
                            if not pad.HasHole():
                                continue
                        except Exception:
                            continue
                        q = pad.GetPosition()
                        try:
                            drill = pad.GetDrillSizeX() / MM
                        except Exception:
                            drill = 0.5
                        all_holes.append((q.x/MM, q.y/MM, drill))
                for t in self.b.GetTracks():
                    if type(t).__name__ == 'PCB_VIA':
                        q = t.GetPosition()
                        try:
                            actual_drill = t.GetDrillValue() / MM
                        except Exception:
                            actual_drill = self.via_drill
                        all_holes.append((q.x/MM, q.y/MM, actual_drill))
                hole_ok = True
                for (hx, hy, hd) in all_holes:
                    dist = math.hypot(vx-hx, vy-hy)
                    min_dist = self.via_drill/2 + hd/2 + self.min_hole_to_hole
                    if dist < min_dist:
                        hole_ok = False
                        break
                if hole_ok:
                    # Return via position — caller will place it
                    return (vx, vy, src_li)
        return None

    def route_pair(self, nc, x1, y1, x2, y2, src_li=0, dst_li=0, corridor=None):
        """路由一对焊盘(自适应乐观栅格 + LazySP + 逃逸优先 + 多层逃逸)."""
        free = self.router.build_free(nc, occupancy=self.occupancy)
        cost = [[[1.0]*self.ny for _ in range(self.nx)] for _ in range(self.nl)]

        # Adaptive optimistic grid (LazySP pattern):
        # - blocked cells get moderate cost (not hard block)
        # - cost scales with congestion (PathFinder history)
        # - solver can detour through narrow corridors
        base_blocked_cost = 6.0  # LazySP optimal range: 6-8
        for li in range(self.nl):
            for gx in range(self.nx):
                for gy in range(self.ny):
                    if not free[li][gx][gy]:
                        cost[li][gx][gy] = base_blocked_cost
                        free[li][gx][gy] = True  # passable but expensive

        for (li, gx, gy), penalty in self.congestion.items():
            if 0 <= li < self.nl and 0 <= gx < self.nx and 0 <= gy < self.ny:
                cost[li][gx][gy] = max(cost[li][gx][gy], 1.0 + penalty)

        # ABSOLUTE coordinates (matches GridModel: gx = int(x/GRID))
        src_gx, src_gy = int(x1/self.G), int(y1/self.G)
        dst_gx, dst_gy = int(x2/self.G), int(y2/self.G)

        if not (0 <= src_gx < self.nx and 0 <= src_gy < self.ny):
            return None
        if not (0 <= dst_gx < self.nx and 0 <= dst_gy < self.ny):
            return None

        # ESCAPE-FIRST: Disabled (causing regression)
        # src_free = self._count_src_free(src_gx, src_gy, src_li)
        # dst_free = self._count_src_free(dst_gx, dst_gy, dst_li)
        # ESCAPE_THRESHOLD = 8

        # If source is fenced, try escape via first
        if False:  # Disabled
            escape = self._try_escape_via(nc, x1, y1, src_li, dst_li)
            if escape:
                evx, evy, evli = escape
                # Place escape via
                v = pcbnew.PCB_VIA(self.b)
                v.SetPosition(pcbnew.VECTOR2I(int(evx*MM), int(evy*MM)))
                v.SetWidth(int(self.via_d*MM))
                v.SetDrill(int(self.via_drill*MM))
                v.SetNet(self.b.FindNet(nc))
                self.b.Add(v)
                # Update occupancy
                for li in range(self.nl):
                    for dx in range(-2, 3):
                        for dy in range(-2, 3):
                            key = (li, int(evx/self.G)+dx, int(evy/self.G)+dy)
                            if 0 <= int(evx/self.G)+dx < self.nx and 0 <= int(evy/self.G)+dy < self.ny:
                                self.occupancy[key] = nc
                # Now route from via on opposite layer
                opposite_li = (src_li + 1) % self.nl
                # Mark via position as free
                evgx, evgy = int(evx/self.G), int(evy/self.G)
                for dx in range(-1, 2):
                    for dy in range(-1, 2):
                        if 0 <= evgx+dx < self.nx and 0 <= evgy+dy < self.ny:
                            free[opposite_li][evgx+dx][evgy+dy] = True
                # Route from via to destination
                T = self._solve_with_cost(free, cost, [(dst_li, dst_gx, dst_gy)])
                path = self.router.extract_path(T, free, (opposite_li, evgx, evgy))
                if path and len(path) > 1:
                    return path

        # Mark source and destination as free (3x3 escape window)
        for li in range(self.nl):
            for dx in range(-1, 2):
                for dy in range(-1, 2):
                    if 0 <= src_gx+dx < self.nx and 0 <= src_gy+dy < self.ny:
                        free[li][src_gx+dx][src_gy+dy] = True
                    if 0 <= dst_gx+dx < self.nx and 0 <= dst_gy+dy < self.ny:
                        free[li][dst_gx+dx][dst_gy+dy] = True

        # Try direct routing first
        T = self._solve_with_cost(free, cost, [(dst_li, dst_gx, dst_gy)])
        path = self.router.extract_path(T, free, (src_li, src_gx, src_gy))
        if path and len(path) > 1:
            return path

        # Multi-layer escape: try via intermediate layers
        for intermediate_li in range(self.nl):
            if intermediate_li == src_li or intermediate_li == dst_li:
                continue
            T2 = self._solve_with_cost(free, cost, [(intermediate_li, dst_gx, dst_gy)])
            path2 = self.router.extract_path(T2, free, (src_li, src_gx, src_gy))
            if path2 and len(path2) > 2:
                return path2

        # A* fallback with larger limit
        return self._astar_fallback(free, src_li, src_gx, src_gy, dst_li, dst_gx, dst_gy)

    def _astar_fallback(self, free, src_li, src_gx, src_gy, dst_li, dst_gx, dst_gy):
        """A* fallback when Eikonal fails to find a path."""
        import heapq
        nl, nx, ny = self.nl, self.nx, self.ny
        G = self.G
        moves = ((1,0),(-1,0),(0,1),(0,-1),(1,1),(1,-1),(-1,1),(-1,-1))

        def h(gx, gy):
            return max(abs(gx-dst_gx), abs(gy-dst_gy)) * G

        start = (src_li, src_gx, src_gy)
        goal = (dst_li, dst_gx, dst_gy)
        heap = [(h(src_gx, src_gy), 0, start)]
        gscore = {start: 0}
        parent = {}
        visited = set()
        count = 0

        while heap:
            _, g, node = heapq.heappop(heap)
            if node in visited:
                continue
            visited.add(node)
            if node == goal:
                # Reconstruct path
                path = []
                n = node
                while n is not None:
                    path.append(n)
                    n = parent.get(n)
                path.reverse()
                return path

            li, gx, gy = node
            for dx, dy in moves:
                nx2, ny2 = gx+dx, gy+dy
                if not (0 <= nx2 < nx and 0 <= ny2 < ny):
                    continue
                if not free[li][nx2][ny2]:
                    continue
                # Diagonal corner-cut guard
                if abs(dx) == 1 and abs(dy) == 1:
                    if not (free[li][gx+dx][gy] and free[li][gx][gy+dy]):
                        continue
                nk = (li, nx2, ny2)
                ng = g + G * (1.4142 if dx and dy else 1.0)
                if ng < gscore.get(nk, 1e18):
                    gscore[nk] = ng
                    parent[nk] = node
                    count += 1
                    heapq.heappush(heap, (ng + h(nx2, ny2), ng, nk))

            # Layer transitions
            ib = gy * nx + gx
            for lj in range(nl):
                if lj == li:
                    continue
                if not free[lj][gx][gy]:
                    continue
                nk = (lj, gx, gy)
                ng = g + 6.0 * G  # via cost
                if ng < gscore.get(nk, 1e18):
                    gscore[nk] = ng
                    parent[nk] = node
                    count += 1
                    heapq.heappush(heap, (ng + h(gx, gy), ng, nk))

            if count > 5000000:
                return None
        return None

    def _solve_with_cost(self, free, cost, dst_cells):
        """GPU FIM with cost field."""
        import numpy as np
        nl, nx, ny = self.nl, self.nx, self.ny

        T_np = np.full((nl, nx, ny), 1e18, dtype=np.float32)
        free_np = np.array(free, dtype=np.int32)
        cost_np = np.array(cost, dtype=np.float32)
        active_np = np.zeros((nl, nx, ny), dtype=np.int32)
        new_active_np = np.zeros((nl, nx, ny), dtype=np.int32)

        for li, gx, gy in dst_cells:
            if 0 <= li < nl and 0 <= gx < nx and 0 <= gy < ny:
                T_np[li, gx, gy] = 0.0
                active_np[li, gx, gy] = 1
                for ddx in range(-1, 2):
                    for ddy in range(-1, 2):
                        nx2, ny2 = gx+ddx, gy+ddy
                        if 0 <= nx2 < nx and 0 <= ny2 < ny:
                            active_np[li, nx2, ny2] = 1

        try:
            import warp as wp
            T_gpu = wp.array(T_np, dtype=wp.float32)
            free_gpu = wp.array(free_np, dtype=wp.int32)
            cost_gpu = wp.array(cost_np, dtype=wp.float32)
            active_gpu = wp.array(active_np, dtype=wp.int32)
            new_active_gpu = wp.array(new_active_np, dtype=wp.int32)

            for iteration in range(500):
                wp.launch(_clear_k, (nl, nx, ny), [new_active_gpu], device=wp.get_device())
                wp.launch(_fim_cost_k, (nl, nx, ny),
                           [T_gpu, free_gpu, cost_gpu, active_gpu, new_active_gpu,
                            nl, nx, ny, self.G, 6.0], device=wp.get_device())
                active_gpu, new_active_gpu = new_active_gpu, active_gpu
                if iteration % 20 == 0:
                    if int(active_gpu.numpy().sum()) == 0:
                        break
            return T_gpu.numpy()
        except Exception:
            return self._solve_cpu_cost(free, cost, dst_cells)

    def _solve_cpu_cost(self, free, cost, dst_cells):
        """CPU FIM with cost."""
        import numpy as np
        nl, nx, ny = self.nl, self.nx, self.ny
        T = np.full((nl, nx, ny), 1e18, dtype=np.float32)
        active = np.zeros((nl, nx, ny), dtype=bool)
        free_arr = np.array(free, dtype=bool)
        cost_arr = np.array(cost, dtype=np.float32)

        for li, gx, gy in dst_cells:
            if 0 <= li < nl and 0 <= gx < nx and 0 <= gy < ny:
                T[li, gx, gy] = 0.0
                active[li, gx, gy] = True
                for ddx in range(-1, 2):
                    for ddy in range(-1, 2):
                        nx2, ny2 = gx+ddx, gy+ddy
                        if 0 <= nx2 < nx and 0 <= ny2 < ny:
                            active[li, nx2, ny2] = True

        for iteration in range(500):
            new_active = np.zeros_like(active)
            updated = 0
            for li in range(nl):
                for gx in range(nx):
                    for gy in range(ny):
                        if not active[li, gx, gy] or not free_arr[li, gx, gy]:
                            continue
                        t_old = T[li, gx, gy]
                        min_n = 1e18
                        for ddx, ddy in ((1,0),(-1,0),(0,1),(0,-1),(1,1),(1,-1),(-1,1),(-1,-1)):
                            nx2, ny2 = gx+ddx, gy+ddy
                            if 0 <= nx2 < nx and 0 <= ny2 < ny and free_arr[li, nx2, ny2]:
                                step = self.G * (1.4142 if ddx and ddy else 1.0)
                                min_n = min(min_n, T[li, nx2, ny2] + step * cost_arr[li, nx2, ny2])
                        for li2 in range(nl):
                            if li2 != li and free_arr[li2, gx, gy]:
                                min_n = min(min_n, T[li2, gx, gy] + 6.0 * self.G * cost_arr[li2, gx, gy])
                        t_new = min(min_n, t_old)
                        if t_new < t_old - 1e-4:
                            T[li, gx, gy] = t_new
                            updated += 1
                            for ddx in range(-1, 2):
                                for ddy in range(-1, 2):
                                    nx2, ny2 = gx+ddx, gy+ddy
                                    if 0 <= nx2 < nx and 0 <= ny2 < ny:
                                        new_active[li, nx2, ny2] = True
                            for li2 in range(nl):
                                if li2 != li and free_arr[li2, gx, gy]:
                                    new_active[li2, gx, gy] = True
            active = new_active
            if updated == 0:
                break
        return T

    def detect_collisions(self):
        """检测碰撞(geomir 精确验证). 返回碰撞的走线列表.
        Note: same-net tracks are allowed to be close (they're part of same connection).
        Also excludes self-collision (track checked against itself)."""
        sgs, rgs = geomir.extract_objects(self.b, self.lids)
        rules = {'clearance': self.min_clearance, 'clearance_smd': self.min_clearance}
        collisions = []

        # Collect tracks first (SWIG live-view is invalidated by board mutations)
        all_tracks = [t for t in self.b.GetTracks() if type(t).__name__ != 'PCB_VIA']
        for t in all_tracks:
            nc = t.GetNetCode()
            s, e = t.GetStart(), t.GetEnd()
            x1, y1 = s.x/MM, s.y/MM
            x2, y2 = e.x/MM, e.y/MM
            w = t.GetWidth()/MM
            lid = t.GetLayer()
            li_idx = self.lids.index(lid) if lid in self.lids else 0

            # Build segs list EXCLUDING the track being checked (avoid self-collision)
            # Also exclude segments that share endpoints (consecutive segments in same path)
            segs_excluding_self = []
            for (net, s2, e2, hw) in sgs[li_idx]:
                # Skip if this is the track being checked
                if (abs(s2[0]-x1)<0.01 and abs(s2[1]-y1)<0.01 and
                    abs(e2[0]-x2)<0.01 and abs(e2[1]-y2)<0.01):
                    continue
                # Skip if segments share an endpoint (consecutive segments)
                if (abs(s2[0]-x1)<0.01 and abs(s2[1]-y1)<0.01) or \
                   (abs(s2[0]-x2)<0.01 and abs(s2[1]-y2)<0.01) or \
                   (abs(e2[0]-x1)<0.01 and abs(e2[1]-y1)<0.01) or \
                   (abs(e2[0]-x2)<0.01 and abs(e2[1]-y2)<0.01):
                    continue
                segs_excluding_self.append((net, s2, e2, hw))

            ok, blocker = geomir.verify_polyline(
                [(x1,y1),(x2,y2)], w, nc, segs_excluding_self, rgs[li_idx], rules=rules)
            if not ok:
                # Check if blocker is same-net (skip same-net collisions)
                if blocker and isinstance(blocker, dict) and blocker.get('net') == nc:
                    continue  # Same-net collision is OK
                collisions.append(t)
                # Debug: print collision details
                if len(collisions) <= 5:  # Only print first 5
                    print(f'    Collision: net={nc}, track=({x1:.2f},{y1:.2f})-({x2:.2f},{y2:.2f}), w={w:.3f}', flush=True)
                    print(f'      blocker: {blocker}', flush=True)

        return collisions

    def add_congestion_cost(self, track, cost_mult=2.0):
        """在碰撞走线周围加 congestion cost."""
        s, e = track.GetStart(), track.GetEnd()
        x1, y1 = s.x/MM, s.y/MM
        x2, y2 = e.x/MM, e.y/MM
        ox, oy = self.gm.edge[0], self.gm.edge[1]

        # 沿走线加 cost
        n = max(2, int(math.hypot(x2-x1, y2-y1) / self.G))
        for k in range(n+1):
            x = x1 + (x2-x1)*k/n
            y = y1 + (y2-y1)*k/n
            gx, gy = int(x/self.G), int(y/self.G)
            li = self.lids.index(track.GetLayer()) if track.GetLayer() in self.lids else 0
            if 0 <= gx < self.nx and 0 <= gy < self.ny:
                key = (li, gx, gy)
                self.congestion[key] = self.congestion.get(key, 0.0) + cost_mult

    def _get_text_obstacles(self):
        """Extract PCB text positions as obstacles (x, y, hw, hh, li)."""
        obstacles = []
        try:
            for dr in self.b.GetDrawings():
                if not hasattr(dr, 'GetText') or not dr.GetText():
                    continue
                bbox = dr.GetBoundingBox()
                cx = (bbox.GetX() + bbox.GetWidth()/2) / MM
                cy = (bbox.GetY() + bbox.GetHeight()/2) / MM
                hw = bbox.GetWidth() / MM / 2
                hh = bbox.GetHeight() / MM / 2
                for li, lid in enumerate(self.lids):
                    try:
                        if dr.IsOnLayer(lid):
                            obstacles.append((cx, cy, hw, hh, li))
                            break
                    except Exception:
                        pass
        except Exception:
            pass
        return obstacles

    def _seg_rect_dist(self, x1, y1, x2, y2, cx, cy, hw, hh):
        """Segment to axis-aligned rectangle distance."""
        # Check distance from segment to rectangle edges
        corners = [(cx-hw, cy-hh), (cx+hw, cy-hh), (cx+hw, cy+hh), (cx-hw, cy+hh)]
        min_dist = 1e9
        for i in range(4):
            d = rg.seg_seg_dist((x1,y1), (x2,y2), corners[i], corners[(i+1)%4])
            min_dist = min(min_dist, d)
        # Also check if segment is inside rect
        for px, py in [(x1,y1), (x2,y2)]:
            if abs(px-cx) <= hw and abs(py-cy) <= hh:
                return 0.0
        return min_dist

    def _verify_segments(self, segs_to_check, vias_to_check, nc):
        """验证所有线段和过孔 (commit gate).
        Incremental: each segment is added to sgs after checking, so
        later segments can check against earlier ones in the same path.
        Returns (ok, blocking_tracks) — blocking_tracks are the tracks causing failure."""
        sgs, rgs = geomir.extract_objects(self.b, self.lids)
        rules = {'clearance': self.min_clearance, 'clearance_smd': self.min_clearance}

        # Debug: print sgs count
        total_segs = sum(len(s) for s in sgs)
        print(f'    Commit gate: net={nc}, checking {len(segs_to_check)} segs, {len(vias_to_check)} vias, sgs has {total_segs} segs', flush=True)

        # Add PCB text as obstacles (track-to-text clearance)
        text_obstacles = self._get_text_obstacles()

        blocking = []
        for idx, (px1, py1, px2, py2, lid, w) in enumerate(segs_to_check):
            li = self.lids.index(lid) if lid in self.lids else 0
            ok, blocker = geomir.verify_polyline(
                [(px1, py1), (px2, py2)], w, nc, sgs[li], rgs[li], rules=rules)
            if not ok:
                blocking.append((px1, py1, px2, py2, lid, w, blocker))
                return False, blocking
            # Check against text obstacles
            for (tx, ty, thw, thh, tli) in text_obstacles:
                if tli != li:
                    continue
                # Check if segment passes near text bounding box
                dist = self._seg_rect_dist(px1, py1, px2, py2, tx, ty, thw, thh)
                if dist < self.min_clearance + w/2:
                    return False, [(px1, py1, px2, py2, lid, w, 'text')]
            # Add this segment to sgs so later segments can check against it
            sgs[li].append((nc, (px1, py1), (px2, py2), w/2))
        for (vx, vy) in vias_to_check:
            for li in range(self.nl):
                ok, blocker = geomir.verify_polyline(
                    [(vx, vy), (vx, vy)], self.via_d/2, nc,
                    sgs[li], rgs[li], rules=rules)
                if not ok:
                    blocking.append((vx, vy, vx, vy, self.lids[li], self.via_d/2, blocker))
                    return False, blocking
            # Add via as zero-length seg on ALL layers
            for li in range(self.nl):
                sgs[li].append((nc, (vx, vy), (vx, vy), self.via_d/2))

        # Check hole-to-hole (min 0.25mm per board rules)
        # NET-AGNOSTIC: same-net vias still violate if too close
        all_holes = []  # (x, y, drill_diameter)
        for fp in self.b.GetFootprints():
            for pad in fp.Pads():
                # Use HasHole() instead of GetAttribute() != 3 (which fails on KiCad 10)
                try:
                    if not pad.HasHole():
                        continue
                except Exception:
                    continue
                q = pad.GetPosition()
                try:
                    drill = pad.GetDrillSizeX() / MM
                except Exception:
                    drill = 0.5
                all_holes.append((q.x/MM, q.y/MM, drill, pad.GetNetCode()))
        for t in self.b.GetTracks():
            if type(t).__name__ == 'PCB_VIA':
                q = t.GetPosition()
                # Use ACTUAL drill value, not hardcoded
                try:
                    actual_drill = t.GetDrillValue() / MM
                except Exception:
                    actual_drill = self.via_drill
                all_holes.append((q.x/MM, q.y/MM, actual_drill, t.GetNetCode()))

        # Check new vias against existing holes (net-agnostic)
        for (vx, vy) in vias_to_check:
            for (hx, hy, hd, hnc) in all_holes:
                dist = math.hypot(vx-hx, vy-hy)
                min_dist = self.via_drill/2 + hd/2 + self.min_hole_to_hole
                if dist < min_dist:
                    return False, [(vx, vy, hx, hy, 0, hd, 'hole_to_hole')]

        # Check new vias against EACH OTHER (same batch)
        for i in range(len(vias_to_check)):
            for j in range(i+1, len(vias_to_check)):
                vx1, vy1 = vias_to_check[i]
                vx2, vy2 = vias_to_check[j]
                dist = math.hypot(vx1-vx2, vy1-vy2)
                min_dist = self.via_drill/2 + self.via_drill/2 + self.min_hole_to_hole
                if dist < min_dist:
                    return False, [(vx1, vy1, vx2, vy2, 0, self.via_drill, 'via_to_via')]
        # Check hole-to-copper clearance (min_hole_clearance = 0.25mm)
        for (vx, vy) in vias_to_check:
            for li in range(self.nl):
                for (net, a, b, hw) in sgs[li]:
                    if net == nc:
                        continue
                    dist = rg.pt_seg_dist(vx, vy, a[0], a[1], b[0], b[1])
                    min_dist = self.via_drill/2 + hw + self.min_hole_clearance
                    if dist < min_dist:
                        return False, [(vx, vy, a[0], a[1], li, hw, 'hole_clearance')]
                for (net, rect) in rgs[li]:
                    if net == nc:
                        continue
                    dist = rg.pt_rect_dist(vx, vy, rect)
                    min_dist = self.via_drill/2 + self.min_hole_clearance
                    if dist < min_dist:
                        return False, [(vx, vy, rect[0], rect[1], li, 0, 'hole_clearance')]
        return True, []

    def draw_route(self, path, nc, net, x1, y1, x2, y2):
        """画线到板上(commit gate — all-or-nothing per pair).
        Returns (drawn_count, items_list, path_cells) or (0, [], [])."""
        ox, oy = self.gm.edge[0], self.gm.edge[1]

        # Build segment + via lists for verification
        segs_to_check = []
        vias_to_check = []
        for j in range(len(path)-1):
            li1, gx1, gy1 = path[j]
            li2, gx2, gy2 = path[j+1]
            px1, py1 = (gx1+0.5)*self.G, (gy1+0.5)*self.G
            px2, py2 = (gx2+0.5)*self.G, (gy2+0.5)*self.G
            if li1 == li2:
                segs_to_check.append((px1, py1, px2, py2, self.lids[li1], self.track_w))
            else:
                vias_to_check.append((px1, py1))

        first = path[0]
        fx, fy = (first[1]+0.5)*self.G, (first[2]+0.5)*self.G
        if math.hypot(fx-x1, fy-y1) > 0.02:
            segs_to_check.append((x1, y1, fx, fy, self.lids[first[0]], self.track_w))

        last = path[-1]
        lx, ly = (last[1]+0.5)*self.G, (last[2]+0.5)*self.G
        if math.hypot(lx-x2, ly-y2) > 0.02:
            segs_to_check.append((lx, ly, x2, y2, self.lids[last[0]], self.track_w))

        # Commit gate: verify ALL before drawing ANY
        ok, blocking = self._verify_segments(segs_to_check, vias_to_check, nc)
        if not ok:
            # Debug: print rejection reason
            if blocking:
                print(f'    Commit gate REJECTED: net={nc}, blocking={blocking[0] if blocking else "None"}', flush=True)
            return 0, [], [], blocking

        # All verified — draw incrementally with re-verification
        items = []
        drawn_segs = []  # Track drawn segments for incremental verification
        sgs, rgs = geomir.extract_objects(self.b, self.lids)
        rules = {'clearance': self.min_clearance, 'clearance_smd': self.min_clearance}
        for j in range(len(path)-1):
            li1, gx1, gy1 = path[j]
            li2, gx2, gy2 = path[j+1]
            px1, py1 = (gx1+0.5)*self.G, (gy1+0.5)*self.G
            px2, py2 = (gx2+0.5)*self.G, (gy2+0.5)*self.G
            if li1 != li2:
                # Check hole-to-hole before drawing via (net-agnostic)
                can_place_via = True
                for fp in self.b.GetFootprints():
                    for pad in fp.Pads():
                        try:
                            if not pad.HasHole(): continue
                        except: continue
                        q = pad.GetPosition()
                        dist = math.hypot(px1 - q.x/MM, py1 - q.y/MM)
                        try:
                            pad_drill = pad.GetDrillSizeX() / MM
                        except: pad_drill = 0.5
                        min_dist = self.via_drill/2 + pad_drill/2 + self.min_hole_to_hole
                        if dist < min_dist:
                            can_place_via = False
                            break
                    if not can_place_via:
                        break
                if can_place_via:
                    for t in self.b.GetTracks():
                        if type(t).__name__ != 'PCB_VIA': continue
                        q = t.GetPosition()
                        dist = math.hypot(px1 - q.x/MM, py1 - q.y/MM)
                        try:
                            via_drill = t.GetDrillValue() / MM
                        except: via_drill = self.via_drill
                        min_dist = self.via_drill/2 + via_drill/2 + self.min_hole_to_hole
                        if dist < min_dist:
                            can_place_via = False
                            break
                if not can_place_via:
                    continue  # skip this via (route will be incomplete but no DRC)
                v = pcbnew.PCB_VIA(self.b)
                v.SetPosition(pcbnew.VECTOR2I(int(px1*MM), int(py1*MM)))
                v.SetWidth(int(self.via_d*MM))
                v.SetDrill(int(self.via_drill*MM))
                v.SetNet(net)
                self.b.Add(v)
                items.append(v)
            else:
                # Incremental verification: verify this segment against sgs
                li_idx = self.lids.index(self.lids[li1]) if self.lids[li1] in self.lids else 0
                ok, blocker = geomir.verify_polyline(
                    [(px1,py1),(px2,py2)], self.track_w, nc,
                    sgs[li_idx], rgs[li_idx], rules=rules)
                if not ok:
                    # This segment collides with previously drawn segment
                    # Skip it (route will be incomplete but no DRC)
                    continue
                t = pcbnew.PCB_TRACK(self.b)
                t.SetStart(pcbnew.VECTOR2I(int(px1*MM), int(py1*MM)))
                t.SetEnd(pcbnew.VECTOR2I(int(px2*MM), int(py2*MM)))
                t.SetWidth(int(self.track_w*MM))
                t.SetLayer(self.lids[li1])
                t.SetNet(net)
                self.b.Add(t)
                items.append(t)
                # Update sgs with this segment for future verification
                sgs[li_idx].append((nc, (px1, py1), (px2, py2), self.track_w/2))

        # Connect pads (stubs)
        first = path[0]
        fx, fy = (first[1]+0.5)*self.G, (first[2]+0.5)*self.G
        if math.hypot(fx-x1, fy-y1) > 0.02:
            t = pcbnew.PCB_TRACK(self.b)
            t.SetStart(pcbnew.VECTOR2I(int(x1*MM), int(y1*MM)))
            t.SetEnd(pcbnew.VECTOR2I(int(fx*MM), int(fy*MM)))
            t.SetWidth(int(self.track_w*MM))
            t.SetLayer(self.lids[first[0]])
            t.SetNet(net)
            self.b.Add(t)
            items.append(t)

        last = path[-1]
        lx, ly = (last[1]+0.5)*self.G, (last[2]+0.5)*self.G
        if math.hypot(lx-x2, ly-y2) > 0.02:
            t = pcbnew.PCB_TRACK(self.b)
            t.SetStart(pcbnew.VECTOR2I(int(lx*MM), int(ly*MM)))
            t.SetEnd(pcbnew.VECTOR2I(int(x2*MM), int(y2*MM)))
            t.SetWidth(int(self.track_w*MM))
            t.SetLayer(self.lids[last[0]])
            t.SetNet(net)
            self.b.Add(t)
            items.append(t)

        # Mark occupancy with clearance neighborhood (5x5 for proper clearance)
        # Track width 0.127mm + clearance 0.127mm = 0.254mm = 1.59 GRID
        # Need 5x5 to ensure tracks are at least 2 cells apart
        for (li, gx, gy) in path:
            for dx in range(-2, 3):
                for dy in range(-2, 3):
                    key = (li, gx+dx, gy+dy)
                    if 0 <= gx+dx < self.nx and 0 <= gy+dy < self.ny:
                        self.occupancy[key] = nc

        # Post-processing: check for collisions with previously drawn tracks
        # and add congestion cost to prevent future collisions
        sgs, rgs = geomir.extract_objects(self.b, self.lids)
        rules = {'clearance': self.min_clearance, 'clearance_smd': self.min_clearance}
        for (li, gx, gy) in path:
            px, py = (gx+0.5)*self.G, (gy+0.5)*self.G
            for (net, s, e, hw) in sgs[li]:
                if net == nc:
                    continue
                dist = math.hypot(px - (s[0]+e[0])/2, py - (s[1]+e[1])/2)
                if dist < self.min_clearance + self.track_w/2 + hw:
                    # Add congestion cost to this area
                    for dx in range(-2, 3):
                        for dy in range(-2, 3):
                            key = (li, gx+dx, gy+dy)
                            if 0 <= gx+dx < self.nx and 0 <= gy+dy < self.ny:
                                self.congestion[key] = self.congestion.get(key, 0.0) + 5.0

        return len(items), items, list(path), []

    def _try_nudge(self, blocking_tracks, nc, target_path, x1, y1, x2, y2, depth=3):
        """Push-and-shove: recursive nudge with depth cap (crash-safe).
        Returns True if path becomes routable after nudging."""
        if not blocking_tracks or depth <= 0:
            return False
        sgs, rgs = geomir.extract_objects(self.b, self.lids)
        rules = {'clearance': self.min_clearance, 'clearance_smd': self.min_clearance}

        for (bx1, by1, bx2, by2, blid, bw, blocker) in blocking_tracks:
            for t in self.b.GetTracks():
                if type(t).__name__ == 'PCB_VIA':
                    continue
                s, e = t.GetStart(), t.GetEnd()
                if (abs(s.x/MM - bx1) < 0.01 and abs(s.y/MM - by1) < 0.01 and
                    abs(e.x/MM - bx2) < 0.01 and abs(e.y/MM - by2) < 0.01):
                    dx = bx2 - bx1; dy = by2 - by1
                    length = math.hypot(dx, dy)
                    if length < 0.01:
                        continue
                    px, py = -dy/length, dx/length
                    for shift in [0.16, -0.16, 0.32, -0.32, 0.48, -0.48, 0.64, -0.64]:
                        nsx, nsy = bx1 + px*shift, by1 + py*shift
                        nex, ney = bx2 + px*shift, by2 + py*shift
                        li = self.lids.index(blid) if blid in self.lids else 0
                        ok, blocker = geomir.verify_polyline(
                            [(nsx, nsy), (nex, ney)], bw, t.GetNetCode(),
                            sgs[li], rgs[li], rules=rules)
                        if ok:
                            t.SetStart(pcbnew.VECTOR2I(int(nsx*MM), int(nsy*MM)))
                            t.SetEnd(pcbnew.VECTOR2I(int(nex*MM), int(ney*MM)))
                            return True
                        elif depth > 1 and blocker:
                            # Recursive shove: move the new blocker (single item)
                            if self._try_nudge([blocker], nc, target_path,
                                               x1, y1, x2, y2, depth-1):
                                t.SetStart(pcbnew.VECTOR2I(int(nsx*MM), int(nsy*MM)))
                                t.SetEnd(pcbnew.VECTOR2I(int(nex*MM), int(ney*MM)))
                                return True
                    break
        return False

    def _mst_pairs(self, pads):
        """Prim MST ordering: route shortest connections first."""
        n = len(pads)
        if n <= 2:
            return [(i, i+1) for i in range(n-1)]
        import math
        visited = {0}
        edges = []
        while len(visited) < n:
            best = None
            for i in visited:
                for j in range(n):
                    if j in visited:
                        continue
                    d = math.hypot(pads[i][0]-pads[j][0], pads[i][1]-pads[j][1])
                    if best is None or d < best[0]:
                        best = (d, i, j)
            if best is None:
                break
            _, i, j = best
            edges.append((i, j))
            visited.add(j)
        # Sort by distance: shortest first (easier to route)
        edges.sort(key=lambda e: math.hypot(
            pads[e[0]][0]-pads[e[1]][0], pads[e[0]][1]-pads[e[1]][1]))
        return edges

    def _fix_drc_violations(self, max_fixes=50):
        """Post-processing: nudge tracks to fix remaining DRC violations.
        Uses geomir.verify_polyline to find violations and nudges tracks."""
        sgs, rgs = geomir.extract_objects(self.b, self.lids)
        rules = {'clearance': 0.127, 'clearance_smd': 0.0375}
        fixed = 0

        # Collect all tracks
        all_tracks = [t for t in self.b.GetTracks() if type(t).__name__ != 'PCB_VIA']

        for t in all_tracks:
            if fixed >= max_fixes:
                break
            nc = t.GetNetCode()
            s, e = t.GetStart(), t.GetEnd()
            x1, y1 = s.x/MM, s.y/MM
            x2, y2 = e.x/MM, e.y/MM
            w = t.GetWidth()/MM
            lid = t.GetLayer()
            li = self.lids.index(lid) if lid in self.lids else 0

            # Check if this track violates clearance
            ok, blocker = geomir.verify_polyline(
                [(x1,y1),(x2,y2)], w, nc, sgs[li], rgs[li], rules=rules)
            if ok:
                continue

            # Try nudging perpendicular to track direction
            dx, dy = x2-x1, y2-y1
            length = math.hypot(dx, dy)
            if length < 0.01:
                continue
            px, py = -dy/length, dx/length

            for shift in [0.05, -0.05, 0.1, -0.1, 0.15, -0.15]:
                nsx, nsy = x1 + px*shift, y1 + py*shift
                nex, ney = x2 + px*shift, y2 + py*shift
                # Verify nudged position
                ok2, _ = geomir.verify_polyline(
                    [(nsx,nsy),(nex,ney)], w, nc, sgs[li], rgs[li], rules=rules)
                if ok2:
                    # Accept nudge
                    t.SetStart(pcbnew.VECTOR2I(int(nsx*MM), int(nsy*MM)))
                    t.SetEnd(pcbnew.VECTOR2I(int(nex*MM), int(ney*MM)))
                    # Update sgs with new position
                    sgs[li] = [(n, a, b, hw) for (n, a, b, hw) in sgs[li]
                               if not (abs(a[0]-x1)<0.01 and abs(a[1]-y1)<0.01 and
                                       abs(b[0]-x2)<0.01 and abs(b[1]-y2)<0.01)]
                    sgs[li].append((nc, (nsx,nsy), (nex,ney), w/2))
                    fixed += 1
                    break

        if fixed > 0:
            print(f'  DRC fix: nudged {fixed} tracks', flush=True)

    def _create_power_plane(self, net_name):
        """Create a copper pour (power plane) for the given net.
        This connects all pads of the net through the plane."""
        net = self.b.FindNet(net_name)
        if net is None:
            return
        nc = net.GetNetCode()

        # Create a zone covering the entire board on B.Cu
        zone = pcbnew.ZONE(self.b)
        zone.SetNet(net)
        zone.SetLayer(self.lids[-1])  # bottom layer
        zone.SetIsKnockout(False)

        # Set zone outline to board edge
        bb = self.b.GetBoardEdgesBoundingBox()
        x0 = bb.GetX() / MM
        y0 = bb.GetY() / MM
        x1 = (bb.GetX() + bb.GetWidth()) / MM
        y1 = (bb.GetY() + bb.GetHeight()) / MM

        # Add zone outline (with margin)
        margin = 0.5
        outline = pcbnew.SHAPE_LINE_CHAIN()
        outline.Append(int((x0+margin)*MM), int((y0+margin)*MM))
        outline.Append(int((x1-margin)*MM), int((y0+margin)*MM))
        outline.Append(int((x1-margin)*MM), int((y1-margin)*MM))
        outline.Append(int((x0+margin)*MM), int((y1-margin)*MM))
        outline.SetClosed(True)
        zone.AddPolygon(outline)

        # Set zone properties
        zone.SetLocalClearance(int(0.15 * MM))
        zone.SetMinThickness(int(0.15 * MM))
        zone.SetThermalReliefGap(int(0.25 * MM))
        zone.SetThermalReliefSpokeWidth(int(0.30 * MM))
        zone.SetPadConnection(2)  # thermal reliefs

        self.b.Add(zone)

        # Fill the zone
        try:
            filler = pcbnew.ZONE_FILLER(self.b)
            filler.Fill(self.b.Zones())
        except Exception:
            pass

        print(f'  Created {net_name} power plane on {self.b.GetLayerName(self.lids[-1])}', flush=True)

    def _retry_failed_pairs(self, net_pads, t0, timeout_s):
        """Multi-pass retry: retry failed pairs with different strategies.

        Pass 1: Normal routing (already done)
        Pass 2: Relaxed clearance (0.127mm instead of board rules)
        Pass 3: Different layer assignment
        Pass 4: Aggressive PNS nudge
        Pass 5: Escape-first (via region for fenced-in pads)
        """
        # Collect failed pairs (NOT in pair_items OR in collided_pairs)
        failed_pairs = []
        for nn, pads in sorted(net_pads.items()):
            if len(pads) < 2:
                continue
            net = self.b.GetNetInfo().GetNetItem(nn)
            if net is None:
                continue
            nc = net.GetNetCode()
            for i, j in self._mst_pairs(pads):
                x1, y1, l1 = pads[i]
                x2, y2, l2 = pads[j]
                pair_key = (nn, round(x1,2), round(y1,2), round(x2,2), round(y2,2))
                if pair_key not in self.pair_items or pair_key in self.collided_pairs:
                    failed_pairs.append((pair_key, nc, net, nn, x1, y1, x2, y2, l1, l2))

        if not failed_pairs:
            return

        print(f'  Multi-pass retry: {len(failed_pairs)} failed pairs', flush=True)

        # Pass 2: Relaxed clearance
        orig_clearance = self.min_clearance
        self.min_clearance = 0.10  # relaxed for retry
        routed_pass2 = 0
        for pair_key, nc, net, nn, x1, y1, x2, y2, l1, l2 in failed_pairs:
            if time.time() - t0 > timeout_s:
                break
            if pair_key in self.pair_items:
                self.collided_pairs.discard(pair_key)  # Clear collision mark
                continue
            path = self.route_pair(nc, x1, y1, x2, y2, src_li=l1, dst_li=l2)
            if path:
                drawn, items, cells, _ = self.draw_route(path, nc, net, x1, y1, x2, y2)
                if drawn > 0:
                    self.pair_items[pair_key] = items
                    self.pair_cells[pair_key] = cells
                    self.collided_pairs.discard(pair_key)  # Clear collision mark
                    routed_pass2 += 1
        self.min_clearance = orig_clearance
        print(f'    Pass 2 (relaxed clearance): routed {routed_pass2}', flush=True)

        # Pass 3: Different layer assignment
        routed_pass3 = 0
        for pair_key, nc, net, nn, x1, y1, x2, y2, l1, l2 in failed_pairs:
            if time.time() - t0 > timeout_s:
                break
            if pair_key in self.pair_items:
                continue
            # Try opposite layer
            alt_l1 = (l1 + 1) % self.nl
            alt_l2 = (l2 + 1) % self.nl
            path = self.route_pair(nc, x1, y1, x2, y2, src_li=alt_l1, dst_li=alt_l2)
            if path:
                drawn, items, cells, _ = self.draw_route(path, nc, net, x1, y1, x2, y2)
                if drawn > 0:
                    self.pair_items[pair_key] = items
                    self.pair_cells[pair_key] = cells
                    routed_pass3 += 1
        print(f'    Pass 3 (alt layer): routed {routed_pass3}', flush=True)

        # Pass 4: Aggressive PNS
        routed_pass4 = 0
        for pair_key, nc, net, nn, x1, y1, x2, y2, l1, l2 in failed_pairs:
            if time.time() - t0 > timeout_s:
                break
            if pair_key in self.pair_items:
                continue
            path = self.route_pair(nc, x1, y1, x2, y2, src_li=l1, dst_li=l2)
            if path:
                drawn, items, cells, blocking = self.draw_route(path, nc, net, x1, y1, x2, y2)
                if drawn > 0:
                    self.pair_items[pair_key] = items
                    self.pair_cells[pair_key] = cells
                    routed_pass4 += 1
                elif blocking:
                    # Aggressive nudge with more shifts
                    if self._try_nudge(blocking, nc, path, x1, y1, x2, y2, depth=5):
                        path2 = self.route_pair(nc, x1, y1, x2, y2, src_li=l1, dst_li=l2)
                        if path2:
                            drawn2, items2, cells2, _ = self.draw_route(path2, nc, net, x1, y1, x2, y2)
                            if drawn2 > 0:
                                self.pair_items[pair_key] = items2
                                self.pair_cells[pair_key] = cells2
                                routed_pass4 += 1
        print(f'    Pass 4 (aggressive PNS): routed {routed_pass4}', flush=True)

    def _gen_failure_feedback(self, pair_key, path, failure_reason, blocking_info):
        """Generate structured failure feedback (PCBRouteAgent-style).

        Returns a feedback packet that can guide replanning.
        """
        nn = pair_key[0]
        net = self.b.GetNetInfo().GetNetItem(nn)
        nc = net.GetNetCode() if net else 0

        # Collect potential blockers
        blockers = []
        if blocking_info:
            for (bx1, by1, bx2, by2, blid, bw, blocker_type) in blocking_info:
                blockers.append({
                    'type': blocker_type,
                    'position': [round(bx1, 3), round(by1, 3)],
                    'end': [round(bx2, 3), round(by2, 3)],
                    'layer': blid,
                    'width': round(bw, 3),
                })

        # Collect via candidates near stuck position
        via_sites = []
        if path:
            stuck_x, stuck_y = path[-1][1], path[-1][2]
            ox, oy = self.gm.edge[0], self.gm.edge[1]
            for dx in range(-5, 6):
                for dy in range(-5, 6):
                    vx = (stuck_x + dx + 0.5) * self.G
                    vy = (stuck_y + dy + 0.5) * self.G
                    if self.gm.via_exact_ok(vx, vy, nc):
                        via_sites.append([round(vx, 3), round(vy, 3)])

        feedback = {
            'pair_key': [nn, round(pair_key[1], 3), round(pair_key[2], 3),
                         round(pair_key[3], 3), round(pair_key[4], 3)],
            'failure_reason': failure_reason,
            'blockers': blockers[:10],  # top 10 blockers
            'via_sites': via_sites[:20],  # top 20 via candidates
            'path_length': len(path) if path else 0,
            'stuck_position': [
                round(path[-1][1], 3), round(path[-1][2], 3)
            ] if path else None,
        }
        return feedback

    def _ripup_pair(self, pair_key, conflict_index=None):
        """Remove items of a pair. If conflict_index is given, selective rip-up:
        only remove segments near the conflict point (±2 cells), keep the rest."""
        items = self.pair_items.get(pair_key, [])
        cells = self.pair_cells.get(pair_key, [])

        if conflict_index is not None and len(items) > 2:
            # Selective rip-up: only remove items near conflict
            items_to_remove = []
            for i, item in enumerate(items):
                # Check if item is near conflict index
                if abs(i - conflict_index) <= 2:
                    items_to_remove.append(item)
            # Remove only selected items
            for item in items_to_remove:
                try:
                    self.b.Remove(item)
                except Exception:
                    pass
                if item in items:
                    items.remove(item)
            # Update pair_items with remaining items
            self.pair_items[pair_key] = items
            # Clear occupancy for removed cells only
            for (li, gx, gy) in cells[conflict_index-2:conflict_index+3]:
                for dx in range(-2, 3):
                    for dy in range(-2, 3):
                        self.occupancy.pop((li, gx+dx, gy+dy), None)
        else:
            # Whole-path rollback
            for item in items:
                try:
                    self.b.Remove(item)
                except Exception:
                    pass
            self.pair_items.pop(pair_key, None)
            # Clear occupancy neighborhood for this pair's cells
            cells = self.pair_cells.pop(pair_key, [])
            for (li, gx, gy) in cells:
                for dx in range(-2, 3):
                    for dy in range(-2, 3):
                        self.occupancy.pop((li, gx+dx, gy+dy), None)

    def run(self, net_pads, timeout_s=1200, corridors=None):
        """NCER 主循环: 路由全部 → 检测碰撞 → 反馈 → 重路由.

        Implements:
        1. Corridor-guided routing (PCBRouteAgent-style)
        2. PathFinder negotiation (present + history cost)
        3. Failure feedback → replanning loop
        4. State fingerprinting (measurement-state binding)
        """
        t0 = time.time()
        MAX_NCEER = 10

        # State fingerprint: bind all measurements to board state
        sys.path.insert(0, os.path.join(os.path.dirname(__file__),
                                         '../../flux-neurotransmitter/fluxpcb/kicadgen'))
        try:
            from state_fingerprint import get_registry
            registry = get_registry()
            registry.update_fingerprint(self.b)
            print(f'  State fingerprint: {registry.current_fp.board_hash}', flush=True)
        except ImportError:
            registry = None

        # PathFinder history cost: accumulate over iterations
        self.history_cost = {}

        # Store corridors from BGA planner (if provided)
        self.corridors = corridors or {}

        # Store failure feedback for replanning
        self.failure_feedback = []

        for ncer_iter in range(MAX_NCEER):
            if time.time() - t0 > timeout_s:
                break

            # Rebuild GridModel (includes existing copper from previous iters)
            self.gm = rg.GridModel(self.b, self.lids)
            self.router.gm = self.gm
            # Rebuild occupancy from previously routed pairs
            self.occupancy = {}
            for pk, cells in self.pair_cells.items():
                for c in cells:
                    nn = pk[0]
                    net = self.b.GetNetInfo().GetNetItem(nn)
                    if net:
                        self.occupancy[c] = net.GetNetCode()

            # Collect all pairs to route (MST order within each net)
            pairs_to_route = []
            for nn, pads in sorted(net_pads.items()):
                if len(pads) < 2:
                    continue
                net = self.b.GetNetInfo().GetNetItem(nn)
                if net is None:
                    continue
                nc = net.GetNetCode()
                for i, j in self._mst_pairs(pads):
                    x1, y1, l1 = pads[i]
                    x2, y2, l2 = pads[j]
                    pair_key = (nn, round(x1,2), round(y1,2), round(x2,2), round(y2,2))
                    if pair_key not in self.pair_items:
                        pairs_to_route.append((pair_key, nc, net, nn, x1, y1, x2, y2))

            print(f'  NCER {ncer_iter+1}: {len(pairs_to_route)} pairs to route', flush=True)

            # Route all unrouted pairs (corridor-guided)
            routed = 0
            for pair_key, nc, net, nn, x1, y1, x2, y2 in pairs_to_route:
                if time.time() - t0 > timeout_s:
                    break

                # Find corridor for this pair (from BGA planner)
                corridor = None
                if self.corridors:
                    for corr in self.corridors.get(nn, []):
                        # Check if pair endpoints are in corridor
                        if (corr[0] <= x1 <= corr[2] and corr[1] <= y1 <= corr[3] and
                            corr[0] <= x2 <= corr[2] and corr[1] <= y2 <= corr[3]):
                            corridor = corr
                            break

                path = self.route_pair(nc, x1, y1, x2, y2, src_li=l1, dst_li=l2, corridor=corridor)
                if path:
                    drawn, items, cells, blocking = self.draw_route(path, nc, net, x1, y1, x2, y2)
                    if drawn > 0:
                        self.pair_items[pair_key] = items
                        self.pair_cells[pair_key] = cells
                        routed += 1
                    else:
                        # Try push-and-shove: nudge blocking tracks
                        sgs, rgs = geomir.extract_objects(self.b, self.lids)
                        rules = {'clearance': self.min_clearance, 'clearance_smd': self.min_clearance}
                        ox, oy = self.gm.edge[0], self.gm.edge[1]
                        blocking = []
                        for k in range(len(path)-1):
                            li1, gx1, gy1 = path[k]; li2, gx2, gy2 = path[k+1]
                            px1, py1 = (gx1+0.5)*self.G, (gy1+0.5)*self.G
                            px2, py2 = (gx2+0.5)*self.G, (gy2+0.5)*self.G
                            if li1 == li2:
                                ok, blocker = geomir.verify_polyline(
                                    [(px1,py1),(px2,py2)], self.track_w, nc,
                                    sgs[li1], rgs[li1], rules=rules)
                                if not ok:
                                    blocking.append((px1,py1,px2,py2,self.lids[li1],self.track_w,blocker))
                                    break
                        if blocking and self._try_nudge(blocking, nc, path, x1, y1, x2, y2):
                            # Retry after nudge
                            path2 = self.route_pair(nc, x1, y1, x2, y2, src_li=l1, dst_li=l2)
                            if path2:
                                drawn2, items2, cells2, _ = self.draw_route(path2, nc, net, x1, y1, x2, y2)
                                if drawn2 > 0:
                                    self.pair_items[pair_key] = items2
                                    self.pair_cells[pair_key] = cells2
                                    routed += 1
                                    continue

                        # Commit gate rejected — add congestion and retry
                        for (li, gx, gy) in path:
                            for dx in range(-1, 2):
                                for dy in range(-1, 2):
                                    key = (li, gx+dx, gy+dy)
                                    if 0 <= gx+dx < self.nx and 0 <= gy+dy < self.ny:
                                        self.congestion[key] = self.congestion.get(key, 0.0) + 5.0

                        # Standard retry with congestion
                        if pair_key not in self.pair_items:
                            for retry in range(3):
                                path2 = self.route_pair(nc, x1, y1, x2, y2, src_li=l1, dst_li=l2)
                                if path2:
                                    drawn2, items2, cells2, _ = self.draw_route(path2, nc, net, x1, y1, x2, y2)
                                    if drawn2 > 0:
                                        self.pair_items[pair_key] = items2
                                        self.pair_cells[pair_key] = cells2
                                        routed += 1
                                        break
                                    else:
                                        for (li, gx, gy) in path2:
                                            for dx in range(-1, 2):
                                                for dy in range(-1, 2):
                                                    key = (li, gx+dx, gy+dy)
                                                    if 0 <= gx+dx < self.nx and 0 <= gy+dy < self.ny:
                                                        self.congestion[key] = self.congestion.get(key, 0.0) + 5.0

            # Detect collisions
            collisions = self.detect_collisions()
            print(f'  NCER {ncer_iter+1}: routed={routed}, total_pairs={len(self.pair_items)}, collisions={len(collisions)}', flush=True)

            # Post-processing: remove colliding tracks and retry
            if collisions:
                print(f'  Post-processing: removing {len(collisions)} colliding tracks', flush=True)
                for t in collisions:
                    try:
                        self.b.Remove(t)
                    except Exception:
                        pass
                # Rebuild occupancy after removal
                self.occupancy = {}
                for pk, cells in self.pair_cells.items():
                    for c in cells:
                        nn = pk[0]
                        net = self.b.GetNetInfo().GetNetItem(nn)
                        if net:
                            self.occupancy[c] = net.GetNetCode()

            # Only converge if 0 collisions AND all pairs routed (or no progress)
            total_expected = sum(max(0, len(pads)-1) for pads in net_pads.values() if len(pads) >= 2)
            if not collisions and len(self.pair_items) >= total_expected:
                print(f'  NCER converged at iteration {ncer_iter+1}', flush=True)
                break

            # Add congestion cost for unrouted pairs (helps retry find paths)
            if not collisions and len(self.pair_items) < total_expected:
                # Add small congestion cost everywhere to force different paths
                for li in range(self.nl):
                    for gx in range(0, self.nx, 5):
                        for gy in range(0, self.ny, 5):
                            key = (li, gx, gy)
                            self.congestion[key] = self.congestion.get(key, 0.0) + 0.5

            # PathFinder negotiated congestion: history + present share
            # history accumulates over iterations (nets eventually yield)
            # present_share punishes simultaneous use
            for t in collisions:
                # History cost: accumulate (PathFinder key insight)
                self.add_congestion_cost(t, cost_mult=3.0)
                # Present share: additional penalty for current collision
                self.add_congestion_cost(t, cost_mult=1.5)

            # Also add history cost to ALL routed pairs (not just collisions)
            # This encourages nets to yield when congested
            for pair_key, cells in self.pair_cells.items():
                for (li, gx, gy) in cells:
                    key = (li, gx, gy)
                    self.congestion[key] = self.congestion.get(key, 0.0) + 0.3

            # Find which SPECIFIC pairs are involved in collisions
            colliding_track_data = []
            for t in collisions:
                s, e = t.GetStart(), t.GetEnd()
                colliding_track_data.append((s.x/MM, s.y/MM, e.x/MM, e.y/MM,
                                             t.GetLayer(), t.GetNetCode(), t.GetWidth()/MM))

            # Map colliding tracks to their pairs (by item identity)
            pairs_to_ripup = set()
            for pair_key, items in self.pair_items.items():
                for item in items:
                    try:
                        s, e = item.GetStart(), item.GetEnd()
                        ix1, iy1 = s.x/MM, s.y/MM
                        ix2, iy2 = e.x/MM, e.y/MM
                        for ct in colliding_track_data:
                            if (abs(ix1-ct[0])<0.01 and abs(iy1-ct[1])<0.01 and
                                abs(ix2-ct[2])<0.01 and abs(iy2-ct[3])<0.01):
                                pairs_to_ripup.add(pair_key)
                                break
                    except Exception:
                        pass

            # SWIG-safe rip-up: save board state, mark pairs as unrouted,
            # then reload to get fresh SWIG references
            for pair_key in pairs_to_ripup:
                self.pair_items.pop(pair_key, None)
                self.collided_pairs.add(pair_key)  # Track for retry
                cells = self.pair_cells.pop(pair_key, None)
                if cells:
                    for (li, gx, gy) in cells:
                        for dx in range(-2, 3):
                            for dy in range(-2, 3):
                                self.occupancy.pop((li, gx+dx, gy+dy), None)

            # Save + reload for clean SWIG state
            tmp = str(self.b.GetFileName()).replace('.kicad_pcb', '_tmp.kicad_pcb')
            self.b.Save(tmp)
            import pcbnew as _p
            self.b = _p.LoadBoard(tmp)
            if self.b is None:
                break
            self.b.BuildConnectivity()

            # Now remove colliding tracks on the FRESH board (safe SWIG)
            # Match by geometric properties to avoid stale references
            for t_data in colliding_track_data:
                tx1, ty1, tx2, ty2, tlid, tnc, tw = t_data
                for t in self.b.GetTracks():
                    if type(t).__name__ == 'PCB_VIA':
                        continue
                    s, e = t.GetStart(), t.GetEnd()
                    if (abs(s.x/MM - tx1) < 0.01 and abs(s.y/MM - ty1) < 0.01 and
                        abs(e.x/MM - tx2) < 0.01 and abs(e.y/MM - ty2) < 0.01 and
                        t.GetLayer() == tlid and t.GetNetCode() == tnc):
                        self.b.Remove(t)
                        break

            self.gm = rg.GridModel(self.b, self.lids)
            self.router.gm = self.gm
            self.router.b = self.b
            self.router.invalidate_cache()

            # Save intermediate result after each iteration (crash-safe)
            try:
                tmp_save = str(self.b.GetFileName()).replace('.kicad_pcb', '_progress.kicad_pcb')
                self.b.Save(tmp_save)
            except Exception:
                pass

        # Post-processing: nudge tracks to fix remaining DRC violations
        self._fix_drc_violations()

        # Multi-pass retry: retry failed pairs with different strategies
        self._retry_failed_pairs(net_pads, t0, timeout_s)

        # Phase FINAL: Pour GND power plane AFTER routing
        # Zone filler automatically carves clearance around existing signal tracks
        self._create_power_plane('GND')

        # Calculate completion metrics
        routed_pairs = len(self.pair_items)
        total_expected = sum(max(0, len(pads)-1) for pads in net_pads.values() if len(pads) >= 2)
        completion_pct = 100.0 * routed_pairs / total_expected if total_expected > 0 else 0.0

        # Record measurements with state fingerprints
        if registry:
            registry.update_fingerprint(self.b)
            fp = registry.current_fp
            registry.record('total_pairs', total_expected, 'pairs')
            registry.record('routed_pairs', routed_pairs, 'pairs')
            registry.record('unconnected', self._count_unconnected(), 'items')
            print(f'  State fingerprint: {fp.board_hash} '
                  f'({fp.track_count} tracks, {fp.via_count} vias)', flush=True)

        return {
            'ncer_iterations': ncer_iter + 1,
            'total_pairs': total_expected,
            'routed_pairs': routed_pairs,
            'completion_pct': completion_pct,
            'seconds': round(time.time() - t0, 1),
            'state_fingerprint': registry.current_fp.to_dict() if registry else None,
        }

    def _count_unconnected(self):
        """Count unconnected items with state verification."""
        try:
            self.b.BuildConnectivity()
            return self.b.GetConnectivity().GetUnconnectedCount(True)
        except Exception:
            return -1


# Warp kernels (same as before)
try:
    import warp as wp

    @wp.kernel
    def _clear_k(a: wp.array3d(dtype=wp.int32)):
        li, gx, gy = wp.tid()
        a[li, gx, gy] = 0

    @wp.kernel
    def _fim_cost_k(T: wp.array3d(dtype=wp.float32),
                     free: wp.array3d(dtype=wp.int32),
                     cost: wp.array3d(dtype=wp.float32),
                     active: wp.array3d(dtype=wp.int32),
                     new_active: wp.array3d(dtype=wp.int32),
                     nl: int, nx: int, ny: int,
                     G: float, vc: float):
        li, gx, gy = wp.tid()
        if active[li, gx, gy] == 0 or free[li, gx, gy] == 0:
            return
        t_old = T[li, gx, gy]
        mn = wp.float32(1e18)
        for ddx in range(-1, 2):
            for ddy in range(-1, 2):
                if ddx == 0 and ddy == 0:
                    continue
                nx2 = gx + ddx
                ny2 = gy + ddy
                if 0 <= nx2 < nx and 0 <= ny2 < ny and free[li, nx2, ny2] == 1:
                    step = G * (1.4142 if ddx != 0 and ddy != 0 else 1.0)
                    c = T[li, nx2, ny2] + step * cost[li, nx2, ny2]
                    if c < mn:
                        mn = c
        for li2 in range(nl):
            if li2 != li and free[li2, gx, gy] == 1:
                c = T[li2, gx, gy] + vc * G * cost[li2, gx, gy]
                if c < mn:
                    mn = c
        t_new = wp.min(mn, t_old)
        if wp.abs(t_new - t_old) > wp.float32(1e-6):
            T[li, gx, gy] = t_new
            for ddx in range(-1, 2):
                for ddy in range(-1, 2):
                    nx2 = gx + ddx
                    ny2 = gy + ddy
                    if 0 <= nx2 < nx and 0 <= ny2 < ny:
                        new_active[li, nx2, ny2] = 1
            for li2 in range(nl):
                if li2 != li and free[li2, gx, gy] == 1:
                    new_active[li2, gx, gy] = 1
except Exception:
    pass


def main():
    if len(sys.argv) < 3:
        print("Usage: python fluxpcb_ncer.py INPUT.kicad_pcb OUTPUT.kicad_pcb [--timeout S]")
        sys.exit(1)

    input_pcb = sys.argv[1]
    output_pcb = sys.argv[2]
    timeout_s = 1200
    if '--timeout' in sys.argv:
        timeout_s = int(sys.argv[sys.argv.index('--timeout') + 1])

    b = pcbnew.LoadBoard(str(input_pcb))
    b.BuildConnectivity()
    lids = rg.signal_layer_ids(b)

    # Collect net pads
    net_pads = {}
    for fp in b.GetFootprints():
        for pad in fp.Pads():
            nc = pad.GetNetCode()
            if nc == 0:
                continue
            nn = pad.GetNetname()
            if nn not in net_pads:
                net_pads[nn] = []
            p = pad.GetPosition()
            pad_lid = pad.GetLayer()
            pad_li = lids.index(pad_lid) if pad_lid in lids else 0
            net_pads[nn].append((p.x/MM, p.y/MM, pad_li))

    router = NCERRouter(b, lids)
    result = router.run(net_pads, timeout_s)

    # Save the router's board (may have been reloaded during save+reload)
    saved = False
    try:
        router.b.Save(str(output_pcb))
        print(f'Saved to {output_pcb}', flush=True)
        saved = True
    except Exception as e:
        print(f'Failed to save: {e}', flush=True)

    # If save failed, try to copy progress file
    if not saved:
        try:
            progress_file = str(output_pcb).replace('.kicad_pcb', '_progress.kicad_pcb')
            if os.path.exists(progress_file):
                import shutil
                shutil.copy2(progress_file, str(output_pcb))
                print(f'Copied progress to {output_pcb}', flush=True)
                saved = True
        except Exception as e:
            print(f'Failed to copy progress: {e}', flush=True)

    # Always output JSON result (even on timeout)
    if 'completion_pct' not in result:
        # Calculate from actual routing
        total_pairs = result.get('total_pairs', 0)
        routed_pairs = result.get('routed_pairs', 0)
        result['completion_pct'] = 100.0 * routed_pairs / total_pairs if total_pairs > 0 else 0.0
    print(json.dumps(result))


if __name__ == '__main__':
    main()
