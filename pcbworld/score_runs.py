#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Score routed boards with KiCad's own DRC (the benchmark's camera).

Matches the metric definition used for the published numbers: error-severity
violations under the board's own project, zones refilled; clean pass =
0 errors AND 0 unconnected.

Usage:
    python work/pcbworld/score_runs.py [--manifest boards_full.json]
                                       [--runs runs] [--out score.json]
"""
import argparse
import collections
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
KICAD_CLI = os.environ.get('KICAD_CLI', 'kicad-cli')

# Acceptance axes: routing is what a router can own; library/silk are
# separate manufacturability classes and must not pollute routing rates.
ROUTING_TYPES = frozenset({
    'clearance', 'track_width', 'shorting_items', 'unconnected',
    'via_dangling', 'track_dangling', 'via_diameter', 'annular_width',
    'hole_clearance', 'hole_to_hole', 'copper_edge_clearance'})
LIBRARY_TYPES = frozenset({
    'lib_footprint_issues', 'lib_footprint_mismatch',
    'footprint_type_mismatch', 'courtyard', 'padstack'})
SILK_TYPES = frozenset({
    'silk_over_copper', 'silk_overlap', 'silk_edge_clearance',
    'text_height', 'text_thickness'})


# what the DRC calls a track or via nothing is connected to at one end
DANGLING = ('track_dangling', 'via_dangling')


def axis_of(vtype: str) -> str:
    if vtype in ROUTING_TYPES:
        return 'routing'
    if vtype in LIBRARY_TYPES:
        return 'library'
    if vtype in SILK_TYPES:
        return 'silk'
    return 'other'


def score_one(pcb: str, pro: str) -> dict:
    # kicad-cli pairs a board with the project of the same basename
    stem = os.path.splitext(pcb)[0]
    # Repair-generated waivers must never judge the board.  Quarantine any
    # .kicad_dru beside the run artifact before scoring.
    import glob as _glob
    for _dru in _glob.glob(os.path.join(os.path.dirname(pcb), '*.kicad_dru')):
        try:
            os.replace(_dru, _dru + '.quarantined')
        except OSError:
            pass
    # The source project is authoritative: always judge against it, never
    # against whatever a save happened to leave beside the board.
    if os.path.exists(pro):
        import shutil
        shutil.copy(pro, stem + '.kicad_pro')
    rep = pcb + '.score.drc.json'
    subprocess.run([KICAD_CLI, 'pcb', 'drc', '--format', 'json',
                    '--severity-error', '--refill-zones', '-o', rep, pcb],
                   capture_output=True, text=True, timeout=1800)
    if not os.path.exists(rep):
        return {'errors': None, 'errorsByType': {}, 'unconnected': None,
                'error': 'no DRC report'}
    with open(rep, encoding='utf-8') as f:
        d = json.load(f)
    by = {}
    for v in d.get('violations', []):
        if v.get('severity') == 'error' and not v.get('excluded'):
            by[v.get('type', '?')] = by.get(v.get('type', '?'), 0) + 1
    unconn = len([u for u in d.get('unconnected_items', [])
                  if not u.get('excluded')])
    axis = {'routing': 0, 'library': 0, 'silk': 0, 'other': 0}
    for t, n in by.items():
        axis[axis_of(t)] += n
    axis['routing'] += unconn
    # The strict criterion adds what the error criterion cannot see: a
    # track or via nothing is connected to at one end is a warning.  The
    # error report is made with --severity-error on purpose (it is the
    # criterion the comparison table uses); the stubs come from a second
    # report of the same board.  Both runs of one DRC take a second.
    strict_rep = pcb + '.score.drc.all.json'
    dangling = None
    try:
        subprocess.run([KICAD_CLI, 'pcb', 'drc', '--format', 'json',
                        '--severity-all', '--refill-zones', '-o', strict_rep, pcb],
                       capture_output=True, text=True, timeout=1800)
        with open(strict_rep, encoding='utf-8') as f:
            da = json.load(f)
        dangling = len([v for v in da.get('violations', [])
                        if v.get('type') in DANGLING and not v.get('excluded')])
    except (OSError, ValueError, subprocess.SubprocessError):
        dangling = None                     # unknown is never "none"
    finally:
        if os.path.exists(strict_rep):
            os.remove(strict_rep)
    out = {'errors': sum(by.values()), 'errorsByType': by,
           'unconnected': unconn, 'axis': axis,
           'cleanPassRouting': axis['routing'] == 0,
           'dangling': dangling}
    if dangling is not None:
        out['strictErrors'] = out['errors'] + unconn + dangling
        out['cleanPassStrict'] = out['strictErrors'] == 0
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--manifest', default=os.path.join(HERE, 'boards_full.json'))
    ap.add_argument('--runs', default=os.path.join(HERE, 'runs'))
    ap.add_argument('--out', default=os.path.join(HERE, 'score_new.json'))
    ap.add_argument('--limit', type=int, default=0)
    ap.add_argument('--splits', default='')
    ap.add_argument('--resume', action='store_true')
    ap.add_argument('--max-new', type=int, default=0)
    args = ap.parse_args()

    boards = json.load(open(args.manifest))
    if args.splits:
        want = set(args.splits.split(','))
        boards = [b for b in boards if b['set'] in want]
    if args.limit:
        boards = boards[:args.limit]
    rows = []
    if args.resume and os.path.exists(args.out):
        rows = json.load(open(args.out))
        # Resume must retry rows without a real DRC result; skipping
        # 'not routed' rows silently froze the R2 0496 rescore.
        done = {r['dir'] for r in rows
                if (r.get('drc') or {}).get('errors') is not None}
        boards = [b for b in boards if b['dir'] not in done]
        print(f'resume: {len(done)} scored, {len(boards)} to go', flush=True)
    if args.max_new:
        boards = boards[:args.max_new]
    for b in boards:
        d = b['dir']
        pcb = os.path.join(args.runs, d, f'{d}.kicad_pcb')
        pro = os.path.join(HERE, 'boards', d, 'circuit.kicad_pro')
        if not os.path.exists(pcb):
            rows.append({**b, 'drc': {'errors': None, 'unconnected': None,
                                      'error': 'not routed'}, 'cleanPass': False})
            print(f'{d[:40]}: MISSING', flush=True)
            continue
        drc = score_one(pcb, pro)
        clean = (drc.get('errors') == 0 and drc.get('unconnected') == 0)
        routing_clean = bool(drc.get('cleanPassRouting', clean))
        rows.append({**b, 'drc': drc, 'cleanPass': clean,
                     'cleanPassRouting': routing_clean})
        print(f"{d[:44]}: errors={drc.get('errors')} "
              f"unconn={drc.get('unconnected')} clean={clean} "
              f"routing={routing_clean}", flush=True)

    agg = collections.defaultdict(lambda: [0, 0])
    for r in rows:
        a = agg[r['set']]
        a[1] += 1
        a[0] += 1 if r.get('cleanPass') else 0
    summary = {}
    for k, v in sorted(agg.items()):
        sub = [r for r in rows if r['set'] == k]
        rc = sum(1 for r in sub if r.get('cleanPassRouting'))
        summary[k] = {'clean': v[0], 'n': v[1],
                      'rate': round(100.0 * v[0] / v[1], 1),
                      'routing_clean': rc,
                      'routing_rate': round(100.0 * rc / v[1], 1)}
    total = [sum(v[0] for v in agg.values()), sum(v[1] for v in agg.values())]
    rc = sum(1 for r in rows if r.get('cleanPassRouting'))
    summary['all'] = {'clean': total[0], 'n': total[1],
                      'rate': round(100.0 * total[0] / total[1], 1),
                      'routing_clean': rc,
                      'routing_rate': round(100.0 * rc / total[1], 1)}
    tmp = args.out + '.tmp'
    json.dump(rows, open(tmp, 'w'), indent=1)
    os.replace(tmp, args.out)
    summary_path = args.out.replace('.json', '_summary.json')
    json.dump(summary, open(summary_path, 'w'), indent=1)
    print(json.dumps(summary, indent=1))


if __name__ == '__main__':
    main()
