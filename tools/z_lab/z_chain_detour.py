# -*- coding: utf-8 -*-
"""z_chain_detour — 链感知绕行改道算子(靶2b 核心): 用绕行折线替换占道段.

战理(为什么盲拖不行): z_nudge_wall 半挪拖歪邻居段(楼梯链撕裂),
整段平移又拉不动(段超出半径)。正确做法是**替换**而非挪动:
  占道段 A->B 被走廊切断 -> 用绕行折线 A->C->D->B 替换(C/D 绕过走廊)
  端点 A/B 不动(链连通性保持) + 新增绕行段(几何合法)

机制:
  1. 定位: verify_polyline 逐段找走廊上的占道段(含网码/坐标)
  2. 拆角: 在占道段两侧各取一个锚点(距走廊 0.4mm 垂直偏移)
  3. 改道: 用 3 点折线(锚点1->锚点2)替换原段 —— 不是挪动, 是替换
  4. 验证: verify_polyline 绕行段 + 连通计数差

用法: z_chain_detour.py BOARD_DIR TARGET_NET CORRIDOR_X1,Y1,X2,Y2,...
     CORRIDOR = 走廊折线坐标(目标网要走的路)
返回: {"detoured": n, "corridor_clear": bool, "opens_delta": int}
"""
import json
import math
import subprocess
import sys
from pathlib import Path

KIPY = 'python'
LAB = Path('E:/DATA/vscode/ZCODE/z_lab')
KG = Path('E:/DATA/vscode/ZCODE/flux-neurotransmitter/fluxpcb/kicadgen')
sys.path.insert(0, str(KG))
sys.path.insert(0, 'E:/DATA/vscode/ZCODE/flux-neurotransmitter')

board_dir = Path(sys.argv[1])
target_net = sys.argv[2]
corridor_coords = [float(x) for x in sys.argv[3].split(',')] if len(sys.argv) > 3 else []
pcb = sorted(board_dir.glob('*.kicad_pcb'))[0]

from fluxpcb.routing import find_kicad       # noqa: E402
from fluxpcb.kernel import state as kstate   # noqa: E402

kicad_cli, kipy = find_kicad()


def last_json(out):
    lines = [l.strip() for l in (out or '').splitlines()
             if l.strip().startswith('{')]
    return json.loads(lines[-1]) if lines else {}


def opens_now():
    q = kstate.query(kicad_cli, kipy, pcb, 'opens', consolidate=True)
    return q.value, sorted((q.detail or {}).get('pairs') or [])


o0, pairs0 = opens_now()
target_pairs = [p for p in pairs0 if p.split('|')[0] == target_net]
if not target_pairs:
    print(json.dumps({'detoured': 0, 'closed': True}))
    sys.exit(0)

# 走廊折线
corridor = [(corridor_coords[i], corridor_coords[i+1])
            for i in range(0, len(corridor_coords), 2)]

import pcbnew  # noqa: E402
import geomir  # noqa: E402

b = pcbnew.LoadBoard(str(pcb))
b.BuildConnectivity()
_LIDS = list(range(32))
sg, rgz = geomir.extract_objects(b, _LIDS)
nc = b.GetNetInfo().GetNetItem(target_net).GetNetCode()
MM = 1e6

# ── 1. 定位占道段(verify_polyline 逐段找 blocker) ─────────
blockers = []  # [(seg_start, seg_end, net_name, at_point, layer)]
for i in range(len(corridor) - 1):
    p1, p2 = corridor[i], corridor[i+1]
    ok, bl = geomir.verify_polyline([p1, p2], 0.1, nc, sg[0], rgz[0])
    if not ok and isinstance(bl, dict):
        bnet = bl.get('net')
        at = bl.get('at', [0, 0])
        bname = b.GetNetInfo().GetNetItem(bnet).GetNetname() if bnet else 'UNKNOWN'
        blockers.append((p1, p2, bname, at, 'top_copper'))

print('[detour] 走廊占道段 %d 处: %s'
      % (len(blockers), [(b[2], b[3]) for b in blockers]), flush=True)

# ── 2. 对每个占道段: 拆角+改道(去重+防级联+全段覆盖) ──────
detoured = 0
detoured_keys = set()   # 防级联: 记录已改道的段坐标

