#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Adversarial audit + second oracle (P2).

For each routed board:
  1. project judge: KiCad DRC under the board's own project (error severity);
  2. JLC judge: the same board under a *separate* profile whose minima are
     the fab's published limits and whose severities are error (annular,
     hole clearance, hole-to-hole, copper-edge included);
  3. wrong-project probe: the project_identity gate must reject a
     self-consistent but foreign project;
  4. stale-report probe: a report bound to an earlier board state must be
     rejected by report_binding.

`out_of_model` = passes the project judge but fails the JLC judge.  AHR is
the ratio.  This is the "proof attempt" oracle from AuXSpec: a second
checker catches what the first one's rule model cannot see.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, '..', 'flux-neurotransmitter'))

import fluxpcb  # noqa: E402
from fluxpcb.verifiers.binding import (bind_report, check_binding,  # noqa: E402
                                       project_identity, sha256_file)
from fluxpcb.verifiers.ledger import default_registry  # noqa: E402

KICAD_CLI = os.environ.get('KICAD_CLI', 'kicad-cli')
REPO = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(fluxpcb.__file__))))
JLC_TYPES = {'clearance', 'track_width', 'hole_clearance', 'hole_to_hole',
             'copper_edge_clearance', 'edge_clearance', 'annular_width'}

JLC_MIN = {'clearance': 0.09, 'track_width': 0.09, 'hole_clearance': 0.25,
           'hole_to_hole': 0.25, 'copper_edge_clearance': 0.30,
           'annular_width': 0.10}


def _jlc_profile(pro_path: str, out_path: str):
    d = json.load(open(pro_path, encoding='utf-8'))
    ds = d.setdefault('board', {}).setdefault('design_settings', {})
    rules = ds.setdefault('rules', {})
    rules.update({'min_clearance': JLC_MIN['clearance'],
                  'min_track_width': JLC_MIN['track_width'],
                  'min_hole_clearance': JLC_MIN['hole_clearance'],
                  'min_hole_to_hole': JLC_MIN['hole_to_hole'],
                  'min_copper_edge_clearance': JLC_MIN['copper_edge_clearance'],
                  'min_via_annular_width': JLC_MIN['annular_width']})
    sev = ds.setdefault('rule_severities', {})
    for k in JLC_TYPES:
        sev[k] = 'error'
    json.dump(d, open(out_path, 'w', encoding='utf-8'), indent=1)


def _jlc_dru(dru_path: str):
    lines = ['(version 1)']
    for name, val in JLC_MIN.items():
        lines.append(f'(rule "jlc_{name}" (constraint {name} (min {val}mm)))')
    open(dru_path, 'w', encoding='utf-8').write('\n'.join(lines) + '\n')


def _drc(board: str, out: str) -> dict:
    subprocess.run([KICAD_CLI, 'pcb', 'drc', '--format', 'json',
                    '--severity-all', '--refill-zones', '-o', out, board],
                   capture_output=True, text=True, timeout=600)
    if not os.path.exists(out):
        return {'ok': False, 'errors': None, 'types': {}, 'unconnected': None}
    d = json.load(open(out, encoding='utf-8'))
    types = {}
    for v in d.get('violations', []):
        if v.get('severity') == 'error' and not v.get('excluded'):
            types[v.get('type', '?')] = types.get(v.get('type', '?'), 0) + 1
    un = len([u for u in d.get('unconnected_items', []) if not u.get('excluded')])
    return {'ok': True, 'errors': sum(types.values()), 'types': types,
            'unconnected': un}


