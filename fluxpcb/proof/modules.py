"""Modules: a contract, a scoped proof, and a merge.

A module is designed (and proved) independently: its contract names the
footprints and derives which nets are internal (every pad inside the module)
and which are boundary (pads on both sides).  The module proof covers its
internal connectivity, its boundary pads, and its local fab geometry; it does
not claim anything about the shared nets.

The merge is where the shared nets are proved: boundary pads from both sides
must be disjoint and together equal the full board's pads for that net, and
the full state's connectivity/netlist/fab must verify.  That is the
composition obligation the parallel design rests on.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field

from ..kernels import fab
from . import certs, claims as claim_mod, gate, state as S


@dataclass
class Contract:
    name: str
    refs: list = field(default_factory=list)
    boundary: list = field(default_factory=list)   # nets with pads on both sides
    internal: list = field(default_factory=list)   # nets entirely inside
    region: list | None = None

    def as_dict(self):
        return {'name': self.name, 'refs': list(self.refs),
                'boundary': list(self.boundary), 'internal': list(self.internal),
                'region': self.region}


def derive(st, name, refs, region=None):
    refs = set(refs)
    pads = {}
    for it in st['items']:
        if 'ref' not in it:
            continue
        pads.setdefault(it['net'], []).append(it['ref'])
    # anonymous no-net copper is not a net: it belongs to the module's fab
    # scope, never to its boundary contract
    touched = sorted(n for n in pads if n in st['netPads']
                     and any(r in refs for r in pads[n]))
    internal, boundary = [], []
    for n in touched:
        if all(r in refs for r in pads[n]) and len(st['netPads'].get(n, [])) >= 1:
            internal.append(n)
        elif any(r in refs for r in pads[n]):
            boundary.append(n)
    return Contract(name=name, refs=sorted(refs), boundary=boundary,
                    internal=internal, region=region)


def load(st, path):
    with open(path, encoding='utf-8') as f:
        d = json.load(f)
    c = derive(st, d.get('name') or 'module', d['refs'], d.get('region'))
    for key in ('boundary', 'internal'):
        want = d.get(key)
        if want is not None and sorted(want) != sorted(getattr(c, key)):
            raise ValueError(f"{c.name}: contract {key} {sorted(want)} != derived "
                             f"{sorted(getattr(c, key))}")
    return c


def module_state(st, contract):
    """The module's own copper: its pads, plus the tracks/vias of internal nets."""
    refs = set(contract.refs)
    internal = set(contract.internal)
    items = []
    for it in st['items']:
        if 'ref' in it:
            if it['ref'] in refs:
                items.append(it)
        elif it['net'] in internal:
            items.append(it)
    net_pads = {}
    for it in items:
        if 'ref' in it and it['net']:
            net_pads.setdefault(it['net'], []).append(it['id'])
    mst = {'schema': st['schema'], 'board': f"{st['board']}#{contract.name}",
           'layers': st['layers'], 'items': items, 'rules': st['rules'],
           'netPads': {n: sorted(v) for n, v in sorted(net_pads.items())}}
    return S.freeze(mst)


def module_claims(mst, contract):
    cl = []
    for net in contract.internal:
        pads = mst['netPads'].get(net, [])
        if len(pads) >= 2:
            cl.append({'id': f'CONN:{net}', 'pred': 'connected', 'net': net,
                       'pads': list(pads),
                       'scope': sorted(i['id'] for i in mst['items']
                                       if i['net'] == net)})
    for net in contract.boundary:
        pads = mst['netPads'].get(net, [])
        if pads:
            cl.append({'id': f'PADS:{net}', 'pred': 'pads', 'net': net,
                       'pads': list(pads),
                       'scope': list(pads)})
    allids = sorted(i['id'] for i in mst['items'])
    cl.append({'id': 'FAB:CLEARANCE', 'pred': 'clearance', 'scope': allids,
               'rule': mst['rules'].get('clearance')})
    cl.append({'id': 'FAB:WIDTH', 'pred': 'width', 'scope': allids,
               'rule': mst['rules'].get('track_min')})
    return cl


