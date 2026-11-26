"""CLI for module proofs: one module per process, so agents can run in parallel."""
from __future__ import annotations

import argparse
import json
import os
import sys

from . import framework, modules, state as S


def _rules(path):
    if not path:
        return {'clearance': 200000, 'track_min': 150000}
    with open(path, encoding='utf-8') as f:
        return {k: int(v) for k, v in json.load(f).items()}


def main(argv=None):
    ap = argparse.ArgumentParser(prog='fluxpcb.proof.cli')
    sub = ap.add_subparsers(dest='cmd', required=True)
    p = sub.add_parser('prove')
    p.add_argument('--board', required=True)
    p.add_argument('--contract', required=True)
    p.add_argument('--rules')
    p.add_argument('--out', required=True)
    c = sub.add_parser('contract')
    c.add_argument('--board', required=True)
    c.add_argument('--refs', required=True, help='comma-separated refs')
    c.add_argument('--name', default='module')
    c.add_argument('--rules')
    m = sub.add_parser('merge')
    m.add_argument('--board', required=True)
    m.add_argument('--contracts', nargs='+', required=True)
    m.add_argument('--rules')
    m.add_argument('--proofs', nargs='*')
    m.add_argument('--out', required=True)
    z = sub.add_parser('certify')
    z.add_argument('--board', required=True)
    z.add_argument('--rules')
    z.add_argument('--profile', default='lean+fab')
    z.add_argument('--domain', help='JSON list of domain claims')
    z.add_argument('--spec', help='requirements JSON (compiled to conjectures)')
    z.add_argument('--route-nets', help='comma-separated nets to re-route')
    z.add_argument('--workdir')
    z.add_argument('--out', required=True)
    a = ap.parse_args(argv)
    rules = _rules(a.rules)
    if a.cmd == 'contract':
        st = S.extract(a.board, rules)
        ctr = modules.derive(st, a.name, [r for r in a.refs.split(',') if r])
        print(json.dumps(ctr.as_dict(), indent=1, ensure_ascii=False))
        return 0
    if a.cmd == 'prove':
        st = S.extract(a.board, rules)
        ctr = modules.load(st, a.contract)
        r = modules.prove(a.board, ctr, rules)
        os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
        with open(a.out, 'w', encoding='utf-8') as f:
            json.dump(r, f, indent=1, ensure_ascii=False)
        print(json.dumps({'module': r['module'], 'ok': r['ok'],
                          'summary': r['summary'], 'seconds': r['seconds']},
                         ensure_ascii=False))
        return 0 if r['ok'] else 3
    if a.cmd == 'merge':
        proofs = None
        if a.proofs:
            proofs = [json.load(open(p, encoding='utf-8')) for p in a.proofs]
        r = modules.merge(a.board, a.contracts, rules, proofs=proofs)
        with open(a.out, 'w', encoding='utf-8') as f:
            json.dump(r, f, indent=1, ensure_ascii=False)
        print(json.dumps(r, indent=1, ensure_ascii=False))
        return 0 if r['ok'] else 3
    if a.cmd == 'certify':
        crules = rules if a.rules else None
        dom = []
        if a.domain:
            with open(a.domain, encoding='utf-8') as f:
                dom = json.load(f)
        nets = [n for n in (a.route_nets or '').split(',') if n]
        spec = None
        if a.spec:
            with open(a.spec, encoding='utf-8') as f:
                spec = json.load(f)
        r = framework.certify(a.board, crules, profile=a.profile,
                              domain_claims=dom,
                              route_nets=nets or None,
                              workdir=a.workdir, spec=spec)
        framework.write(r, a.out)
        print(json.dumps({'ok': r['ok'], 'digest': r.get('digest'),
                          'profile': r.get('profile'),
                          'summary': r.get('summary'),
                          'reasons': r.get('reasons', [])[:6],
                          'unformalized': len(r.get('unformalized', [])),
                          'seconds': r.get('seconds')},
                         ensure_ascii=False))
        return 0 if r['ok'] else 3
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
