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

from . import certs, claims as claim_mod, conjectures, gate, step, state as S

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


_KEEP_TACTICS = {'embedded_keep', 'freerouting_keep'}


def _tactic_base(tactic):
    """Map a tactic name to its backend.  `_keep` variants route on the board
    as-is: no net is stripped, the backend only adds copper for the open
    pairs.  Use it for repairs on a half-routed board, where stripping the
    target nets would also remove their good sections (and for FR, which
    continues from the protected open stubs, the Z1 recipe)."""
    return tactic[:-5] if tactic.endswith('_keep') else tactic


def _route_backend(tactic, kicad_cli, kipy, dst, workdir, rules, passes, log,
                   net_classes=None, normal_rule=None):
    """Run one routing tactic on the stripped board; the gate judges it.

    `freerouting`: the incremental FR session (fix-marked, DSN round trip).
    `embedded`: the in-process L/Z/A* router.  It is the fallback for boards
    where the DSN round trip or FR's optimizer crashes -- a backend failure
    is then a tactic failure, not the end of the proof.
    """
    from .. import routing
    if tactic == 'embedded':
        rs = routing.ratsnest_state(kipy, dst, log=log)
        if not rs:
            return {'ok': False, 'mode': 'embedded',
                    'error': 'ratsnest audit failed'}
        pairs = rs.get('pairs') or []
        if not pairs:
            return {'ok': False, 'mode': 'embedded',
                    'error': 'no open pairs after strip'}
        cls = workdir / 'route_classes.json'
        clearance = (rules.get('clearance') or 200000) / 1e6
        cls.write_text(json.dumps({'classes': {},
                                   'clearance_mm': clearance}),
                       encoding='utf-8')
        pairs_file = dst.parent / 'ratsnest_pairs.txt'
        try:
            rc, out = routing._kipy(kipy, routing._EMBEDDED, str(dst),
                                    str(pairs_file), str(cls), timeout=1200)
        except Exception as e:                 # timeout, spawn failure, ...
            return {'ok': False, 'mode': 'embedded',
                    'error': f'embedded router failed: {e}'}
        line = [l for l in out.splitlines() if 'ROUTES_ADDED' in l]
        return {'ok': rc == 0, 'mode': 'embedded', 'pairs': len(pairs),
                'summary': line[-1][:400] if line else out[-300:],
                'error': None if rc == 0 else out[-300:]}
    fr = {}
    for attempt in range(max(1, passes)):
        fr = routing.route_with_freerouting(dst, kipy, log=log,
                                            incremental=True,
                                            net_classes=net_classes,
                                            normal_rule=normal_rule)
        if not fr.get('ok'):
            fr['mode'] = 'freerouting'
            return fr
        if attempt + 1 >= max(1, passes):
            break
        probe = S.extract(dst, rules)
        if not any(c.get('missing') for c in certs.connectivity_certs(probe)):
            break
        log(f'  [proof] pass {attempt + 1}: opens left, another FR pass')
    fr['mode'] = 'freerouting'
    return fr


def route_scope(board, nets, rules, workdir=None, profile='lean+fab',
                domain_claims=(), carry=True, spec=None, passes=2,
                mode='publish', tactic='freerouting', log=print):
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
    if tactic in _KEEP_TACTICS:
        dropped = 0                  # additive repair: keep every track
    else:
        dropped = _strip(kipy, dst, nets, workdir)
    # tell FR the fab rule instead of its built-in 0.1397mm default: every
    # net in the "normal" class at the board's clearance (R2 recipe)
    net_classes = {n: 'normal' for n in (before.get('netPads') or {})
                   if n and not n.startswith('~')}
    normal_rule = None
    if rules.get('clearance'):
        ceil_um = lambda nm: -(-int(nm) // 1000)      # noqa: E731
        normal_rule = (max(150, ceil_um(rules.get('track_min') or 150000)),
                       ceil_um(rules['clearance']))
    fr = _route_backend(_tactic_base(tactic), kicad_cli, kipy, dst, workdir,
                        rules, passes, log, net_classes=net_classes,
                        normal_rule=normal_rule)
    if not fr.get('ok'):
        return {'ok': False, 'error': fr.get('error'), 'tactic': tactic,
                'mode': fr.get('mode'), 'seconds':
                    round(time.time() - t0, 1), 'dropped': dropped}
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
    if mode == 'preserve':
        # a repair step on a board that already has unknowns: no proof may be
        # lost and no proven process predicate may regress, but pre-existing
        # unknowns/refutations do not block the candidate; the caller checks
        # the descent measure
        pr = step.preserve(before, after, rules, profile=profile,
                           domain_claims=dom, before_report=before_report)
        r = pr['report']
        ok, reasons = bool(pr['ok']), list(pr['reasons'])
    else:
        try:
            r = gate.commit_step(before, after, touched, cl, cs,
                                 carried=carried, rules=rules, profile=profile,
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
            'fr': {k: v for k, v in fr.items() if k not in ('stats',)},
            'tactic': tactic, 'mode': fr.get('mode'),
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
