# -*- coding: utf-8 -*-
"""bga_dogbone — BGA 球场 Dogbone 逃逸器(fluxpcb 集成模块).

核心: TFBGA240 0.8mm 球场 → 0.4mm via 对角缝隙 → 逃逸 stub.
  球半径 0.225mm + via 半径 0.2mm + JLC 间隙 0.09mm = 0.515mm
  对角缝隙中心距球心 0.566mm > 0.515mm → 物理可行!

集成到 fluxpcb 管线:
  routing.py → bga_dogbone.py → fluxrouter.py → shorts_cleanup.py

用法(KiCad Python):
  import bga_dogbone
  result = bga_dogbone.place_dogbones(board, target_nets)

用法(CLI):
  python bga_dogbone.py PCB [NET1 NET2 ...]
"""
import json
import math
import sys
from pathlib import Path as _P

import pcbnew

MM = 1e6

# JLC 物理约束
BALL_R = 0.225       # BGA 球半径
VIA_R = 0.2          # dogbone via 半径(0.4mm 直径)
JLC_CLEAR = 0.09     # JLC 最小间隙
MIN_BALL_DIST = BALL_R + VIA_R + JLC_CLEAR  # 0.515mm
BGA_HALF = 6.8       # 球场半宽(TFBGA240: 16 球 × 0.8mm)
BGA_CX, BGA_CY = 26.0, 24.0  # 球场中心(U1 位置)

# dogbone 候选偏移(对角缝隙)
DOG_OFFSETS = [
    (-0.566, -0.566), (-0.566, 0.566), (0.566, -0.566), (0.566, 0.566),
    (-0.4, -0.4), (-0.4, 0.4), (0.4, -0.4), (0.4, 0.4),
    (-0.8, -0.8), (-0.8, 0.8), (0.8, -0.8), (0.8, 0.8),
    (-0.8, -0.4), (-0.8, 0.4), (0.8, -0.4), (0.8, 0.4),
    (-0.4, -0.8), (-0.4, 0.8), (0.4, -0.8), (0.4, 0.8),
]


def collect_bga_balls(board):
    """收集 U1 BGA 球场内所有球."""
    balls = []
    for fp in board.GetFootprints():
        if fp.GetReference() != 'U1':
            continue
        for pad in fp.Pads():
            p = pad.GetPosition()
            px, py = p.x / MM, p.y / MM
            if abs(px - BGA_CX) <= BGA_HALF and abs(py - BGA_CY) <= BGA_HALF:
                balls.append((px, py, pad.GetNetCode(), pad.GetNetname()))
    return balls


def find_dogbone_via(bx, by, nc, balls, occupied_vias=None):
    """找最近的 dogbone via 位置.

    约束:
      1. 距所有其他球 ≥ 0.515mm
      2. 距其他 dogbone via ≥ 0.5mm
      3. 在板内
    """
    if occupied_vias is None:
        occupied_vias = []

    best = None
    best_d = 1e18

    for dx, dy in DOG_OFFSETS:
        vx, vy = bx + dx, by + dy

        # 约束 1: 距所有球
        min_ball = 1e18
        for ox, oy, onc, _ in balls:
            if onc == nc:
                continue
            d = math.hypot(vx - ox, vy - oy)
            min_ball = min(min_ball, d)
        if min_ball < MIN_BALL_DIST:
            continue

        # 约束 2: 距其他 dogbone via
        min_via = 1e18
        for ovx, ovy in occupied_vias:
            d = math.hypot(vx - ovx, vy - ovy)
            min_via = min(min_via, d)
        if min_via < 0.5:
            continue

        # 选最近的
        d = math.hypot(vx - bx, vy - by)
        if d < best_d:
            best_d = d
            best = (vx, vy)

    return best


def needs_escape(board, nc):
    """检查网是否需要 BGA 逃逸(有 BGA 球 + 连接器)."""
    has_bga = False
    has_connector = False

    for fp in board.GetFootprints():
        if fp.GetReference() == 'U1':
            for pad in fp.Pads():
                if pad.GetNetCode() == nc:
                    has_bga = True
                    break
        elif fp.GetReference().startswith('J'):
            for pad in fp.Pads():
                if pad.GetNetCode() == nc:
                    has_connector = True
                    break
        if has_bga and has_connector:
            return True

    return False


def place_dogbones(board, target_nets=None):
    """为 BGA 网放置 dogbone via + 逃逸 stub.

    返回: {'placed': [...], 'skipped': [...], 'drawn': int}
    """
    balls = collect_bga_balls(board)
    occupied_vias = []

    # 找需逃逸的网
    escape_nets = {}
    for px, py, nc, nn in balls:
        if nc == 0 or nn in escape_nets:
            continue
        if target_nets and nn not in target_nets:
            continue
        if needs_escape(board, nc):
            escape_nets[nn] = (px, py, nc)

    placed = []
    skipped = []
    drawn = 0

    for nn, (bx, by, nc) in sorted(escape_nets.items()):
        # 找 dogbone 位置
        via_pos = find_dogbone_via(bx, by, nc, balls, occupied_vias)
        if via_pos is None:
            skipped.append(nn)
            continue

        vx, vy = via_pos
        occupied_vias.append((vx, vy))

        # 找网对象
        net = board.GetNetInfo().GetNetItem(nn)
        if net is None:
            skipped.append(nn)
            continue

        # 画逃逸 stub(球→via)
        t = pcbnew.PCB_TRACK(board)
        t.SetStart(pcbnew.VECTOR2I(int(bx * MM), int(by * MM)))
        t.SetEnd(pcbnew.VECTOR2I(int(vx * MM), int(vy * MM)))
        t.SetWidth(int(0.10 * MM))
        t.SetLayer(pcbnew.F_Cu)
        t.SetNet(net)
        board.Add(t)
        drawn += 1

        # 画 dogbone via(0.4mm)
        v = pcbnew.PCB_VIA(board)
        v.SetPosition(pcbnew.VECTOR2I(int(vx * MM), int(vy * MM)))
        v.SetWidth(int(0.4 * MM))
        v.SetDrill(int(0.2 * MM))
        v.SetNet(net)
        board.Add(v)
        drawn += 1

        placed.append({
            'net': nn, 'ball': (bx, by),
            'via': (vx, vy),
            'stub_len': math.hypot(vx - bx, vy - by),
        })

    return {
        'placed': placed,
        'skipped': skipped,
        'drawn': drawn,
        'total_nets': len(escape_nets),
    }


def main():
    """CLI 入口."""
    pcb_path = sys.argv[1]
    target_nets = sys.argv[2:] if len(sys.argv) > 2 else None

    board = pcbnew.LoadBoard(pcb_path)
    board.BuildConnectivity()

    result = place_dogbones(board, target_nets)

    board.Save(pcb_path)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