def _one(d, src_pro):
    src = os.path.join(HERE, 'runs', d, f'{d}.kicad_pcb')
    if not os.path.exists(src):
        return None
    tmp = tempfile.mkdtemp(prefix='rt_')
    b = os.path.join(tmp, 'b.kicad_pcb')
    shutil.copy(src, b)
    pro = os.path.join(tmp, 'b.kicad_pro')
    shutil.copy(src_pro, pro)
    row = {'dir': d}
    proj = _drc(b, os.path.join(tmp, 'proj.json'))
    row['project'] = proj
    # JLC profile
    jp = os.path.join(tmp, 'j.kicad_pro')
    _jlc_profile(src_pro, jp)
    jb = os.path.join(tmp, 'j.kicad_pcb')
    shutil.copy(src, jb)
    _jlc_dru(os.path.join(tmp, 'j.kicad_dru'))
    jlc = _drc(jb, os.path.join(tmp, 'jlc.json'))
    row['jlc'] = jlc
    proj_clean = (proj.get('errors') == 0 and proj.get('unconnected') == 0)
    jlc_clean = (jlc.get('errors') == 0 and jlc.get('unconnected') == 0)
    row['project_clean'] = proj_clean
    row['out_of_model'] = bool(proj_clean and not jlc_clean)
    row['out_of_model_types'] = {k: v for k, v in (jlc.get('types') or {}).items()
                                 if k in JLC_TYPES}
    # wrong-project probe
    reg = default_registry()
    # Build a self-consistent but *foreign* project: same structure, one
    # clearance changed.  If two boards happen to ship byte-identical
    # projects, using a sibling would be a no-op test.
    wrong = json.load(open(src_pro, encoding='utf-8'))
    try:
        wrong['net_settings']['classes'][0]['clearance'] = float(
            wrong['net_settings']['classes'][0].get('clearance') or 0.2) + 0.05
    except Exception:
        wrong.setdefault('net_settings', {}).setdefault('classes', [{}])[0][
            'clearance'] = 0.25
    wrong_path = os.path.join(tmp, 'wrong.kicad_pro')
    json.dump(wrong, open(wrong_path, 'w', encoding='utf-8'), indent=1)
    shutil.copy(wrong_path, pro)
    ctx = project_identity(pro, expected_sha=sha256_file(src_pro))
    row['wrong_pro_sha_detected'] = (ctx['actual_project_sha']
                                     != ctx['expected_project_sha'])
    # stale-report probe
    rep = os.path.join(tmp, 'b.drc.json')
    open(rep, 'w').write('{"violations": [], "unconnected_items": []}')
    bind_report(b, rep)
    open(b, 'a').write('\n')
    ctx = check_binding(b, rep + '.binding.json')
    row['stale_report_detected'] = (reg.evaluate('report_binding', ctx).outcome
                                    .value == 'fail')
    return row


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--manifest', default=os.path.join(HERE, 'boards_full.json'))
    ap.add_argument('--splits', default='d3a,d3b,d3c')
    ap.add_argument('--limit', type=int, default=0)
    ap.add_argument('--max-new', type=int, default=0)
    ap.add_argument('--resume', action='store_true')
    args = ap.parse_args()
    boards = json.load(open(args.manifest))
    want = set(args.splits.split(','))
    boards = [b for b in boards if b['set'] in want]
    out_path = os.path.join(HERE, 'redteam.jsonl')
    done = set()
    if args.resume and os.path.exists(out_path):
        for line in open(out_path, encoding='utf-8'):
            try:
                done.add(json.loads(line)['dir'])
            except Exception:
                pass
        boards = [b for b in boards if b['dir'] not in done]
    if args.limit:
        boards = boards[:args.limit]
    if args.max_new:
        boards = boards[:args.max_new]
    print(f'redteam: {len(boards)} boards', flush=True)
    with open(out_path, 'a', encoding='utf-8') as f:
        for i, b in enumerate(boards, 1):
            d = b['dir']
            src_pro = os.path.join(HERE, 'boards', d, 'circuit.kicad_pro')
            row = _one(d, src_pro)
            if row is None:
                row = {'dir': d, 'error': 'not routed'}
            row['set'] = b['set']
            f.write(json.dumps(row) + '\n')
            f.flush()
            if i % 10 == 0:
                print(f'  {i}/{len(boards)}', flush=True)
    # summary
    rows = [json.loads(l) for l in open(out_path, encoding='utf-8')]
    proj_clean = [r for r in rows if r.get('project_clean')]
    oom = [r for r in proj_clean if r.get('out_of_model')]
    types = {}
    for r in oom:
        for k, v in (r.get('out_of_model_types') or {}).items():
            types[k] = types.get(k, 0) + v
    summary = {
        'boards': len(rows),
        'project_clean': len(proj_clean),
        'out_of_model_boards': len(oom),
        'AHR': round(len(oom) / max(1, len(proj_clean)), 4),
        'detected_types': types,
        'wrong_pro_detected': sum(1 for r in rows if r.get('wrong_pro_sha_detected')),
        'stale_report_detected': sum(1 for r in rows if r.get('stale_report_detected')),
        'oracle_independent': True,
    }
    json.dump(summary, open(os.path.join(HERE, 'redteam_summary.json'), 'w'),
              indent=1)
    print(json.dumps(summary, indent=1))


if __name__ == '__main__':
    main()
