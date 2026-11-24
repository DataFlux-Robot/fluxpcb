"""Markdown tables of a full run, from the cells the bench driver wrote.

    python table_md.py <run_dir> <label>

Per split against PCBRouteAgent, per failing board, stubs left, the
router retries, wall times.  Reads only cell records; nothing is scored
here.
"""
import collections
import glob
import json
import os
import statistics
import sys

RUN, LABEL = sys.argv[1], sys.argv[2]
PAPER = {'d3a': ('D3-A', 99, 94.9), 'd3b': ('D3-B', 10, 100.0), 'd3c': ('D3-C', 10, 40.0)}
cells = []
for f in glob.glob(os.path.join(RUN, 'cells', '*.json')):
    try:
        cells.append(json.load(open(f, encoding='utf-8')))
    except ValueError:
        pass
cells = [c for c in cells if c.get('arm', '').endswith('~' + LABEL + 'r0') or LABEL in c.get('arm', '')]
print('cells: %d' % len(cells))


def outcome(c, strict):
    return c.get('outcome_strict') if strict else c.get('outcome')


def table(strict):
    print('| 集合 | 板数 | 我们（格子） | 我们的板 | PCBRouteAgent |' if not strict else '', end='\n' if not strict else '')
    rows = []
    for split in ('d3a', 'd3b', 'd3c'):
        mine = [c for c in cells if c['set'] == split]
        by = collections.defaultdict(list)
        for c in mine:
            by[c['board']].append(c)
        clean = sum(1 for c in mine if outcome(c, strict) == 'clean')
        every = sum(1 for b, cs in by.items() if cs and all(outcome(c, strict) == 'clean' for c in cs))
        anyc = sum(1 for b, cs in by.items() if any(outcome(c, strict) == 'clean' for c in cs))
        name, n, paper = PAPER[split]
        rows.append((name, n, '%d/%d，%.1f%%' % (clean, len(mine), 100.0 * clean / max(1, len(mine))),
                     '%d/%d 每次都过；%d/%d 至少一次' % (every, n, anyc, n), '%.1f%%' % paper,
                     statistics.mean(c.get('wall_seconds') or 0 for c in mine),
                     max(c.get('wall_seconds') or 0 for c in mine)))
    return rows


for strict in (False, True):
    print('\n=== %s ===' % ('strict（线头计入）' if strict else 'error（对比表的口径）'))
    print('| 集合 | 板数 | 我们（格子） | 我们的板 | PCBRouteAgent | 平均墙钟 s | 最长 s |')
    print('|---|---|---|---|---|---|---|')
    for r in table(strict):
        print('| %s | %d | %s | %s | %s | %.0f | %.0f |' % r)

print('\n=== 结果分布 ===')
print(dict(collections.Counter(c.get('outcome') for c in cells)), '| strict:', dict(collections.Counter(c.get('outcome_strict') for c in cells)))
by = collections.defaultdict(list)
for c in cells:
    by[c['board']].append(c)
print('\n=== 不是每次都过的板（error 口径）===')
for b, cs in sorted(by.items()):
    outs = [outcome(c, False) for c in cs]
    if outs != ['clean'] * len(cs):
        print('- %s（%s）：%s' % (b, cs[0]['set'].upper(), [
            (outcome(c, False), (c.get('score') or {}).get('errors'), (c.get('score') or {}).get('unconnected'),
             'd%d' % ((c.get('score') or {}).get('dangling') or 0), c.get('wall_seconds')) for c in sorted(cs, key=lambda c: c['arm'])]))
print('\n=== strict 口径下不是每次都过的板 ===')
for b, cs in sorted(by.items()):
    outs = [outcome(c, True) for c in cs]
    if outs != ['clean'] * len(cs):
        print('- %s（%s）：%s' % (b, cs[0]['set'].upper(), [
            (outcome(c, True), (c.get('score') or {}).get('dangling')) for c in sorted(cs, key=lambda c: c['arm'])]))
retried = slow = 0
for c in cells:
    p = c.get('cert')
    if not p:
        continue
    try:
        cert = json.load(open(os.path.join(RUN, p), encoding='utf-8'))
    except (OSError, ValueError):
        continue
    a = (cert.get('route') or {}).get('attempts') or []
    if len(a) > 1:
        retried += 1
print('\n=== 其它 ===')
print('路由器重试的格子：%d' % retried)
print('instrument 有问题的格子：%d' % sum(1 for c in cells if not c.get('instrument_ok')))
walls = sorted(((c.get('wall_seconds') or 0), c['board'][:40]) for c in cells)[-6:]
print('最慢的格子：%s' % walls)
print('预算 %s s；中位墙钟 %.0f s' % (cells[0].get('budget_s') if cells else None, statistics.median(c.get('wall_seconds') or 0 for c in cells)))
