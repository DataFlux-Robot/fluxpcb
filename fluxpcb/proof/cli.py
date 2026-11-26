"""CLI for module proofs: one module per process, so agents can run in parallel."""
from __future__ import annotations

import argparse
import json
import os
import sys

from . import compare, framework, library, modules, spec as spec_mod, state as S


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
    z.add_argument('--process', help='fab profile (jlc) as extra obligations')
    z.add_argument('--robust', help='process box for margin proofs (jlc-1oz/tight)')
    z.add_argument('--assembly', action='store_true',
                   help='also prove courtyard overlap / containment')
    z.add_argument('--assembly-strict', action='store_true',
                   help='deny footprints without a courtyard')
    z.add_argument('--trust', action='store_true',
                   help='cross-check the state against an independent text parser')
    z.add_argument('--trust-strict', action='store_true',
                   help='deny chord-approximated arcs')
    z.add_argument('--schematic',
                   help='schematic (.kicad_sch) or exported netlist (.net) to refine against')
    z.add_argument('--alias', help='declared ref aliases, e.g. U3=REF**')
    z.add_argument('--route-nets', help='comma-separated nets to re-route')
    z.add_argument('--workdir')
    z.add_argument('--out', required=True)
    cp = sub.add_parser('compare')
    cp.add_argument('--a', required=True)
    cp.add_argument('--b', required=True)
    lp = sub.add_parser('library')
    lsub = lp.add_subparsers(dest='libcmd', required=True)
    ll = lsub.add_parser('list')
    ll.add_argument('--root', default='modules')
    lc = lsub.add_parser('check')
    lc.add_argument('--root', default='modules')
    lc.add_argument('--board')
    lc.add_argument('--rules')
    lc.add_argument('--verify', action='store_true')
    lf = lsub.add_parser('find')
    lf.add_argument('--root', default='modules')
    lf.add_argument('--board', required=True)
    lf.add_argument('--rules')
    lf.add_argument('--terms', default='')
    lv = lsub.add_parser('prove')
    lv.add_argument('--root', default='modules')
    lv.add_argument('--module', required=True)
    lv.add_argument('--board', required=True)
    lv.add_argument('--rules')
    lv.add_argument('--no-register', action='store_true')
    a = ap.parse_args(argv)
    rules = _rules(getattr(a, 'rules', None))
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
    if a.cmd == 'compare':
        print(json.dumps(compare.compare_files(a.a, a.b), indent=1,
                         ensure_ascii=False))
        return 0
    if a.cmd == 'library':
        lib = library.Library(a.root)
        if a.libcmd == 'list':
            out = [{'module': m, 'version': lib.get(m).version,
                    'refs': len(lib.get(m).refs()),
                    'instances': len(lib.get(m).instances),
                    'deps': lib.get(m).deps} for m in lib.list()]
            print(json.dumps(out, indent=1, ensure_ascii=False))
            return 0
        if a.libcmd == 'check':
            rules = _rules(a.rules) if a.rules else None
            print(json.dumps(lib.check(board=a.board, rules=rules,
                                       verify=a.verify),
                             indent=1, ensure_ascii=False))
            return 0
        if a.libcmd == 'find':
            st = S.extract(a.board, _rules(a.rules) if a.rules
                           else S.board_rules(a.board)[0])
            terms = [t for t in (a.terms or '').split(',') if t]
            print(json.dumps(lib.find(st, terms), indent=1,
                             ensure_ascii=False))
            return 0
        if a.libcmd == 'prove':
            r = lib.prove_instance(a.module, a.board,
                                   _rules(a.rules) if a.rules else None,
                                   register=not a.no_register)
            print(json.dumps({'module': a.module, 'ok': r['ok'],
                              'summary': r['summary'],
                              'seconds': r['seconds']},
                             ensure_ascii=False))
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
            spec = spec_mod.load(a.spec).raw
        r = framework.certify(a.board, crules, profile=a.profile,
                              domain_claims=dom,
                              route_nets=nets or None,
                              workdir=a.workdir, spec=spec,
                              process=a.process, robust=a.robust,
                              assembly=a.assembly,
                              assembly_strict=a.assembly_strict,
                              trust=a.trust, trust_strict=a.trust_strict,
                              schematic=a.schematic,
                              schematic_aliases={
                                  k: v for k, v in
                                  (x.split('=', 1) for x in
                                   (a.alias or '').split(',') if '=' in x)})
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
