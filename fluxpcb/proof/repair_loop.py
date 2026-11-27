"""Proof-directed repair: the gate's refusals drive the next route.

Each round reads the current board, turns every unproven obligation into a
target (the open nets), hands exactly those nets to the router lemma
(`route_scope`, which strips them and re-routes them around the fixed
copper), and re-verifies.  A round is adopted only when the obligation
measure strictly decreases; otherwise the loop stops and reports the
remaining set as the core.  The measure is (number of obligations, sorted
negative margins), an integer lexicographic order — the termination argument
is not a hope.
"""
from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

from . import certs, claims as claim_mod, gate, repair, router, state as S


def open_nets(st, report=None):
    """Nets whose connectivity certificate does not reach every pad."""
    out = []
    for c in certs.connectivity_certs(st):
        if c.get("missing"):
            out.append(c["claim"].split(":", 1)[1])
        elif report is not None:
            by = {x["id"]: x for x in report["lean"].get("claims", [])}
            if by.get(c["claim"], {}).get("status") not in ("proven", "carried"):
                out.append(c["claim"].split(":", 1)[1])
    return sorted(set(out))


def _report(st, rules):
    cl = claim_mod.bench_claims(st)
    return gate.verify(st, cl, certs.connectivity_certs(st), rules=rules)


def repair_loop(board, rules, rounds=3, passes=1, workdir=None, log=print):
    t0 = time.time()
    board = Path(board)
    work = Path(workdir) if workdir else board.parent / "proof_repair"
    work.mkdir(parents=True, exist_ok=True)
    src = work / board.name
    for ext in (".kicad_pro", ".kicad_prl"):
        s = board.with_suffix(ext)
        if s.exists():
            shutil.copyfile(s, src.with_suffix(ext))
    shutil.copyfile(board, src)
    rounds_log = []
    for r in range(rounds):
        st = S.extract(src, rules)
        rep = _report(st, rules)
        obl = repair.obligations(rep)
        m = repair.measure(obl)
        nets = open_nets(st, rep)
        rod = {'round': r, 'digest': st['digest'], 'measure': [m[0], list(m[1])],
               'open_nets': nets[:80], 'n_open': len(nets),
               'summary': gate.report_summary(rep)}
        rounds_log.append(rod)
        log(f"round {r}: {m[0]} obligations, {len(nets)} open nets")
        if not nets:
            break
        rw = work / f"round{r}"
        res = router.route_scope(src, nets, rules, workdir=rw,
                                 passes=passes, mode='preserve', log=log)
        if not res.get('ok'):
            rod['route'] = {'ok': False, 'reasons': res.get('reasons', [])[:6],
                            'seconds': res.get('seconds')}
            log(f"round {r}: route refused ({res.get('reasons', [])[:2]})")
            break
        src = Path(res['board'])
        st2 = S.extract(src, rules)
        rep2 = _report(st2, rules)
        m2 = repair.measure(repair.obligations(rep2))
        rod['route'] = {'ok': True, 'seconds': res.get('seconds'),
                        'carried': len(res['step']['carried']),
                        'touched': res['step']['touched']}
        rod['after'] = {'digest': st2['digest'],
                        'measure': [m2[0], list(m2[1])],
                        'n_open': len(open_nets(st2, rep2)),
                        'summary': gate.report_summary(rep2)}
        log(f"round {r}: measure {m[0]} -> {m2[0]}, "
            f"open {len(nets)} -> {rod['after']['n_open']}")
        if not repair.terminates(obl, repair.obligations(rep2)):
            rod['reason'] = 'no strict descent; stopping'
            break
    final = rounds_log[-1]
    return {'schema': 'fluxpcb-repair-1', 'board': str(board),
            'ok': final.get('n_open') == 0 if rounds_log else False,
            'rounds': rounds_log, 'final_board': str(src),
            'seconds': round(time.time() - t0, 1)}
