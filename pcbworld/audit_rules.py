#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Audit rule-model congruence across PCBWorld-Bench boards.

For each board:
  1. pair the reference board with its project (same basename);
  2. strip -> board.dsn (KiCad's export, exactly what the router receives);
  3. audit project table vs DSN table          (pre)
  4. patch + read-back verify                  (T1-A)
  5. audit again                               (post)

Writes audit/rows.jsonl + audit/summary.json.  No routing, no Java.
"""
import json
import os
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, '..', 'flux-neurotransmitter'))
sys.path.insert(0, os.path.join(ROOT, 'scripts'))

from common import KICAD_PYTHON  # noqa: E402
os.environ.setdefault('KICAD_PYTHON', KICAD_PYTHON)

from fluxpcb.router.freerouting import FreeroutingBackend  # noqa: E402

AUD = os.path.join(HERE, 'audit')
os.makedirs(AUD, exist_ok=True)
ROWS = os.path.join(AUD, 'rows.jsonl')
HELPER = os.path.join(ROOT, 'scripts', 'kicad_board.py')


def main():
    manifest = os.path.join(HERE, 'boards_full.json')
    if not os.path.exists(manifest):
        manifest = os.path.join(HERE, 'boards.json')
    boards = json.load(open(manifest))
    only = set(sys.argv[1].split(',')) if len(sys.argv) > 1 else None
    if only:
        boards = [b for b in boards if b['dir'] in only or b['id'] in only]
    done = set()
    if os.path.exists(ROWS):
        with open(ROWS, encoding='utf-8') as f:
            for line in f:
                try:
                    done.add(json.loads(line)['dir'])
                except Exception:
                    pass
    todo = [b for b in boards if b['dir'] not in done]
    print(f'audit: {len(todo)} to do, {len(done)} cached', flush=True)

    be = FreeroutingBackend()
    t0 = time.time()
    with open(ROWS, 'a', encoding='utf-8') as out:
        for i, b in enumerate(todo, 1):
            d = b['dir']
            src = os.path.join(HERE, 'boards', d)
            work = os.path.join(AUD, 'work', d)
            os.makedirs(work, exist_ok=True)
            board = os.path.join(work, 'board.kicad_pcb')
            pro = os.path.join(work, 'board.kicad_pro')
            shutil.copy(os.path.join(src, 'circuit.reference.kicad_pcb'), board)
            shutil.copy(os.path.join(src, 'circuit.kicad_pro'), pro)
            dsn = os.path.join(work, 'board.dsn')
            row = {'dir': d, 'set': b['set'], 'id': b['id']}
            try:
                r = subprocess.run(
                    [KICAD_PYTHON, HELPER, 'strip', board, work],
                    capture_output=True, text=True, timeout=600)
                if not os.path.exists(dsn):
                    row['error'] = f'no dsn: {r.stderr[:200]}'
                else:
                    pre = be.audit_rules(board, dsn)
                    row['pre_ok'] = pre.get('ok')
                    row['pre_mismatch'] = pre.get('mismatch_count', 0)
                    row['kinds'] = sorted({m['kind'] for m in pre.get('mismatches', [])})
                    row['nets'] = pre.get('project_nets')
                    row['out_of_model'] = pre.get('out_of_model', [])
                    ok = be._patch_dsn_from_project(board, dsn)
                    row['patched'] = bool(ok)
                    row['patch_notes'] = (be.last_patch or {}).get('notes')
                    post = be.audit_rules(board, dsn)
                    row['post_ok'] = post.get('ok')
                    row['post_mismatch'] = post.get('mismatch_count', 0)
                    row['post_kinds'] = sorted({m['kind'] for m in post.get('mismatches', [])})
            except Exception as e:
                row['error'] = str(e)
            out.write(json.dumps(row) + '\n')
            out.flush()
            print(f"[{i}/{len(todo)}] {d}: pre_ok={row.get('pre_ok')} "
                  f"post_ok={row.get('post_ok')} kinds={row.get('kinds')} "
                  f"tot={time.time()-t0:.0f}s", flush=True)

    rows = [json.loads(l) for l in open(ROWS, encoding='utf-8')]
    summary = {
        'boards': len(rows),
        'errors': sum(1 for r in rows if r.get('error')),
        'pre_congruent': sum(1 for r in rows if r.get('pre_ok')),
        'pre_divergent': sum(1 for r in rows if r.get('pre_ok') is False),
        'patched': sum(1 for r in rows if r.get('patched')),
        'post_congruent': sum(1 for r in rows if r.get('post_ok')),
        'post_divergent': sum(1 for r in rows if r.get('post_ok') is False),
        'kind_histogram': {},
        'by_set': {},
    }
    for r in rows:
        for k in r.get('kinds') or []:
            summary['kind_histogram'][k] = summary['kind_histogram'].get(k, 0) + 1
        s = summary['by_set'].setdefault(r['set'], {'n': 0, 'pre_ok': 0, 'post_ok': 0})
        s['n'] += 1
        s['pre_ok'] += 1 if r.get('pre_ok') else 0
        s['post_ok'] += 1 if r.get('post_ok') else 0
    json.dump(summary, open(os.path.join(AUD, 'summary.json'), 'w'), indent=1)
    print(json.dumps(summary, indent=1))


if __name__ == '__main__':
    main()
