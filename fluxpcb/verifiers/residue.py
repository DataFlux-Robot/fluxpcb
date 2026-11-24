"""residue — classify DRC violations by whose rule model produced them.

The point of a congruence certificate is that the residue is *accounted
for*, not that it disappears.  A violation is one of:

    out_of_model:no_net_pad   a pad with no net: not in any class, so the
                              DSN cannot carry its clearance
    out_of_model:pad_pad      two pads too close: a placement issue, not a
                              router issue (the router did not draw it)
    out_of_model:pad_override a pad with a local clearance override
    router_fault:<type>       copper the router drew against the table it was
                              given
    unconnected               connectivity the router failed to make
    other:<type>              everything else, named so it can be triaged

Counting these separately is what turns "17 clearance violations" into a
decision: fix the table, fix the operator, or fix the placement.
"""
from __future__ import annotations

import re
from collections import Counter


def _is_pad(item: dict) -> bool:
    d = (item.get('description') or '').lstrip()
    return d.startswith('Pad ') or '焊盘' in d


def _has_net(item: dict) -> bool:
    return '[' in (item.get('description') or '')


def classify_violation(v: dict) -> str:
    t = v.get('type', '?')
    items = v.get('items') or []
    pads = [it for it in items if _is_pad(it)]
    if t == 'clearance':
        no_net = [it for it in pads if not _has_net(it)]
        if no_net:
            return 'out_of_model:no_net_pad'
        if len(pads) >= 2 and len(pads) == len(items):
            return 'out_of_model:pad_pad'
        return 'router_fault:clearance'
    if t in ('hole_clearance', 'hole_to_hole', 'copper_edge_clearance'):
        return f'router_fault:{t}'
    if t == 'track_width':
        return 'router_fault:track_width'
    if t in ('unconnected', 'track_dangling'):
        return 'unconnected'
    if t in ('shorting_items', 'silk_over_copper', 'silk_overlap'):
        return f'other:{t}'
    return f'other:{t}'


def classify_drc(drc: dict) -> dict:
    counts = Counter()
    examples = {}
    for v in drc.get('violations') or []:
        if v.get('severity') != 'error' or v.get('excluded'):
            continue
        c = classify_violation(v)
        counts[c] += 1
        if c not in examples:
            examples[c] = (v.get('description') or '')[:120]
    n_unconn = len([u for u in (drc.get('unconnected_items') or [])
                    if not u.get('excluded')])
    if n_unconn:
        counts['unconnected'] += n_unconn
    total = sum(counts.values())
    out_model = sum(n for k, n in counts.items() if k.startswith('out_of_model'))
    router = sum(n for k, n in counts.items() if k.startswith('router_fault'))
    return {
        'total': total,
        'out_of_model': out_model,
        'router_fault': router,
        'unconnected': counts.get('unconnected', 0),
        'by_class': dict(sorted(counts.items())),
        'examples': examples,
    }
