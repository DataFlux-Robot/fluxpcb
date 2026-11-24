"""How long does Freerouting take on one board, and with what result,
across thread counts and pass limits?  (router-timeout study: 0344)

    python router_study.py <board key> <out jsonl> <spec>[,<spec>...]...
    spec = threads x passes x timeout, e.g. 2x15x600 ; repeat a spec to
    sample the router's own randomness.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

REPO = 'E:/DATA/vscode/ZCODE/flux-neurotransmitter'
BOARDS = 'C:/zcode_pcbworld/boards'
sys.path.insert(0, REPO)
KICAD_CLI = os.environ.setdefault('KICAD_CLI', 'C:/Users/zhang/AppData/Local/Programs/KiCad/10.0/bin/kicad-cli.exe')

key, out_path, specs = sys.argv[1], sys.argv[2], sys.argv[3].split(',')
from fluxpcb.router.freerouting import FreeroutingBackend  # noqa: E402

bdir = [d for d in os.listdir(BOARDS) if d.startswith(key)][0]
src = os.path.join(BOARDS, bdir)
work = tempfile.mkdtemp(prefix='rstudy_')
paired = os.path.join(work, 'board.kicad_pcb')
shutil.copy(os.path.join(src, 'circuit.reference.kicicad_pcb' if False else 'circuit.reference.kicad_pcb'), paired)
shutil.copy(os.path.join(src, 'circuit.kicad_pro'), os.path.join(work, 'board.kicad_pro'))

for spec in specs:
    th, mp, tmo = (int(x) for x in spec.split('x'))
    out = os.path.join(work, 'out.kicad_pcb')
    for stale in (out, out + '.drc.json'):
        if os.path.exists(stale):
            os.remove(stale)
    shutil.copy(os.path.join(src, 'circuit.kicad_pro'), os.path.join(work, 'out.kicad_pro'))
    backend = FreeroutingBackend(threads=th, max_passes=mp)
    t0 = time.time()
    try:
        fr = backend.route(paired, out, timeout_s=tmo, use_board_rules=True)
    except Exception as e:                        # noqa: BLE001
        fr = None
        err = '%s: %s' % (type(e).__name__, e)
    wall = time.time() - t0
    row = {'board': key, 'threads': th, 'passes_limit': mp, 'timeout': tmo, 'wall': round(wall, 1)}
    if fr is not None:
        row.update({'routed': fr.routed, 'seconds': round(fr.seconds or 0, 1), 'passes': fr.passes,
                    'timed_out': fr.timed_out, 'error': fr.error, 'rule_congruent': fr.rule_congruence})
        if fr.routed and os.path.exists(out):
            shutil.copy(os.path.join(src, 'circuit.kicad_pro'), os.path.join(work, 'out.kicad_pro'))
            subprocess.run([KICAD_CLI, 'pcb', 'drc', '--format', 'json', '--severity-all', '--refill-zones',
                            '--output', out + '.drc.json', out], capture_output=True, timeout=900)
            if os.path.exists(out + '.drc.json'):
                d = json.load(open(out + '.drc.json', encoding='utf-8'))
                errs = [v for v in d.get('violations', []) if v.get('severity') == 'error' and not v.get('excluded')]
                row['errors'] = len(errs)
                row['by_type'] = {}
                for v in errs:
                    row['by_type'][v.get('type')] = row['by_type'].get(v.get('type'), 0) + 1
                row['unconnected'] = len([u for u in d.get('unconnected_items', []) if not u.get('excluded')])
                shutil.copy(os.path.join(src, 'circuit.kicad_pro'), os.path.join(work, 'out.kicad_pro'))
    else:
        row['error'] = err
    with open(out_path, 'a', encoding='utf-8') as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + '\n')
    print(json.dumps(row, ensure_ascii=False), flush=True)
print('STUDY DONE', flush=True)
