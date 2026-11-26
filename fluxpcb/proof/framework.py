"""The framework entry point: one certificate per board.

`certify` is the whole loop in one call — extract, claims, certificates, Lean
kernel, fab kernel, the profile's domain kernels, and (optionally) a scoped
route as a gated Step.  The certificate is the artifact a run leaves behind:
its digest, its profile, every claim's status and witness, the per-item hashes
a later `maintain` diffs against, and the timings.

`maintain` re-proves a prior certificate against the current board: only the
claims whose scope was touched are re-verified, the rest are carried and the
Lean kernel checks the carried scopes.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from . import (certs, claims as claim_mod, conjectures, gate, router,
               spec as spec_mod, state as S)

SCHEMA = 'fluxpcb-cert-2'


def _rows(r):
    rows = []
    for src, kernel in ((r['lean'].get('claims', []), 'lean'),
                        (r['fab']['claims'], 'fab'),
                        (r['domain']['claims'], 'domain')):
        for c in src:
            rows.append({'id': c['id'], 'kernel': kernel,
                         'status': c['status'],
                         'witness': c.get('witness', ''),
                         'measured': c.get('measured'),
                         'units': c.get('units', '')})
    return rows


def certify(board, rules=None, profile='lean+fab', domain_claims=(),
            route_nets=None, workdir=None, spec=None, store=None,
            process=None, robust=None, assembly=False,
            assembly_strict=False, trust=False, trust_strict=False,
            log=print):
    t0 = time.time()
    board = Path(board)
    if rules is None:
        rules, rule_src = S.board_rules(board)
    else:
        rule_src = {'clearance': 'explicit', 'track_min': 'explicit'}
    if route_nets:
        route = router.route_scope(board, route_nets, rules, workdir=workdir,
                                   profile=profile,
                                   domain_claims=domain_claims, spec=spec,
                                   log=log)
        return {'schema': SCHEMA, 'board': str(board),
                'digest': route.get('digest'), 'profile': profile,
                'rules': dict(rules), 'rule_source': rule_src,
                'ok': bool(route.get('ok')),
                'reasons': route.get('reasons') or
                ([route['error']] if route.get('error') else []),
                'summary': route.get('summary', {}),
                'claims': route.get('claims', []),
                'unformalized': route.get('unformalized', []), 'route': route,
                'verify_seconds': route.get('seconds'),
                'seconds': round(time.time() - t0, 1)}
    st = S.extract(board, rules)
    dom = list(domain_claims or ())
    unformalized = []
    if spec:
        more, unformalized = conjectures.from_spec(spec, st)
        dom += more
    bench = claim_mod.bench_claims(st)
    carried = []
    check = []
    ih = S.item_hashes(st)
    for c in bench:
        hit = store.get(c, st, ih) if store is not None else None
        if hit is not None and c['pred'] not in ('clearance', 'width'):
            carried.append((c['id'], list(c.get('scope') or [])))
        else:
            check.append(c)
    proc = claim_mod.process_claims(st, process) if process else []
    cl = check + dom + proc
    cs = [c for c in certs.connectivity_certs(st)
          if c['claim'] in {x['id'] for x in check}]
    t1 = time.time()
    obligations = {c['id']: c['pred'] for c in bench + proc}
    r = gate.verify(st, cl, cs, carried=carried, rules=rules, profile=profile,
                    domain_claims=dom, obligations=obligations)
    verify_seconds = round(time.time() - t1, 2)
    if r['ok'] and store is not None:
        from ..kernels import fab as _fab
        for c in bench:
            src = (r['fab']['claims'] if c['pred'] in _fab.PREDS
                   else r['lean'].get('claims', []))
            stt = {x['id']: x['status'] for x in src}.get(c['id'])
            if stt == 'proven':
                store.put(c, stt, '', st, ih)
        store.save()
    margins = None
    if robust:
        from . import process as proc
        eff = dict(rules)
        if process:
            eff.update(S.process_rules(process))
        margins = proc.margins(st, eff, robust)
        if not margins['ok']:
            r['ok'] = False
            r['reasons'] = list(r['reasons']) + margins['reasons']
    from . import repair as repair_mod
    obl = repair_mod.obligations(r)
    if margins:
        obl += repair_mod.margin_obligations(margins)
    tr = None
    if trust:
        from . import kicad_text
        tr = kicad_text.check(board)
        if not tr['ok']:
            r['ok'] = False
            r['reasons'] = list(r['reasons']) + [
                f"trust: {tr['n_mismatch']} mismatches, "
                f"{tr['n_missing']} missing, {tr['n_extra']} extra"]
        elif trust_strict and tr['arcs_approximated']:
            r['ok'] = False
            r['reasons'] = list(r['reasons']) + [
                f"trust: {tr['arcs_approximated']} arcs are chord-approximated "
                f"in the state"]
    asm = None
    if assembly:
        from . import assembly as asm_mod
        asm = asm_mod.check(board)
        if not asm['ok']:
            r['ok'] = False
            r['reasons'] = list(r['reasons']) + asm['reasons']
        elif assembly_strict and not asm.get('complete', True):
            r['ok'] = False
            r['reasons'] = list(r['reasons']) + [
                'assembly: ' + ','.join(asm.get('unknown', [])[:6]) +
                ' have no courtyard']
    refinement = None
    if spec:
        from . import spec as _spec
        refinement = _spec.refine(_spec.load(spec), st, r['ok'])
        if not refinement['ok']:
            r['ok'] = False
            r['reasons'] = list(r['reasons']) + refinement['reasons']
    rows = _rows(r) + [{'id': cid, 'kernel': 'lean', 'status': 'carried',
                        'witness': 'reused: scope untouched',
                        'measured': None, 'units': ''}
                       for cid, _ in carried]
    summary = gate.report_summary(r)
    summary['carried'] = summary.get('carried', 0) + len(carried)
    return {'schema': SCHEMA, 'board': str(board),
            'digest': st['digest'], 'profile': profile,
            'rules': dict(rules), 'rule_source': rule_src,
            'process': process, 'margins': margins, 'assembly': asm,
            'trust': tr,
            'refinement': refinement,
            'obligations': obl, 'measure': repair_mod.measure(obl),
            'ok': r['ok'], 'reasons': r['reasons'],
            'summary': summary,
            'claims': rows, 'route': None, 'unformalized': unformalized,
            'items': S.item_hashes(st),
            'verify_seconds': verify_seconds,
            'seconds': round(time.time() - t0, 1)}


def maintain(board=None, cert_path=None, rules=None, profile='lean+fab',
             domain_claims=(), state=None, prior=None, log=print):
    """Re-prove a prior certificate, carrying the untouched claims."""
    t0 = time.time()
    prior = prior or json.loads(Path(cert_path).read_text(encoding='utf-8'))
    if rules is None:
        rules = prior.get('rules') or S.board_rules(board)[0]
    st = state if state is not None else S.extract(Path(board), rules)
    ih = S.item_hashes(st)
    prev_items = prior.get('items') or {}
    changed = {i for i in set(ih) | set(prev_items)
               if ih.get(i) != prev_items.get(i)}
    prev = {}
    for row in prior.get('claims', []):
        prev[row['id']] = row
    cl = claim_mod.bench_claims(st)
    by_id = {c['id']: c for c in cl}
    keep, carried, reasons = [], [], []
    obligations = {}
    for cid, row in sorted(prev.items()):
        if row.get('status') not in ('proven', 'carried'):
            continue
        c = by_id.get(cid)
        if c is None:
            reasons.append(f'{cid}: claim disappeared')
            continue
        obligations[cid] = c['pred']
        scope = set(c.get('scope') or [])
        if scope and not (scope & changed):
            carried.append((cid, list(c.get('scope') or [])))
        else:
            keep.append(c)
    # every process predicate re-runs; it is the per-step rule.
    from ..kernels import fab as _fab
    have = {k['id'] for k in keep}
    for c in cl:
        if c['pred'] in _fab.PREDS and c['id'] not in have:
            keep.append(c)
    ids = {c['id'] for c in keep}
    cs = [c for c in certs.connectivity_certs(st) if c['claim'] in ids]
    r = gate.verify(st, keep, cs, touched=sorted(changed), carried=carried,
                    rules=rules, profile=profile,
                    domain_claims=domain_claims, obligations=obligations)
    carried_ids = {i for i, _ in carried}
    rows = _rows(r) + [{'id': cid, 'kernel': 'lean', 'status': 'carried',
                        'witness': 'reused: scope untouched',
                        'measured': None, 'units': ''}
                       for cid in sorted(carried_ids)]
    summary = gate.report_summary(r)
    summary['carried'] = summary.get('carried', 0) + len(carried_ids)
    return {'schema': SCHEMA, 'board': str(board), 'digest': st['digest'],
            'profile': profile, 'rules': dict(rules),
            'ok': r['ok'] and not reasons, 'reasons': reasons + r['reasons'],
            'summary': summary,
            'claims': rows,
            'carried': sorted(carried_ids),
            'reused': len(carried_ids),
            'changed_items': sorted(changed),
            'verify_seconds': round(time.time() - t0, 2),
            'seconds': round(time.time() - t0, 1)}


def write(cert, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cert, indent=1, ensure_ascii=False),
                    encoding='utf-8')
    return path
