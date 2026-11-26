"""Connectivity certificates: a spanning tree per net, over item ids.

The kernel checks every edge with its own geometry and verifies that the tree
reaches every pad.  A tree that misses a pad is `unknown` (no witness), not
`refuted` — the search may yet find a path.
"""
from __future__ import annotations

from . import geom


def _adjacency(items):
    n = len(items)
    adj = {i['id']: [] for i in items}
    for a in range(n):
        for b in range(a + 1, n):
            ia, ib = items[a], items[b]
            if ia['net'] != ib['net']:
                continue
            if not any(l in ib['layers'] for l in ia['layers']):
                continue
            if geom.bbox_gap(geom.bbox(ia['shape']), geom.bbox(ib['shape'])) > 0:
                continue
            if geom.item_touch(ia, ib):
                adj[ia['id']].append(ib['id'])
                adj[ib['id']].append(ia['id'])
    return adj


def connectivity_certs(st):
    """One tree certificate per net with >= 2 pads."""
    by_net = {}
    for it in st['items']:
        by_net.setdefault(it['net'], []).append(it)
    certs = []
    for net, items in sorted(by_net.items()):
        pads = [p for p in st['netPads'].get(net, []) if p]
        if len(pads) < 2:
            continue
        adj = _adjacency(items)
        start = pads[0]
        seen = {start}
        parent = {}
        frontier = [start]
        while frontier:
            nxt = []
            for x in frontier:
                for y in adj.get(x, []):
                    if y not in seen:
                        seen.add(y)
                        parent[y] = x
                        nxt.append(y)
            frontier = nxt
        edges = [[parent[y], y] for y in seen if y in parent]
        certs.append({'claim': f'CONN:{net}', 'kind': 'tree', 'edges': edges,
                      'reached': len(pads) - sum(1 for p in pads if p not in seen),
                      'missing': [p for p in pads if p not in seen]})
    return certs
