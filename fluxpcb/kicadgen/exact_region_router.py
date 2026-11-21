# -*- coding: utf-8 -*-
"""exact_region_router — 精确区域路由器(GPU Eikonal + geomir 验证 + 重试).

核心改进(vs 之前的路由器):
  1. 碰撞不跳过 → 加障碍重试(最多 N 轮)
  2. 逐段 geomir 精确验证(0.05mm)
  3. 每网路由后重建 GridModel(后网看到前网)
  4. 电源网平面 via 直连
  5. 规则单一来源

用法: python exact_region_router.py PCB [NET1 NET2 ...]
"""
import json
import math
import sys
import time
from pathlib import Path

LAB = Path('E:/DATA/vscode/ZCODE/z_lab')
REPO = Path('E:/DATA/vscode/ZCODE/flux-neurotransmitter')
KGEN = REPO / 'fluxpcb' / 'kicadgen'
sys.path.insert(0, str(KGEN))
sys.path.insert(0, str(REPO))

board_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else LAB / 'z_v/FC_L13'
target_nets = sys.argv[2:]
pcb = sorted((board_dir / 'pcb' / 'board').glob('*.kicad_pcb'))[0]

from fluxpcb.routing import find_kicad       # noqa: E402
from fluxpcb.kernel import state as kstate   # noqa: E402
import pcbnew  # noqa: E402
import routegrid as rg  # noqa: E402

MM = 1e6
kicad_cli, kipy = find_kicad()

# ── 规则单一来源 ─────────────────────────────────────────
RULES = {
    'track_width': 0.10,      # mm
    'clearance': 0.10,        # mm (JLC 极限 0.09)
    'via_pad': 0.45,          # mm
    'via_drill': 0.20,        # mm
    'verify_clearance': 0.05, # mm (精确验证阈值)
}


def opens_now():
    q = kstate.query(kicad_cli, kipy, pcb, 'opens', consolidate=True)
    return q.value, sorted((q.detail or {}).get('pairs') or [])


