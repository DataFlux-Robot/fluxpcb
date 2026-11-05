# -*- coding: utf-8 -*-
"""z_seg_harvest — 靶2a 分段收割算子: 长对切段逐岛收割.

机制(长对超时根因的对偶): 全板长路径规划 >1800s, 切成岛间短程规划
  1. 岛聚类: 目标网全部铜(线/孔/焊盘)按共享端点做并查集 -> 连通岛
  2. AB 轴排序: 开路对端点 A,B 投影为轴, 各岛质心按轴排序成链
  3. 逐岛对收割: 相邻(岛,岛/端点)取最近锚点(岛上的真实铜顶点),
     合成单子对 -> geomir 规划(短程) -> direct_link 画线(端点三态附着)
     每段独立预算(SEG_TIMEOUT), 一段失败即停(诚实记账)
  4. 终测: opens 实测, 报告逐段战果

用法: z_seg_harvest.py BOARD_DIR NET
判据: opens delta(实测) —— 段全成而不开 = 附着失败, 入账不虚报.
"""
import json
import subprocess
import sys
from pathlib import Path

KG = Path('E:/DATA/vscode/ZCODE/flux-neurotransmitter/fluxpcb/kicadgen')
KIPY = 'python'
SEG_TIMEOUT = 600
SNAP = 10000            # 0.01mm (pcbnew 单位 nm)

board_dir = Path(sys.argv[1])
net = sys.argv[2]
pcb = sorted(board_dir.glob('*.kicad_pcb'))[0]

sys.path.insert(0, 'E:/DATA/vscode/ZCODE/flux-neurotransmitter')
from fluxpcb.routing import find_kicad           # noqa: E402
from fluxpcb.kernel import state as kstate       # noqa: E402


def last_json(out):
    lines = [l.strip() for l in (out or '').splitlines()
             if l.strip().startswith('{')]
    return json.loads(lines[-1]) if lines else {}


kicad_cli, kipy = find_kicad()
q = kstate.query(kicad_cli, kipy, pcb, 'opens', consolidate=True)
live = (q.detail or {}).get('pairs') or []
mine = [p for p in live if p.split('|')[0] == net]
if not mine:
    print(json.dumps({'net': net, 'closed': True, 'why': 'no live pair'}))
    sys.exit(0)
pa = mine[0].split('|')
A = (float(pa[1]), float(pa[2]))
B = (float(pa[4]), float(pa[5]))

# ── 1. 岛聚类(并查集, 端点吸附 0.01mm) ─────────────────────
r = subprocess.run(
    [KIPY, "-c", r'''
import pcbnew, sys, json
b = pcbnew.LoadBoard(sys.argv[1])
net = sys.argv[2]
SNAP = 10000
items = []
for t in b.GetTracks():
    if t.GetNetname() != net:
        continue
    cls = type(t).__name__
    if cls == "PCB_VIA":
        p = t.GetPosition()
        items.append([[p.x, p.y]])
    else:
        s, e = t.GetStart(), t.GetEnd()
        items.append([[s.x, s.y], [e.x, e.y]])
for fp in b.GetFootprints():
    for pad in fp.Pads():
        if pad.GetNetname() == net:
            p = pad.GetPosition()
            items.append([[p.x, p.y]])
# 并查集: 共享吸附顶点的 item 合并
parent = list(range(len(items)))
def find(x):
    while parent[x] != x:
        parent[x] = parent[parent[x]]
        x = parent[x]
    return x
def union(a, c):
    ra, rc = find(a), find(c)
    if ra != rc:
        parent[rc] = ra
vmap = {}
for i, pts in enumerate(items):
    for (x, y) in pts:
        key = (x // SNAP, y // SNAP)
        if key in vmap:
            union(i, vmap[key])
        else:
            vmap[key] = i
groups = {}
for i in range(len(items)):
    groups.setdefault(find(i), []).append(i)
islands = []
for gid, members in groups.items():
    vs = []
    for m in members:
        vs.extend(items[m])
    islands.append(vs)
print(json.dumps({"islands": islands}))
''', str(pcb), net],
    capture_output=True, text=True, encoding="utf-8",
    errors="replace", timeout=300)
isl = last_json(r.stdout).get('islands') or []
if not isl:
    print(json.dumps({'net': net, 'closed': False,
                      'why': 'no copper found'}))
    sys.exit(1)

# ── 2. AB 轴排序 + 锚点选择 ────────────────────────────────
ax, ay = A
bx, by = B
dx, dy = bx - ax, by - ay
L2 = dx * dx + dy * dy or 1.0


