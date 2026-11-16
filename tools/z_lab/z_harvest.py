# -*- coding: utf-8 -*-
"""z_harvest — plan->direct_link 收割驱动(今日链条的批量版).

用法: z_harvest.py BOARD_DIR NET1,NET2,...
每网: geomir 规划 -> 取最优验证路 -> direct_link 收割 -> 报告.
"""
import json
import subprocess
import sys
from pathlib import Path

KG = Path('E:/DATA/vscode/ZCODE/flux-neurotransmitter/fluxpcb/kicadgen')
KIPY = ('C:/Users/zhang/AppData/Local/Programs/KiCad/10.0/bin/python.exe')

board_dir = Path(sys.argv[1])
pcb = sorted(board_dir.glob('*.kicad_pcb'))[0]   # 多板参数化
nets = [x for x in sys.argv[2].split(',') if x.strip()]

# pairs 单源 = 实时 query(反自欺教训: ratsnest_pairs.txt 是 stale
# 快照, 拿它当目标去'收割'已闭合的对 = 假增益, VDDA 案)
sys.path.insert(0, 'E:/DATA/vscode/ZCODE/flux-neurotransmitter')
from fluxpcb.routing import find_kicad           # noqa: E402
from fluxpcb.kernel import state as kstate       # noqa: E402

kicad_cli, kipy = find_kicad()
_q = kstate.query(kicad_cli, kipy, pcb, 'opens', consolidate=True)
live_pairs = (_q.detail or {}).get('pairs') or []
pairs = board_dir / 'loop_pairs.tmp.txt'
pairs.write_text('\n'.join(live_pairs), encoding='utf-8')
print(json.dumps({'live_opens': _q.value, 'live_pairs': len(live_pairs)},
                 ensure_ascii=False))
# 只收割真开路网(闭合的对进不来)
_live_nets = {p.split('|')[0] for p in live_pairs}
nets = [n for n in nets if n in _live_nets]


def last_json(out):
    lines = [l.strip() for l in (out or '').splitlines()
             if l.strip().startswith('{')]
    return json.loads(lines[-1]) if lines else {}


for net in nets:
    r = subprocess.run(
        [KIPY, str(KG / 'geomir.py'), str(pcb), net,
         str(board_dir / 'plan'), str(pairs)],
        capture_output=True, text=True, encoding='utf-8',
        errors='replace', timeout=2400)
    plan = last_json(r.stdout)
    pths = plan.get('paths') or []
    if not pths:
        print(json.dumps({'net': net, 'closed': False,
                          'why': 'no path', 'rejects':
                          len(plan.get('rejects') or [])}))
        continue
    p0 = pths[0]
    spec = {'net': net, 'pts': p0['pts'], 'width': p0.get('width', 0.2)}
    sp = board_dir / 'direct_link_spec.json'
    sp.write_text(json.dumps(spec), encoding='utf-8')
    r2 = subprocess.run(
        [KIPY, str(KG / 'direct_link.py'), str(pcb), str(sp)],
        capture_output=True, text=True, encoding='utf-8',
        errors='replace', timeout=300)
    out = last_json(r2.stdout)
    print(json.dumps({'net': net, 'len': p0.get('length_mm'),
                      'w': p0.get('width'), **out},
                     ensure_ascii=False))