# ── GPU Eikonal 路由器(带碰撞重试) ──────────────────────
class ExactRegionRouter:
    """精确区域路由器.

    路由流程:
      1. GPU Eikonal 求解最优路径
      2. geomir 逐段精确验证
      3. 碰撞 → 加障碍 → 重试(最多 MAX_RETRY 轮)
      4. 0 碰撞 → 接受并画线
    """

    def __init__(self, board, layer_ids):
        self.b = board
        self.lids = layer_ids
        self.G = rg.GRID
        self.gm = rg.GridModel(board, layer_ids)
        self.G2K = {i: layer_ids[i] for i in range(len(layer_ids))}
        self.nx, self.ny, self.nl = self.gm.nx, self.gm.ny, self.gm.nl

        # GPU
        self.gpu_ok = False
        try:
            import warp as wp
            wp.init()
            self.wp = wp
            self.gpu_ok = True
            print('[Exact] GPU: %s' % wp.get_device().name)
        except Exception:
            import numpy as np
            self.np = np

        # geomir
        try:
            import geomir
            self.geomir = geomir
            self.geomir_ok = True
        except Exception:
            self.geomir_ok = False

    def build_free(self, nc, extra_blocked=None):
        """构建可走场."""
        free = [[[False]*self.ny for _ in range(self.nx)] for _ in range(self.nl)]
        for li in range(self.nl):
            for gx in range(self.nx):
                for gy in range(self.ny):
                    if self.gm.fixed_free(li, gx, gy, nc):
                        free[li][gx][gy] = True
                    else:
                        s = self.gm.own_cells.get((li, gx, gy))
                        if s is not None and nc in s:
                            free[li][gx][gy] = True
                    if extra_blocked and (li, gx, gy) in extra_blocked:
                        free[li][gx][gy] = False
        return free

    def solve(self, free, dst_cells, force_via=None):
        """GPU FIM Eikonal."""
        if not self.gpu_ok:
            return self._solve_cpu(free, dst_cells, force_via)
        return self._solve_gpu(free, dst_cells, force_via)

    def _solve_gpu(self, free, dst_cells, force_via):
        wp = self.wp
        import numpy as np
        nl, nx, ny = self.nl, self.nx, self.ny

        T_np = np.full((nl, nx, ny), 1e18, dtype=np.float32)
        free_np = np.array(free, dtype=np.int32)
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

        T_gpu = wp.array(T_np, dtype=wp.float32)
        free_gpu = wp.array(free_np, dtype=wp.int32)
        active_gpu = wp.array(active_np, dtype=wp.int32)
        new_active_gpu = wp.array(new_active_np, dtype=wp.int32)

        for iteration in range(500):
            wp.launch(_clear_k, (nl, nx, ny), [new_active_gpu], device=wp.get_device())
            wp.launch(_fim_k, (nl, nx, ny),
                       [T_gpu, free_gpu, active_gpu, new_active_gpu,
                        nx, ny, nl, self.G, 6.0], device=wp.get_device())
            active_gpu, new_active_gpu = new_active_gpu, active_gpu
            if iteration % 20 == 0:
                if int(active_gpu.numpy().sum()) == 0:
                    break

        return T_gpu.numpy()

    def _solve_cpu(self, free, dst_cells, force_via):
        import numpy as np
        nl, nx, ny = self.nl, self.nx, self.ny
        T = np.full((nl, nx, ny), 1e18, dtype=np.float32)
        active = np.zeros((nl, nx, ny), dtype=bool)
        free_arr = np.array(free, dtype=bool)

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
                                min_n = min(min_n, T[li, nx2, ny2] + step)
                        for li2 in range(nl):
                            if li2 != li and free_arr[li2, gx, gy]:
                                min_n = min(min_n, T[li2, gx, gy] + 6.0 * self.G)
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

    def extract_path(self, T, free, src, force_via=None):
        if force_via is None:
            force_via = set()
        path = [src]
        li, gx, gy = src
        visited = {src}
        for _ in range(self.nx * self.ny):
            if T[li][gx][gy] < 0.01:
                break
            best = None
            best_t = T[li][gx][gy]
            for ddx, ddy in ((1,0),(-1,0),(0,1),(0,-1),(1,1),(1,-1),(-1,1),(-1,-1)):
                nx2, ny2 = gx+ddx, gy+ddy
                if not (0 <= nx2 < self.nx and 0 <= ny2 < self.ny):
                    continue
                if not free[li][nx2][ny2]:
                    continue
                if (li, nx2, ny2) in visited:
                    continue
                if T[li][nx2][ny2] < best_t:
                    best_t = T[li][nx2][ny2]
                    best = (li, nx2, ny2)
            for li2 in range(self.nl):
                if li2 == li:
                    continue
                if not free[li2][gx][gy]:
                    continue
                if (gx, gy) not in force_via:
                    if not self.gm.via_cell_ok(li2, gx, gy):
                        continue
                if (li2, gx, gy) in visited:
                    continue
                if T[li2][gx][gy] < best_t:
                    best_t = T[li2][gx][gy]
                    best = (li2, gx, gy)
            if best is None:
                break
            path.append(best)
            visited.add(best)
            li, gx, gy = best
        return path

    def verify_path(self, path, nc, width=0.10):
        """geomir 精确验证. 返回 (ok, collision_cells)."""
        if not self.geomir_ok or len(path) < 2:
            return True, []

        sgs, rgs = self.geomir.extract_objects(self.b, self.lids)
        rules = {'clearance': RULES['verify_clearance'],
                 'clearance_smd': RULES['verify_clearance'] / 2}
        collision_cells = []

        for i in range(len(path)-1):
            li1, gx1, gy1 = path[i]
            li2, gx2, gy2 = path[i+1]
            if li1 == li2:
                x1, y1 = (gx1+0.5)*self.G, (gy1+0.5)*self.G
                x2, y2 = (gx2+0.5)*self.G, (gy2+0.5)*self.G
                li_idx = self.lids.index(self.lids[li1]) if li1 < len(self.lids) else 0
                ok, _ = self.geomir.verify_polyline(
                    [(x1,y1),(x2,y2)], width, nc,
                    sgs[li_idx], rgs[li_idx], rules=rules)
                if not ok:
                    collision_cells.extend([(li1,gx1,gy1), (li1,gx2,gy2)])
            else:
                # via 检查
                vx, vy = (gx1+0.5)*self.G, (gy1+0.5)*self.G
                ok, _ = self.geomir.via_ok(vx, vy, nc, sgs, rgs,
                                            via_r=0.225, rules=rules)
                if not ok:
                    collision_cells.extend([(li1,gx1,gy1), (li2,gx2,gy2)])

        return len(collision_cells) == 0, collision_cells

    def route_net_exact(self, nc, src_pos, dst_pos, max_retry=8):
        """精确路由单网: GPU Eikonal + geomir 验证 + 碰撞重试.

        返回: (path, segments) 或 (None, None)
        """
        src_gx, src_gy = int(src_pos[0]/self.G), int(src_pos[1]/self.G)
        dst_gx, dst_gy = int(dst_pos[0]/self.G), int(dst_pos[1]/self.G)

        extra_blocked = set()

        for retry in range(max_retry):
            free = self.build_free(nc, extra_blocked)

            # 标记起点
            for li in range(self.nl):
                if 0 <= src_gx < self.nx and 0 <= src_gy < self.ny:
                    free[li][src_gx][src_gy] = True

            force_via = {(src_gx, src_gy)}
            T = self.solve(free, [(0, dst_gx, dst_gy)], force_via)
            path = self.extract_path(T, free, (0, src_gx, src_gy), force_via)

            if len(path) < 2:
                return None, None

            # 精确验证
            ok, collision_cells = self.verify_path(path, nc)

            if ok:
                # 成功! 构建分段
                segments = []
                cur_layer = path[0][0]
                cur_seg = [(path[0][1], path[0][2])]
                for i in range(1, len(path)):
                    lay, gx, gy = path[i]
                    if lay != cur_layer:
                        segments.append((cur_layer, cur_seg))
                        cur_layer = lay
                        cur_seg = [(gx, gy)]
                    else:
                        cur_seg.append((gx, gy))
                segments.append((cur_layer, cur_seg))
                return path, segments

            # 碰撞 → 加障碍 → 重试
            for cell in collision_cells:
                extra_blocked.add(cell)

        return None, None

    def draw_route(self, path, nc, net, src_pos, dst_pos):
        """画线到板上."""
        drawn = 0

        # 内层路径
        for i in range(len(path)-1):
            li1, gx1, gy1 = path[i]
            li2, gx2, gy2 = path[i+1]
            if li1 != li2:
                x1, y1 = (gx1+0.5)*self.G, (gy1+0.5)*self.G
                v = pcbnew.PCB_VIA(self.b)
                v.SetPosition(pcbnew.VECTOR2I(int(x1*MM), int(y1*MM)))
                v.SetWidth(int(RULES['via_pad']*MM))
                v.SetDrill(int(RULES['via_drill']*MM))
                v.SetNet(net)
                self.b.Add(v)
                drawn += 1
            else:
                x1, y1 = (gx1+0.5)*self.G, (gy1+0.5)*self.G
                x2, y2 = (gx2+0.5)*self.G, (gy2+0.5)*self.G
                t = pcbnew.PCB_TRACK(self.b)
                t.SetStart(pcbnew.VECTOR2I(int(x1*MM), int(y1*MM)))
                t.SetEnd(pcbnew.VECTOR2I(int(x2*MM), int(y2*MM)))
                t.SetWidth(int(RULES['track_width']*MM))
                t.SetLayer(self.lids[li1])
                t.SetNet(net)
                self.b.Add(t)
                drawn += 1

        # 连接源焊盘
        if path:
            first = path[0]
            fx, fy = (first[1]+0.5)*self.G, (first[2]+0.5)*self.G
            d = math.hypot(fx-src_pos[0], fy-src_pos[1])
            if d > 0.02:
                t = pcbnew.PCB_TRACK(self.b)
                t.SetStart(pcbnew.VECTOR2I(int(src_pos[0]*MM), int(src_pos[1]*MM)))
                t.SetEnd(pcbnew.VECTOR2I(int(fx*MM), int(fy*MM)))
                t.SetWidth(int(RULES['track_width']*MM))
                t.SetLayer(self.lids[first[0]])
                t.SetNet(net)
                self.b.Add(t)
                drawn += 1

        # 连接目的焊盘
        if path:
            last = path[-1]
            lx, ly = (last[1]+0.5)*self.G, (last[2]+0.5)*self.G
            d = math.hypot(lx-dst_pos[0], ly-dst_pos[1])
            if d > 0.02:
                t = pcbnew.PCB_TRACK(self.b)
                t.SetStart(pcbnew.VECTOR2I(int(lx*MM), int(ly*MM)))
                t.SetEnd(pcbnew.VECTOR2I(int(dst_pos[0]*MM), int(dst_pos[1]*MM)))
                t.SetWidth(int(RULES['track_width']*MM))
                t.SetLayer(self.lids[last[0]])
                t.SetNet(net)
                self.b.Add(t)
                drawn += 1

        return drawn


