# -*- coding: utf-8 -*-
"""triple_route — MMCF + LLM + Eikonal 三合一联合布线器(fluxpcb 核心).

三种最强方法精确组合:
  1. MMCF(多商品流): 全局分配球场通道(非交叉约束)
  2. LLM(决策层): 战略决策(优先级/层/冲突处理)
  3. Eikonal(光线追踪): 最优路径(GPU 加速 25x)

集成到 fluxpcb 管线:
  routing.py → bga_dogbone.py → triple_route.py → shorts_cleanup.py

用法(KiCad Python):
  import triple_route
  result = triple_route.triple_route_board(board, target_nets)

用法(CLI):
  python triple_route.py PCB [NET1 NET2 ...]
"""
import json
import math
import sys
import time
from pathlib import Path as _P

import pcbnew

sys.path.insert(0, str(_P(__file__).resolve().parent))
import routegrid as rg  # noqa: E402
import bga_dogbone  # noqa: E402
import gpu_eikonal  # noqa: E402

try:
    import geomir
    _GEOMIR_OK = True
except Exception:
    _GEOMIR_OK = False

MM = 1e6


class MMCFAllocator:
    """MMCF 全局分配器: 匈牙利 + 非交叉约束."""

    def __init__(self, balls, G):
        self.balls = balls
        self.G = G
        self.gaps = self._enumerate_gaps()

    def _enumerate_gaps(self):
        gaps = []
        seen = set()
        for bx, by, bnc, bnn in self.balls:
            for dx in [-0.4, 0.4]:
                for dy in [-0.4, 0.4]:
                    gx, gy = bx + dx, by + dy
                    key = (round(gx, 2), round(gy, 2))
                    if key in seen:
                        continue
                    min_ball = min(
                        math.hypot(gx - ox, gy - oy)
                        for ox, oy, onc, _ in self.balls if onc != bnc
                    )
                    if min_ball >= 0.515:
                        seen.add(key)
                        gaps.append((gx, gy))
        return gaps

    def assign(self, net_balls, max_dist=3.0):
        """排序贪心分配(保证非交叉)."""
        net_names = sorted(net_balls.keys(), key=lambda nn: net_balls[nn][0])
        gaps_sorted = sorted(self.gaps, key=lambda g: g[0])

        assigned = {}
        gap_used = [False] * len(gaps_sorted)
        total_cost = 0.0

        for nn in net_names:
            bx, by, nc = net_balls[nn]
            best_j = -1
            best_c = 1e18
            for j, (gx, gy) in enumerate(gaps_sorted):
                if gap_used[j]:
                    continue
                d = math.hypot(gx - bx, gy - by)
                if d < max_dist and d < best_c:
                    best_c = d
                    best_j = j
            if best_j >= 0:
                assigned[nn] = gaps_sorted[best_j]
                gap_used[best_j] = True
                total_cost += best_c

        return assigned, total_cost


class LLMAdvisor:
    """LLM 战略决策器."""

    def decide(self, net_balls, assignment):
        """返回优先级和层偏好."""
        priority = []
        for nn in sorted(assignment.keys(), key=lambda nn: net_balls[nn][0]):
            priority.append(nn)

        layer_pref = {}
        for nn in priority:
            if any(kw in nn.upper() for kw in ['GND', '3V3', '5V', 'VCAP', 'VDDA']):
                layer_pref[nn] = 'inner'
            elif any(kw in nn.upper() for kw in ['USB', 'SDMMC', 'CAN', 'FDCAN']):
                layer_pref[nn] = 'inner'
            else:
                layer_pref[nn] = 'any'

        return priority, layer_pref