def prove(board, contract, rules):
    """One module's proof: scoped state -> claims -> certify -> kernel."""
    t = time.time()
    st = S.extract(board, rules)
    mst = module_state(st, contract)
    cl = module_claims(mst, contract)
    cs = [c for c in certs.connectivity_certs(mst)
          if c['claim'].split(':', 1)[1] in set(contract.internal)]
    r = gate.verify(mst, cl, cs,
                    obligations={c['id']: c['pred'] for c in cl})
    theorem = {'id': f"{contract.name}@{S.board_fp(mst)[:8]}",
               'contract': contract.as_dict(), 'claims': cl,
               'digest': mst['digest'], 'board_fp': S.board_fp(mst),
               'lemma_refs': ['connectivity', 'pads', 'fab.clearance',
                              'fab.width', 'frame']}
    return {'module': contract.name, 'contract': contract.as_dict(),
            'theorem': theorem,
            'board': str(board), 'digest': mst['digest'],
            'ok': r['ok'], 'summary': gate.report_summary(r),
            'claims': [{'id': c['id'], 'status': c['status'],
                        'witness': c['witness']}
                       for c in r['lean']['claims'] + r['fab']['claims']],
            'pads': {n: mst['netPads'].get(n, []) for n in contract.boundary},
            'seconds': round(time.time() - t, 2)}


def check_boundary(st, proofs, contracts):
    """Pads on a boundary net must partition the board's pads for that net."""
    reasons = []
    for net in sorted({n for c in contracts for n in c.boundary}):
        sides = [p['pads'].get(net, []) for p in proofs]
        flat = [x for side in sides for x in side]
        if len(flat) != len(set(flat)):
            reasons.append(f'{net}: duplicate pads across modules')
        want = set(st['netPads'].get(net, []))
        got = set(flat)
        if got != want:
            miss = sorted(want - got)
            extra = sorted(got - want)
            reasons.append(f'{net}: boundary pads != board pads '
                           f'(missing {len(miss)}, extra {len(extra)})')
    return reasons


def merge(board, contract_paths, rules, proofs=None, theorems=None, state=None):
    """Compose: prove the shared nets on the full board + check the boundary.

    `theorems` are the ModuleProof theorems: each is validated against the
    module state recomputed from the current board (digest + board_fp), and
    its internal-net claims are then *carried* — the merge does not re-derive
    certificates the module already proved.
    """
    t = time.time()
    st = state if state is not None else S.extract(board, rules)
    contracts = [load(st, p) for p in contract_paths]
    if proofs is None:
        proofs = [prove(board, c, rules) for c in contracts]
    reasons = []
    covered, carried = set(), []
    if theorems:
        by_name = {}
        for th in theorems:
            name = (th.get('contract') or {}).get('name')
            by_name[name] = th
        for c in contracts:
            th = by_name.get(c.name)
            if th is None:
                reasons.append(f'{c.name}: no theorem supplied')
                continue
            mst = module_state(st, c)
            if th.get('board_fp') != S.board_fp(mst) or                     th.get('digest') != mst['digest']:
                reasons.append(f'{c.name}: stale theorem (module state changed)')
                continue
            for cl_ in th.get('claims') or []:
                if cl_['pred'] == 'connected':
                    covered.add(cl_['id'])
                    carried.append((cl_['id'], list(cl_.get('scope') or [])))
    cl = []
    for c in contracts:
        mst = module_state(st, c)
        for net in c.boundary:
            pads = mst['netPads'].get(net, [])
            if pads:
                cl.append({'id': f'PADS:{c.name}:{net}', 'pred': 'pads',
                           'net': net, 'pads': list(pads), 'scope': list(pads)})
    for net, pads in sorted(st['netPads'].items()):
        if len(pads) >= 2:
            cl.append({'id': f'CONN:{net}', 'pred': 'connected', 'net': net,
                       'pads': list(pads),
                       'scope': sorted(i['id'] for i in st['items'] if i['net'] == net)})
    cl += claim_mod.bench_claims(st, include_fab=True)[-3:]
    cs = [c for c in certs.connectivity_certs(st)
          if c['claim'] not in covered]
    r = gate.verify(st, cl, cs, carried=carried,
                    obligations={c['id']: c['pred'] for c in cl})
    reasons += check_boundary(st, proofs, contracts)
    return {'ok': r['ok'] and not reasons, 'reasons': r['reasons'] + reasons,
            'carried': len(carried), 'reused': sorted(covered),
            'boundary': {n: sum(len(p['pads'].get(n, [])) for p in proofs)
                         for n in sorted({x for c in contracts for x in c.boundary})},
            'summary': gate.report_summary(r),
            'modules': [{'name': p['module'], 'ok': p['ok'],
                         'summary': p.get('summary'),
                         'seconds': p.get('seconds')}
                        for p in proofs],
            'seconds': round(time.time() - t, 2)}