for (p1, p2, bname, at, layer) in blockers:
    tx, ty = at  # blocker 最近点
    # 找占道网在走廊附近的**全部**段(不止第一个)
    target_segs = []
    for t in b.GetTracks():
        if type(t).__name__ != 'PCB_TRACK':
            continue
        if t.GetNetname() != bname:
            continue
        s, e = t.GetStart(), t.GetEnd()
        sx, sy = s.x/MM, s.y/MM
        ex, ey = e.x/MM, e.y/MM
        mx, my = (sx+ex)/2, (sy+ey)/2
        # 段中点距 blocker 点 < 0.8mm(扩大范围捕多段)
        if math.hypot(mx-tx, my-ty) < 0.8:
            # 防级联: 跳过已改道的段(用坐标键)
            key = (round(sx,3), round(sy,3), round(ex,3), round(ey,3))
            rkey = (round(ex,3), round(ey,3), round(sx,3), round(sy,3))
            if key in detoured_keys or rkey in detoured_keys:
                continue
            target_segs.append((t, sx, sy, ex, ey))

    if not target_segs:
        print('[detour] %s 占道段未定位或已改道, 跳过' % bname, flush=True)
        continue

    # 拆角: 找段的两个端点, 在**走廊垂直方向**偏移(非占道段方向)
    for (target_seg, sx, sy, ex, ey) in target_segs:
        # 走廊方向(从 p1 到 p2)
        cdx, cdy = p2[0]-p1[0], p2[1]-p1[1]
        c_len = math.hypot(cdx, cdy) or 1.0
        cdx, cdy = cdx/c_len, cdy/c_len
        ccx, ccy = (p1[0]+p2[0])/2, (p1[1]+p2[1])/2
        rx, ry = tx-ccx, ty-ccy
        r_len = math.hypot(rx, ry) or 1.0
        rx, ry = rx/r_len, ry/r_len
        perp1 = (-cdy, cdx)
        perp2 = (cdy, -cdx)
        if perp1[0]*rx + perp1[1]*ry > perp2[0]*rx + perp2[1]*ry:
            px, py = perp1
        else:
            px, py = perp2

        OFFSET = 2.0
        mid_x, mid_y = (sx+ex)/2 + px*OFFSET, (sy+ey)/2 + py*OFFSET
        new_pts = [(sx, sy), (mid_x, mid_y), (ex, ey)]

        # 注册防级联键
        key = (round(sx,3), round(sy,3), round(ex,3), round(ey,3))
        detoured_keys.add(key)

        b.Remove(target_seg)
        net = b.FindNet(bname)
        for j in range(len(new_pts)-1):
            t = pcbnew.PCB_TRACK(b)
            t.SetStart(pcbnew.VECTOR2I(int(new_pts[j][0]*MM), int(new_pts[j][1]*MM)))
            t.SetEnd(pcbnew.VECTOR2I(int(new_pts[j+1][0]*MM), int(new_pts[j+1][1]*MM)))
            t.SetWidth(target_seg.GetWidth())
            t.SetLayer(target_seg.GetLayer())
            t.SetNet(net)
            b.Add(t)
        detoured += 1
        print('[detour] %s 绕行改道: %s->%s->%s'
              % (bname, (round(sx,3),round(sy,3)),
                 (round(mid_x,3),round(mid_y,3)),
                 (round(ex,3),round(ey,3))), flush=True)

b.Save(str(pcb))

# ── 3. 验证走廊 ─────────────────────────────────────────
b2 = pcbnew.LoadBoard(str(pcb))
b2.BuildConnectivity()
sg2, rgz2 = geomir.extract_objects(b2, _LIDS)
nc2 = b2.GetNetInfo().GetNetItem(target_net).GetNetCode()
clear_count = 0
for i in range(len(corridor) - 1):
    ok, _ = geomir.verify_polyline([corridor[i], corridor[i+1]], 0.1, nc2, sg2[0], rgz2[0])
    if ok:
        clear_count += 1

o1, pairs1 = opens_now()
delta = (o0 - o1) if (o0 >= 0 and o1 >= 0) else -99
new_pairs = sorted(set(pairs1) - set(pairs0))

print(json.dumps({
    'target': target_net, 'detoured': detoured,
    'corridor_clear': clear_count == len(corridor) - 1,
    'clear_segs': '%d/%d' % (clear_count, len(corridor) - 1),
    'opens_delta': delta, 'new_pairs': new_pairs,
    'net_gain': delta > 0 and not new_pairs,
}, ensure_ascii=False))