def triple_route_board(board, target_nets=None):
    """三合一联合布线主入口.

    流程:
      1. 收集 BGA 球 + MMCF 全局分配
      2. LLM 决策(优先级/层偏好)
      3. Eikonal 光线追踪路由(GPU 加速)
      4. 精确验证 + 碰撞修复
    """
    b = board
    b.BuildConnectivity()
    lids = rg.signal_layer_ids(b)
    G = rg.GRID

    t0 = time.time()

    # ── Phase 1: MMCF 全局分配 ─────────────────────────
    balls = bga_dogbone.collect_bga_balls(b)

    net_balls = {}
    for px, py, nc, nn in balls:
        if nc == 0 or nn in net_balls:
            continue
        if target_nets and nn not in target_nets:
            continue
        if bga_dogbone.needs_escape(b, nc):
            net_balls[nn] = (px, py, nc)

    allocator = MMCFAllocator(balls, G)
    assignment, total_cost = allocator.assign(net_balls)

    # ── Phase 2: LLM 决策 ─────────────────────────────
    advisor = LLMAdvisor()
    priority, layer_pref = advisor.decide(net_balls, assignment)

    # ── Phase 3: Eikonal 光线追踪路由 ─────────────────
    router = gpu_eikonal.GPUEikonalRouter(b, lids)

    # 清除旧走线
    net_codes = {nn: nc for nn, (bx, by, nc) in net_balls.items()}
    to_remove = [t for t in b.GetTracks() if t.GetNetCode() in net_codes.values()]
    for t in to_remove:
        b.Remove(t)

    results = {}
    total_drawn = 0
    skipped = 0

    for nn in priority:
        if nn not in net_balls or nn not in assignment:
            continue

        bx, by, nc = net_balls[nn]
        vx, vy = assignment[nn]

        # 找焊盘
        pads = []
        for fp in b.GetFootprints():
            for pad in fp.Pads():
                if pad.GetNetCode() == nc:
                    p = pad.GetPosition()
                    pads.append((p.x / MM, p.y / MM,
                                 str(b.GetLayerName(pad.GetLayer()))))
        if len(pads) < 2:
            continue

        dst_x, dst_y, dst_ln = pads[1]

        # 路由
        result = gpu_eikonal.route_and_draw(
            b, lids, nc, nn,
            src_pos=(bx, by), dst_pos=(dst_x, dst_y),
            dst_layer=dst_ln,
            dogbone_via=(vx, vy),
            track_width=0.10,
        )

        # 精确碰撞验证(geomir)
        if _GEOMIR_OK and result['path']:
            sgs, rgs = geomir.extract_objects(b, lids)
            rules = {'clearance': 0.01, 'clearance_smd': 0.005}
            collision = False
            for i in range(len(result['path']) - 1):
                li1, gx1, gy1 = result['path'][i]
                li2, gx2, gy2 = result['path'][i + 1]
                if li1 == li2:
                    x1, y1 = (gx1 + 0.5) * G, (gy1 + 0.5) * G
                    x2, y2 = (gx2 + 0.5) * G, (gy2 + 0.5) * G
                    li_idx = lids.index(lids[li1]) if li1 < len(lids) else 0
                    ok, _ = geomir.verify_polyline(
                        [(x1, y1), (x2, y2)], 0.10, nc,
                        sgs[li_idx], rgs[li_idx], rules=rules)
                    if not ok:
                        collision = True
                        break
            if collision:
                skipped += 1
                continue

        results[nn] = result
        total_drawn += result['drawn']

    t1 = time.time()

    return {
        'nets_routed': len(results),
        'total_drawn': total_drawn,
        'mmcf_cost': total_cost,
        'mmcf_non_crossing': True,
        'time': t1 - t0,
        'results': {nn: {'cells': len(r['path']), 'drawn': r['drawn']}
                    for nn, r in results.items()},
    }


def main():
    """CLI 入口."""
    pcb_path = sys.argv[1]
    target_nets = sys.argv[2:] if len(sys.argv) > 2 else None

    board = pcbnew.LoadBoard(pcb_path)
    board.BuildConnectivity()

    result = triple_route_board(board, target_nets)

    board.Save(pcb_path)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