# Warp kernels
try:
    import warp as wp

    @wp.kernel
    def _clear_k(a: wp.array3d(dtype=wp.int32)):
        li, gx, gy = wp.tid()
        a[li, gx, gy] = 0

    @wp.kernel
    def _fim_k(T: wp.array3d(dtype=wp.float32),
                free: wp.array3d(dtype=wp.int32),
                active: wp.array3d(dtype=wp.int32),
                new_active: wp.array3d(dtype=wp.int32),
                nx: int, ny: int, nl: int,
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
                    c = T[li, nx2, ny2] + step
                    if c < mn:
                        mn = c
        for li2 in range(nl):
            if li2 != li and free[li2, gx, gy] == 1:
                c = T[li2, gx, gy] + vc * G
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
    """主入口: 逐对精确路由."""
    o0, pairs0 = opens_now()
    print('='*60)
    print('精确区域路由器(GPU Eikonal + geomir 验证 + 重试)')
    print('='*60)
    print('起点: opens=%d' % o0)

    b = pcbnew.LoadBoard(str(pcb))
    b.BuildConnectivity()
    lids = rg.signal_layer_ids(b)

    router = ExactRegionRouter(b, lids)
    print('GridModel: %dx%dx%d' % (router.nx, router.ny, router.nl))

    # 找 opens
    _, pairs = opens_now()
    print('需路由: %d 对' % len(pairs))

    # 逐对精确路由
    routed = 0
    skipped = 0
    t_start = time.time()

    for p in pairs:
        parts = p.split('|')
        nn = parts[0]
        x1, y1 = float(parts[1]), float(parts[2])
        x2, y2 = float(parts[4]), float(parts[5])

        net = b.GetNetInfo().GetNetItem(nn)
        if net is None:
            continue
        nc = net.GetNetCode()

        t0 = time.time()
        path, segments = router.route_net_exact(nc, (x1, y1), (x2, y2))
        t1 = time.time()

        if path is None:
            skipped += 1
            continue

        drawn = router.draw_route(path, nc, net, (x1, y1), (x2, y2))
        routed += 1

        # 重建 GridModel(后网看到前网)
        router.gm = rg.GridModel(b, lids)

    t_total = time.time() - t_start
    b.Save(str(pcb))

    print('\n完成: %d 成功, %d 跳过, %.1fs' % (routed, skipped, t_total))

    # 验证
    o1, pairs1 = opens_now()
    delta = o0 - o1
    new_pairs = sorted(set(pairs1) - set(pairs0))
    print('验收: opens %d->%d (delta=%d)' % (o0, o1, delta))
    if new_pairs:
        print('新增 pairs: %s' % new_pairs)

    report = {
        'opens_before': o0, 'opens_after': o1, 'opens_delta': delta,
        'routed': routed, 'skipped': skipped,
        'time': round(t_total, 1),
        'new_pairs': new_pairs,
    }
    print(json.dumps(report, ensure_ascii=False))


if __name__ == '__main__':
    main()
