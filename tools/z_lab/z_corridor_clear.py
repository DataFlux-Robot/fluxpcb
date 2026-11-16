# -*- coding: utf-8 -*-
"""z_corridor_clear — 走廊保留复合算子(靶2b): 先布目标网再让邻网绕行.

CEO 定案的机制: 多墙形态里墙是活铜不能撕, 但可以**先给目标网开走廊**
—— 撕掉走廊上的占道段 -> 布目标网 -> 重布邻网绕行 -> 整体验收。
副本内允许中间态断线, 最终提交必须净收益。

机制:
  1. 解剖: 找目标网走廊上的占道段(verify_polyline 逐段定位 blocker)
  2. 撕占道: 移除走廊上的 blocker 段(有损, 但副本内允许)
  3. 布目标: z_seg_harvest/z_harvest 走已清走廊
  4. 重布邻网: 用 z_seg_harvest 把撕掉的 blocker 网重新布线绕行
  5. 整体验收: 全部网 opens_delta + 无新增对 -> 落板

用法: z_corridor_clear.py BOARD_DIR TARGET_NET [CORRIDOR_SEGMENTS]
     CORRIDOR = x1,y1,x2,y2,... (可选: 手工指定走廊; 空=自动解剖)
返回: {"target_closed": bool, "blockers_rerouted": n, "opens_delta": int}
"""
import json
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
corridor_args = sys.argv[3:] if len(sys.argv) > 3 else []
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
    print(json.dumps({'target_closed': True, 'why': 'already closed'}))
    sys.exit(0)

# ── 1. 解剖走廊: 找占道段 ─────────────────────────────────
import pcbnew  # noqa: E402
import geomir  # noqa: E402
import math   # noqa: E402

b = pcbnew.LoadBoard(str(pcb))
b.BuildConnectivity()
_LIDS = list(range(32))
sg, rgz = geomir.extract_objects(b, _LIDS)
nc = b.GetNetInfo().GetNetItem(target_net).GetNetCode()

pa = target_pairs[0].split('|')
A = (float(pa[1]), float(pa[2]))
B = (float(pa[4]), float(pa[5]))

# 自动解剖: 用 geomir 试规划, 从 reject 里提取 blocker
if not corridor_args:
    # 写全对文件, geomir 会给出 rejects(含 blocker 网码)
    pf = board_dir / 'corridor_probe.txt'
    pf.write_text('%s|%.3f|%.3f|0|%.3f|%.3f|0' % (target_net, A[0], A[1], B[0], B[1]))
    r = subprocess.run(
        [KIPY, str(KG / 'geomir.py'), str(pcb), target_net,
         str(board_dir / 'plan_probe'), str(pf)],
        capture_output=True, text=True, encoding='utf-8',
        errors='replace', timeout=300)
    plan = last_json(r.stdout)
    rejects = plan.get('rejects') or []
    # 从 reject 提取 blocker 网码
    blocker_codes = set()
    for rej in rejects:
        if isinstance(rej, dict) and 'net' in rej:
            blocker_codes.add(rej['net'])
    print('[corridor] geomir rejects=%d, blocker_codes=%s'
          % (len(rejects), blocker_codes), flush=True)
else:
    # 手工指定走廊: x1,y1,x2,y2,...
    blocker_codes = set()
    coords = [float(x) for x in corridor_args[0].split(',')]
    corridor = [(coords[i], coords[i+1]) for i in range(0, len(coords), 2)]

# ── 2. 撕占道段(副本内) ─────────────────────────────────
# 找 blocker 网在目标走廊附近的段并撕掉
ni = b.GetNetInfo()
blocker_names = set()
for code in blocker_codes:
    item = ni.GetNetItem(code)
    if item:
        blocker_names.add(item.GetNetname())

ripped = 0
ripped_nets = set()
for t in list(b.GetTracks()):
    if type(t).__name__ not in ('PCB_TRACK', 'PCB_VIA'):
        continue
    if t.GetNetname() == target_net:
        continue
    if t.GetNetname() not in blocker_names:
        continue
    # 检查是否在走廊附近(目标对端点 2mm 内)
    if type(t).__name__ == 'PCB_VIA':
        p = t.GetPosition()
        d = min(math.hypot(p.x/1e6-A[0], p.y/1e6-A[1]),
                math.hypot(p.x/1e6-B[0], p.y/1e6-B[1]))
    else:
        s, e = t.GetStart(), t.GetEnd()
        d = min(math.hypot(s.x/1e6-A[0], s.y/1e6-A[1]),
                math.hypot(e.x/1e6-A[0], e.y/1e6-A[1]),
                math.hypot(s.x/1e6-B[0], s.y/1e6-B[1]),
                math.hypot(e.x/1e6-B[0], e.y/1e6-B[1]))
    if d < 3.0:
        b.Remove(t)
        ripped += 1
        ripped_nets.add(t.GetNetname())

b.Save(str(pcb))
print('[corridor] ripped %d segments from %s' % (ripped, ripped_nets), flush=True)

# ── 3. 布目标网 ─────────────────────────────────────────
r = subprocess.run(
    [KIPY, '-u', str(LAB / 'z_seg_harvest.py'), str(board_dir), target_net],
    capture_output=True, text=True, encoding='utf-8',
    errors='replace', timeout=600)
target_result = last_json(r.stdout)
target_closed = target_result.get('closed', False)
print('[corridor] target %s -> %s' % (target_net, target_result), flush=True)

# ── 4. 重布邻网(撕掉的 blocker 网) ─────────────────────
rerouted = 0
for name in ripped_nets:
    r2 = subprocess.run(
        [KIPY, '-u', str(LAB / 'z_seg_harvest.py'), str(board_dir), name],
        capture_output=True, text=True, encoding='utf-8',
        errors='replace', timeout=600)
    rr = last_json(r2.stdout)
    if rr.get('closed'):
        rerouted += 1
    print('[corridor] reroute %s -> %s' % (name, rr.get('closed', rr.get('why', ''))), flush=True)

# ── 5. 整体验收 ─────────────────────────────────────────
o1, pairs1 = opens_now()
delta = (o0 - o1) if (o0 >= 0 and o1 >= 0) else -99
new_pairs = sorted(set(pairs1) - set(pairs0))

print(json.dumps({
    'target_net': target_net, 'target_closed': target_closed,
    'ripped': ripped, 'ripped_nets': sorted(ripped_nets),
    'blockers_rerouted': rerouted,
    'opens_delta': delta, 'new_pairs': new_pairs,
    'net_gain': delta > 0 and not new_pairs,
}, ensure_ascii=False))
