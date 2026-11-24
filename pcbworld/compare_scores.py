#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Compare two PCBWorld score files, per board and per split."""
import argparse
import collections
import json


def load(path):
    rows = json.load(open(path))
    return {r['dir']: r for r in rows}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('old')
    ap.add_argument('new')
    ap.add_argument('--out', default='')
    args = ap.parse_args()
    old, new = load(args.old), load(args.new)
    dirs = sorted(set(old) | set(new))
    rows = []
    for d in dirs:
        o, n = old.get(d, {}), new.get(d, {})
        od, nd = o.get('drc') or {}, n.get('drc') or {}
        rows.append({
            'dir': d,
            'set': (n or o).get('set'),
            'old_clean': bool(o.get('cleanPass')),
            'new_clean': bool(n.get('cleanPass')),
            'old_err': od.get('errors'), 'new_err': nd.get('errors'),
            'old_un': od.get('unconnected'), 'new_un': nd.get('unconnected'),
        })
    agg = collections.defaultdict(lambda: [0, 0, 0, 0])  # old_clean, new_clean, n, gained
    for r in rows:
        a = agg[r['set']]
        a[0] += 1 if r['old_clean'] else 0
        a[1] += 1 if r['new_clean'] else 0
        a[2] += 1
        a[3] += 1 if (r['new_clean'] and not r['old_clean']) else 0
    summary = {k: {'old': v[0], 'new': v[1], 'n': v[2], 'gained': v[3]}
               for k, v in sorted(agg.items())}
    tot = [sum(v[i] for v in agg.values()) for i in range(4)]
    summary['all'] = {'old': tot[0], 'new': tot[1], 'n': tot[2], 'gained': tot[3]}
    gained = [r for r in rows if r['new_clean'] and not r['old_clean']]
    lost = [r for r in rows if r['old_clean'] and not r['new_clean']]
    print(json.dumps(summary, indent=1))
    print(f'\ngained {len(gained)}: ' + ', '.join(r['dir'][:12] for r in gained))
    print(f'lost   {len(lost)}: ' + ', '.join(r['dir'][:12] for r in lost))
    if args.out:
        json.dump({'summary': summary, 'rows': rows}, open(args.out, 'w'), indent=1)


if __name__ == '__main__':
    main()
