"""The router lemma: a scoped route is one gated Step.

`route_scope` takes the board as designed, rips only the named nets' copper in
a workspace copy, lets the incremental Freerouting session route them around
the untouched (fix-marked) copper, and re-verifies the result on the full
state.  The Step is before -> after with touched = the removed and added item
ids, so every proof whose scope avoids those ids is carried and every claim on
the routed nets is proved afresh.  The stripped workspace is not a design state
and is never committed; the only board change the gate sees is the Step.
"""
from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

from . import certs, claims as claim_mod, conjectures, gate, state as S

_STRIP = '''\
import json, sys
import pcbnew
board, nets_file = sys.argv[1], sys.argv[2]
nets = set(json.load(open(nets_file, encoding="utf-8")))
b = pcbnew.LoadBoard(board)
dropped = 0
for t in list(b.GetTracks()):
    n = b.FindNet(t.GetNetCode())
    nm = n.GetNetname() if n else ""
    if nm in nets:
        b.Delete(t)
        dropped += 1
pcbnew.SaveBoard(board, b)
print(json.dumps({"dropped": dropped}))
'''


def _copy_with_project(src: Path, dst: Path) -> None:
    shutil.copyfile(src, dst)
    for ext in ('.kicad_pro', '.kicad_prl'):
        s = src.with_suffix(ext)
        if s.exists():
            shutil.copyfile(s, dst.with_suffix(ext))


def _strip(kipy: str, board: Path, nets, workdir: Path) -> int:
    from ..routing import _kipy
    netf = workdir / 'scope_nets.json'
    netf.write_text(json.dumps(sorted(nets)), encoding='utf-8')
    script = workdir / '_strip_nets.py'
    script.write_text(_STRIP, encoding='utf-8')
    rc, out = _kipy(kipy, script, str(board), str(netf), timeout=300)
    try:
        return int(json.loads([l for l in out.splitlines()
                               if l.strip().startswith('{')][-1])['dropped'])
    except (ValueError, IndexError):
        raise RuntimeError(f'strip failed rc={rc}: {out.strip()[-200:]}')


def route_scope(board, nets, rules, workdir=None, profile='lean+fab',
                domain_claims=(), carry=True, spec=None, passes=2, log=print):
    """Rip `nets`' copper, route them incrementally, and gate the Step."""
    from .. import routing
    t0 = time.time()
    board = Path(board)
    nets = set(nets)
    workdir = Path(workdir) if workdir else board.parent / 'proof_route'
    workdir.mkdir(parents=True, exist_ok=True)
    dst = workdir / board.name
    _copy_with_project(board, dst)

    before = S.extract(board, rules)
    before_report = None
    if carry:
        bcl = claim_mod.bench_claims(before)
        before_report = gate.verify(before, bcl, certs.connectivity_certs(before))
    kicad_cli, kipy = routing.find_kicad()
    if not kipy:
        return {'ok': False, 'error': 'KiCad python not found'}
    dropped = _strip(kipy, dst, nets, workdir)
    fr = {}
    for attempt in range(max(1, passes)):
        fr = routing.route_with_freerouting(dst, kipy, log=log,
                                            incremental=True)
        if not fr.get('ok'):
            return {'ok': False, 'error': fr.get('error'), 'seconds':
                    round(time.time() - t0, 1), 'dropped': dropped}
        if attempt + 1 >= max(1, passes):
            break
        probe = S.extract(dst, rules)
        if not any(c.get('missing') for c in certs.connectivity_certs(probe)):
            break
        log(f'  [proof] pass {attempt + 1}: opens left, another FR pass')
    after = S.extract(dst, rules)
    dom = list(domain_claims)
    unformalized = []
    if spec:
        more, unformalized = conjectures.from_spec(spec, after)
        dom += more
    bid = {i['id'] for i in before['items']}
    aid = {i['id'] for i in after['items']}
    touched = bid ^ aid
    cl = claim_mod.bench_claims(after)
    cs = certs.connectivity_certs(after)
    carried = []
    if before_report is not None:
        proven = {c['id'] for c in before_report['lean']['claims']
                  if c['status'] == 'proven'}
        for c in claim_mod.bench_claims(before):
            if c['id'] in proven and not (set(c.get('scope') or []) & touched):
                carried.append((c['id'], list(c.get('scope') or [])))
    try:
        r = gate.commit_step(before, after, touched, cl, cs, carried=carried,
                             rules=rules, profile=profile,
                             domain_claims=dom)
        ok, reasons = True, []
    except gate.GateDenied as e:
        r = gate.verify(after, cl, cs, before=before, touched=touched,
                        carried=carried, rules=rules, profile=profile,
                        domain_claims=dom)
        ok, reasons = False, list(r['reasons'])
    return {'ok': ok, 'reasons': reasons, 'board': str(dst),
            'step': {'before_digest': before['digest'],
                     'after_digest': after['digest'],
                     'touched': len(touched), 'touched_nets': sorted(nets),
                     'dropped': dropped, 'carried': len(carried),
                     'carried_ids': [i for i, _ in carried]},
            'summary': gate.report_summary(r),
            'claims': _rows(r),
            'unformalized': unformalized,
            'digest': after['digest'],
            'fr': {k: v for k, v in fr.items() if k != 'stats'},
            'seconds': round(time.time() - t0, 1)}


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
