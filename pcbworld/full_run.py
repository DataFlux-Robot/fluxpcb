"""Every board of the list, N independent cells of the base arm, through
the ordinary pipeline and the ordinary referee.

    BUDGET=900 WORKERS=6 python full_run.py <run_dir> <label> <reps> [arm]

Prints, per split, against PCBRouteAgent: clean cells / runs, boards
clean every time, boards clean at least once, mean wall, and the same
under the strict criterion (stubs counted).  Nothing here is used by
the scoring: it only reads the cells the bench driver wrote.
"""
import collections
import dataclasses
import json
import os
import statistics
import sys
import time

BENCH = 'E:/DATA/vscode/ZCODE/autorouter-bench/work/pcbworld'
RUN, LABEL, REPS = sys.argv[1], sys.argv[2], int(sys.argv[3])
ARM = sys.argv[4] if len(sys.argv) > 4 else 'gate+ripup'
sys.path.insert(0, BENCH)
sys.path.insert(0, 'E:/DATA/vscode/ZCODE/flux-neurotransmitter')
os.environ.setdefault('KICAD_PYTHON', sys.executable)
os.chdir(BENCH)
import arms_run  # noqa: E402

all_boards = json.load(open(os.path.join(BENCH, 'boards_full.json'), encoding='utf-8'))
gen0 = {a.name: a for a in arms_run.gen0_arms()}
base = gen0[ARM]
budget = int(os.environ.get('BUDGET', '900'))
workers = int(os.environ.get('WORKERS', '6'))
arms = [dataclasses.replace(base, name=f'{ARM}~{LABEL}r{i}') for i in range(REPS)]

PAPER = {'d3a': (99, 94.9), 'd3b': (10, 100.0), 'd3c': (10, 40.0)}
t0 = time.time()
cells, summary = arms_run.evaluate(RUN, all_boards, arms, budget, 240, workers, LABEL)
json.dump(summary, open(os.path.join(RUN, f'summary_{LABEL}.json'), 'w', encoding='utf-8'),
          indent=1, ensure_ascii=False)
print('FULL RUN DONE in %.0f min; cells %d of %d; instrument bad %d' % (
    (time.time() - t0) / 60, len(cells), len(all_boards) * REPS,
    summary.get('instrument_bad_cells', 0)), flush=True)


def table(strict):
    print('=== %s criterion ===' % ('STRICT (stubs counted)' if strict else 'ERROR (the table\'s criterion)'))
    print('%-5s %5s %10s %12s %12s %12s %9s %9s' % (
        'split', 'boards', 'clean/runs', 'ours %', 'PCBRouteAg', 'every time', 'any time', 'mean s', 'max s'))
    for split in ('d3a', 'd3b', 'd3c'):
        mine = [c for c in cells if c['set'] == split]
        by_board = collections.defaultdict(list)
        for c in mine:
            by_board[c['board']].append(c)
        clean = sum(1 for c in mine if (c.get('outcome_strict') if strict else c.get('outcome')) == 'clean')
        every = sum(1 for b, cs in by_board.items() if cs and all(
            (c.get('outcome_strict') if strict else c.get('outcome')) == 'clean' for c in cs))
        anyc = sum(1 for b, cs in by_board.items() if any(
            (c.get('outcome_strict') if strict else c.get('outcome')) == 'clean' for c in cs))
        n = len(by_board)
        walls = [c.get('wall_seconds') or 0 for c in mine]
        print('%-5s %5d %5d/%-5d %11.1f%% %11.1f%% %6d/%-5d %5d/%-6d %9.0f %9.0f' % (
            split.upper(), n, clean, len(mine), 100.0 * clean / max(1, len(mine)),
            PAPER[split][1], every, n, anyc, n, statistics.mean(walls), max(walls)))
    return cells


table(False)
table(True)
print('=== outcomes ===')
print(dict(collections.Counter(c.get('outcome') for c in cells)))
print('=== boards not clean every time (error criterion) ===')
by_board = collections.defaultdict(list)
for c in cells:
    by_board[c['board']].append(c)
for b, cs in sorted(by_board.items()):
    outs = [c.get('outcome') for c in cs]
    if outs != ['clean'] * len(cs):
        s = [(c.get('outcome'), (c.get('score') or {}).get('errors'), (c.get('score') or {}).get('unconnected'),
              c.get('wall_seconds')) for c in cs]
        print('  %-58s %s' % (b[:58], s))
print('=== boards with stubs left (strict only) ===')
for b, cs in sorted(by_board.items()):
    d = [(c.get('score') or {}).get('dangling') for c in cs]
    if any(x for x in d):
        print('  %-58s dangling %s' % (b[:58], d))
print('=== retried router ===', sum(1 for c in cells if ((c.get('cert') or {}) if isinstance(c.get('cert'), dict) else {}) and False))
bad = [c for c in cells if not c.get('instrument_ok')]
print('=== instrument problems ===', [(c['board'][:30], c.get('instrument_problems')) for c in bad][:10])