def proj(p):
    return ((p[0] - ax) * dx + (p[1] - ay) * dy) / L2


def dist2(p, c):
    return (p[0] - c[0]) ** 2 + (p[1] - c[1]) ** 2


# 每岛: 质心投影 + 全部顶点(锚点候选, mm)
def to_mm(v):
    return (v[0] / 1e6, v[1] / 1e6)


isl_info = []
end_isl = {'A': None, 'B': None}
for vs in isl:
    pts = [to_mm(v) for v in vs]
    cx = sum(p[0] for p in pts) / len(pts)
    cy = sum(p[1] for p in pts) / len(pts)
    # 端点所在的岛与端点合并(否则 A->含A岛 = 零长子对, 首测教训)
    if any(dist2(p, A) < 0.01 for p in pts):
        end_isl['A'] = pts
        continue
    if any(dist2(p, B) < 0.01 for p in pts):
        end_isl['B'] = pts
        continue
    isl_info.append({'pts': pts, 'proj': proj((cx, cy))})
isl_info.sort(key=lambda d: d['proj'])

# 链: A(含其岛) -> 岛... -> B(含其岛)
chain = [{'pts': (end_isl['A'] or [A]) + [A], 'proj': 0.0}] + isl_info + \
        [{'pts': (end_isl['B'] or [B]) + [B], 'proj': 1.0}]

# ── 3. 逐段收割 ────────────────────────────────────────────
segs = []
for i in range(len(chain) - 1):
    g1, g2 = chain[i], chain[i + 1]
    best, bd = None, 1e18
    for p in g1['pts']:
        for c in g2['pts']:
            d = dist2(p, c)
            if d < bd:
                bd, best = d, (p, c)
    if bd < 0.0001:                # 零长防御(顶点重合 = 已连通侧)
        continue
    (x1, y1), (x2, y2) = best
    segs.append((round(x1, 3), round(y1, 3), round(x2, 3), round(y2, 3)))

results = []
for i, (x1, y1, x2, y2) in enumerate(segs):
    pf = board_dir / ('seg_pairs_%d.tmp.txt' % i)
    pf.write_text('%s|%.3f|%.3f|0|%.3f|%.3f|0' % (net, x1, y1, x2, y2),
                  encoding='utf-8')
    rg = subprocess.run(
        [KIPY, str(KG / 'geomir.py'), str(pcb), net,
         str(board_dir / ('plan_seg_%d' % i)), str(pf)],
        capture_output=True, text=True, encoding='utf-8',
        errors='replace', timeout=SEG_TIMEOUT)
    plan = last_json(rg.stdout)
    pths = plan.get('paths') or []
    if not pths:
        results.append({'seg': i, 'from': [x1, y1], 'to': [x2, y2],
                        'ok': False,
                        'why': 'no path', 'rejects':
                            len(plan.get('rejects') or [])})
        print(json.dumps(results[-1], ensure_ascii=False), flush=True)
        break                      # 段链断了就停(诚实)
    p0 = pths[0]
    spec = {'net': net, 'pts': p0['pts'], 'width': p0.get('width', 0.2)}
    sp = board_dir / 'direct_link_spec.json'
    sp.write_text(json.dumps(spec), encoding='utf-8')
    rd = subprocess.run(
        [KIPY, str(KG / 'direct_link.py'), str(pcb), str(sp)],
        capture_output=True, text=True, encoding='utf-8',
        errors='replace', timeout=SEG_TIMEOUT)
    out = last_json(rd.stdout)
    ok = bool(out.get('drawn')) or bool(out.get('closed'))
    results.append({'seg': i, 'from': [x1, y1], 'to': [x2, y2],
                    'ok': ok, 'len': p0.get('length_mm'),
                    'drawn': out.get('drawn'), 'err': out.get('error')})
    print(json.dumps(results[-1], ensure_ascii=False), flush=True)
    if not ok:
        break

# ── 4. 终测 ────────────────────────────────────────────────
q2 = kstate.query(kicad_cli, kipy, pcb, 'opens', consolidate=True)
after = [p for p in ((q2.detail or {}).get('pairs') or [])
         if p.split('|')[0] == net]
print(json.dumps({'net': net, 'segs': len(segs), 'done': len(results),
                  'closed': len(after) == 0,
                  'pairs_before': len(mine), 'pairs_after': len(after),
                  'results': results}, ensure_ascii=False))
