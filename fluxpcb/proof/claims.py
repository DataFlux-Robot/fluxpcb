"""Compile a board's design requirement into conjectures.

For the benchmark boards the requirement is the criterion itself: every net
connected, the board netlist unchanged, and the fab rules met.  For a project
spec (later milestone) the requirements/interfaces/power blocks feed the same
`Claim` shape.
"""
from __future__ import annotations


def bench_claims(st, ref_nets=None, include_fab=True):
    claims = []
    for net, pads in sorted(st['netPads'].items()):
        if len(pads) < 2:
            continue
        scope = sorted(i['id'] for i in st['items'] if i['net'] == net)
        claims.append({'id': f'CONN:{net}', 'pred': 'connected', 'net': net,
                       'pads': list(pads), 'scope': scope})
    expect = ref_nets if ref_nets is not None else st['netPads']
    claims.append({'id': 'NETLIST', 'pred': 'netlist',
                   'expect': {n: sorted(v) for n, v in expect.items()},
                   'scope': sorted(i['id'] for i in st['items'])})
    if include_fab:
        allids = sorted(i['id'] for i in st['items'])
        claims.append({'id': 'FAB:CLEARANCE', 'pred': 'clearance',
                       'scope': allids, 'rule': st['rules'].get('clearance')})
        claims.append({'id': 'FAB:WIDTH', 'pred': 'width',
                       'scope': allids, 'rule': st['rules'].get('track_min')})
    return claims


def conjecture_table(st, ref_nets=None):
    """The requirement -> conjecture mapping as rows (for reports/pages)."""
    rows = []
    for net, pads in sorted(st['netPads'].items()):
        if len(pads) >= 2:
            rows.append({'requirement': 'bench:connectivity', 'claim': f'CONN:{net}',
                         'statement': f'net {net}: {len(pads)} pads connected',
                         'kind': 'existential'})
    rows.append({'requirement': 'bench:netlist', 'claim': 'NETLIST',
                 'statement': 'board netlist == reference netlist',
                 'kind': 'universal'})
    rows.append({'requirement': 'fab:clearance', 'claim': 'FAB:CLEARANCE',
                 'statement': 'all different-net copper >= clearance',
                 'kind': 'universal'})
    rows.append({'requirement': 'fab:width', 'claim': 'FAB:WIDTH',
                 'statement': 'all tracks >= min width', 'kind': 'universal'})
    return rows
