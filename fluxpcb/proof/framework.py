"""The framework entry point: one certificate per board.

`certify` is the whole loop in one call — extract, claims, certificates, Lean
kernel, fab kernel, the profile's domain kernels, and (optionally) a scoped
route as a gated Step.  The certificate is the artifact a run leaves behind:
its digest, its profile, every claim's status and witness, and the timings.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from . import certs, claims as claim_mod, conjectures, gate, router, state as S

SCHEMA = 'fluxpcb-cert-1'


def certify(board, rules=None, profile='lean+fab', domain_claims=(),
            route_nets=None, workdir=None, spec=None, log=print):
    t0 = time.time()
    board = Path(board)
    if rules is None:
        rules, rule_src = S.board_rules(board)
    else:
        rule_src = {'clearance': 'explicit', 'track_min': 'explicit'}
    route = None
    if route_nets:
        route = router.route_scope(board, route_nets, rules, workdir=workdir,
                                   profile=profile,
                                   domain_claims=domain_claims, spec=spec,
                                   log=log)
        return {'schema': SCHEMA, 'board': str(board),
                'digest': route.get('digest'), 'profile': profile,
                'rules': dict(rules), 'rule_source': rule_src, 'rule_source': rule_src,
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
    cl = claim_mod.bench_claims(st) + dom
    cs = certs.connectivity_certs(st)
    t1 = time.time()
    r = gate.verify(st, cl, cs, rules=rules, profile=profile,
                    domain_claims=dom)
    verify_seconds = round(time.time() - t1, 2)
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
    return {'schema': SCHEMA, 'board': str(board),
            'digest': st['digest'], 'profile': profile,
            'rules': dict(rules), 'rule_source': rule_src,
            'ok': r['ok'], 'reasons': r['reasons'],
            'summary': gate.report_summary(r),
            'claims': rows, 'route': route, 'unformalized': unformalized,
            'verify_seconds': verify_seconds,
            'seconds': round(time.time() - t0, 1)}


def write(cert, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cert, indent=1, ensure_ascii=False),
                    encoding='utf-8')
    return path
