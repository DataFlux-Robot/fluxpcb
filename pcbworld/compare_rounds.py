#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Compare full-118 rounds against each other and PCBRouteAgent."""
import argparse
import json
import os

PCBR = {'d3a': 94.9, 'd3b': 100.0, 'd3c': 40.0}
SPLITS = ('d3a', 'd3b', 'd3c', 'all')


def load_round(path):
    rp = os.path.join(path, 'round.json')
    sp = os.path.join(path, 'score_summary.json')
    if not (os.path.exists(rp) and os.path.exists(sp)):
        return None
    return {'round': json.load(open(rp, encoding='utf-8')),
            'summary': json.load(open(sp, encoding='utf-8')),
            'scores': json.load(open(os.path.join(path, 'score.json'),
                                     encoding='utf-8'))
            if os.path.exists(os.path.join(path, 'score.json')) else []}


def flips(a, b):
    sa = {r['dir']: r.get('cleanPass') for r in (a or [])}
    sb = {r['dir']: r.get('cleanPass') for r in (b or [])}
    gained, lost = [], []
    for d in sorted(set(sa) & set(sb)):
        if sb[d] and not sa[d]:
            gained.append(d)
        elif sa[d] and not sb[d]:
            lost.append(d)
    return gained, lost


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--rounds', required=True)
    ap.add_argument('--out', required=True)
    args = ap.parse_args()

    names = sorted(d for d in os.listdir(args.rounds)
                   if os.path.isdir(os.path.join(args.rounds, d))
                   and os.path.exists(os.path.join(args.rounds, d,
                                                   'round.json')))
    data = {}
    for n in names:
        r = load_round(os.path.join(args.rounds, n))
        if r:
            data[n] = r
    out = {'rounds': list(data), 'pcbrouteagent': PCBR, 'table': [],
           'flips': []}
    for n in data:
        row = {'round': n,
               'policy': data[n]['round'].get('policy_digest', '')[:12],
               'delta': data[n]['round'].get('delta'),
               'gate': data[n]['round'].get('gate')}
        for s in SPLITS:
            v = data[n]['summary'].get(s) or {}
            row[s] = {'clean': v.get('clean'), 'n': v.get('n'),
                      'rate': v.get('rate'),
                      'routing_rate': v.get('routing_rate', v.get('rate'))}
            if s in PCBR:
                rate = row[s]['routing_rate']
                row[s]['vs_pcbr'] = (round(rate - PCBR[s], 1)
                                     if rate is not None else None)
        out['table'].append(row)
    for i in range(1, len(data)):
        prev, cur = names[i - 1], names[i]
        gained, lost = flips(data[prev]['scores'], data[cur]['scores'])
        out['flips'].append({'from': prev, 'to': cur,
                             'gained': gained, 'lost': lost})
    json.dump(out, open(args.out, 'w'), indent=1, ensure_ascii=False)
    for row in out['table']:
        cells = []
        for s in SPLITS:
            v = row[s]
            tail = f" ({v['vs_pcbr']:+.1f}pp)" if 'vs_pcbr' in v and \
                v['vs_pcbr'] is not None else ''
            cells.append(f"{s} {v['clean']}/{v['n']}={v['rate']}%{tail}")
        print(row['round'], '|', ' | '.join(cells))
    for f in out['flips']:
        print('flips', f['from'], '->', f['to'],
              'gained', len(f['gained']), 'lost', len(f['lost']))


if __name__ == '__main__':
    main()
