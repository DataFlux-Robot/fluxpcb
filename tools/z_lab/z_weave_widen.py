# -*- coding: utf-8 -*-
"""z_weave_widen — 阶段1 weave-channel widening: 平行逃逸线垂直滑移开多车道.

战理(BGA 球场铁律的解法): 球间通孔几何不可能(0.69>0.566), 逃逸只能
顶层细线绕球 —— 而绕球通道是单车道(平行逃逸线占满)。解法不是挪墙
(墙是活铜), 而是把占道的平行逃逸线**垂直滑移 0.15-0.2mm** 开多车道。

机制: 对每条占道线, 找它的走线关节, 沿垂直于绕球走廊的方向整体滑移。
     滑移用 z_nudge_wall 的半挪语义(R=0.05 关节拖拽), 保持连通。
     滑后 verify_polyline 逐段验证走廊 + 连通计数差判据。

用法: z_weave_widen.py BOARD_DIR TARGET_NET BLOCKER1 [BLOCKER2 ...]
     BLOCKER = net_name(:dx:dy 可选自定义方向, 默认自动垂直)
返回: {"widened": n, "corridor_open": bool, "details": [...]}
"""
import json
import subprocess
import sys
import time
from pathlib import Path

KIPY = 'python'
LAB = Path('E:/DATA/vscode/ZCODE/z_lab')
KG = Path('E:/DATA/vscode/ZCODE/flux-neurotransmitter/fluxpcb/kicadgen')
sys.path.insert(0, str(KG))
sys.path.insert(0, 'E:/DATA/vscode/ZCODE/flux-neurotransmitter')

board_dir = Path(sys.argv[1])
target_net = sys.argv[2]
blockers = sys.argv[3:]        # ["UART4_RX:0.2:-0.2", "SDMMC1_D0:0.2:-0.2", ...]
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
    print(json.dumps({'widened': 0, 'closed': True,
                      'why': 'target already closed'}))
    sys.exit(0)

# ── 1. 解剖占道线: 找目标球附近的平行逃逸段 ──────────────────
# 解析 blocker 参数
parsed = []
for b in blockers:
    parts = b.split(':')
    name = parts[0]
    dx = float(parts[1]) if len(parts) > 1 else 0.2
    dy = float(parts[2]) if len(parts) > 2 else -0.2
    parsed.append((name, dx, dy))

# 目标球位置(从 live pair 的 A 端取)
pa = target_pairs[0].split('|')
tx, ty = float(pa[1]), float(pa[2])

results = []
for name, dx, dy in parsed:
    # 找该网在目标球附近的走线关节(端点), 逐个滑移
    r = subprocess.run(
        [KIPY, "-c", r'''
import pcbnew, sys, json
b = pcbnew.LoadBoard(sys.argv[1])
net_name = sys.argv[2]
tx, ty = float(sys.argv[3]), float(sys.argv[4])
R = 2.0   # 目标球 2mm 内的走线段
items = []
for t in b.GetTracks():
    if t.GetNetname() != net_name:
        continue
    cls = type(t).__name__
    if cls == 'PCB_VIA':
        continue
    s, e = t.GetStart(), t.GetEnd()
    sx, sy = s.x/1e6, s.y/1e6
    ex, ey = e.x/1e6, e.y/1e6
    d = min(((sx-tx)**2+(sy-ty)**2)**0.5,
            ((ex-tx)**2+(ey-ty)**2)**0.5)
    if d < R:
        items.append({'start': [round(sx,3),round(sy,3)],
                      'end': [round(ex,3),round(ey,3)],
                      'layer': t.GetLayerName(),
                      'd': round(d,3)})
print(json.dumps({'items': items}))
''', str(pcb), name, str(tx), str(ty)],
        capture_output=True, text=True, encoding='utf-8',
        errors='replace', timeout=120)
    segs = last_json(r.stdout).get('items') or []

    # 对每段的两个端点做关节拖拽(半挪语义, R=0.05)
    moved = 0
    for seg in segs:
        for (px, py) in [seg['start'], seg['end']]:
            # 检查端点是否在目标球附近(只滑逃逸段, 不滑远端)
            d = ((px - tx) ** 2 + (py - ty) ** 2) ** 0.5
            if d > 1.5:
                continue
            rn = subprocess.run(
                [KIPY, str(LAB / 'z_nudge_wall.py'),
                 str(board_dir), name, str(px), str(py),
                 str(dx), str(dy), '0.05'],
                capture_output=True, text=True, encoding='utf-8',
                errors='replace', timeout=120)
            out = last_json(rn.stdout)
            if out.get('moved', 0) > 0:
                moved += out['moved']

    results.append({'net': name, 'segments_near': len(segs),
                    'joints_moved': moved, 'dx': dx, 'dy': dy})

# ── 2. 验证走廊: 目标球附近的 via 槽 ─────────────────────────
import math   # noqa: E402
import geomir # noqa: E402
import pcbnew # noqa: E402

b2 = pcbnew.LoadBoard(str(pcb))
b2.BuildConnectivity()
_LIDS = list(range(32))
sg, rgz = geomir.extract_objects(b2, _LIDS)
nc = b2.GetNetInfo().GetNetItem(target_net).GetNetCode()
free_slots = []
for r in [0.4, 0.566, 0.8, 1.1, 1.4]:
    for k in range(16):
        a = k * math.pi / 8
        cx, cy = tx + r * math.cos(a), ty + r * math.sin(a)
        ok, _ = geomir.via_ok(cx, cy, nc, sg, rgz, via_r=0.25)
        if ok:
            free_slots.append((round(cx, 2), round(cy, 2), r))

# ── 3. 连通计数差判据 ────────────────────────────────────────
o1, pairs1 = opens_now()
delta = (o0 - o1) if (o0 >= 0 and o1 >= 0) else -99
new_pairs = sorted(set(pairs1) - set(pairs0))

print(json.dumps({
    'target': target_net, 'widened': sum(r['joints_moved'] for r in results),
    'details': results,
    'free_via_slots': len(free_slots), 'slot_examples': free_slots[:5],
    'opens_delta': delta, 'new_pairs': len(new_pairs),
    'corridor_open': len(free_slots) > 0,
}, ensure_ascii=False))
