"""Proof-directed repair: the gate's refusals drive the next route.

Each round reads the current board, turns every unproven obligation into a
target (the open nets), hands exactly those nets to a tactic (a scoped route
is one gated Step), and re-verifies.  A candidate is adopted only when the
obligation measure strictly decreases; otherwise the next tactic for that
round is tried, and when every tactic fails the loop stops and reports the
remaining set as the core.  The measure is (number of obligations, sorted
negative margins), an integer lexicographic order — the termination argument
is not a hope.

Tactics are interchangeable backends behind the same gate: Freerouting (the
fast path), the in-process L/Z/A* router `route_embedded` (the fallback when
the DSN round trip or FR's optimizer crashes), or any future router.  A
backend crash is a tactic failure, never the end of the proof.  With
`drc_guard` the candidate must also not increase the external DRC's error
count: the state does not model zone pours yet, so a track crossing a pour is
invisible to the fab kernel and visible to the referee.
"""
from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

from . import certs, claims as claim_mod, gate, repair, router, state as S

DEFAULT_TACTICS = ('freerouting', 'embedded', 'embedded_keep')


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


def missing_pads(st):
    """Pads outside their net's spanning tree — the pad-level progress key.

    The net-level count cannot see a net getting closer (GND losing eight of
    its eighteen islands) or one open net being robbed to pay another; this
    key can.  It is deterministic (the tree starts at the first pad of each
    net in extraction order)."""
    return sum(len(c.get("missing") or [])
               for c in certs.connectivity_certs(st))


def _report(st, rules):
    cl = claim_mod.bench_claims(st)
    return gate.verify(st, cl, certs.connectivity_certs(st), rules=rules)


def _drc_errors(board, workdir, tag):
    """Error-severity count from kicad-cli DRC, or (None, error)."""
    from .. import repair_drc
    d = repair_drc.run_drc(str(board), str(Path(workdir) / f'drc_{tag}.json'))
    if not d.get('ok'):
        return None, d.get('error', 'drc failed')
    errs = [v for v in d.get('violations', [])
            if str(v.get('severity', '')).lower() == 'error']
    return len(errs), None


def repair_loop(board, rules, rounds=3, passes=1, workdir=None,
                tactics=DEFAULT_TACTICS, drc_guard=False, log=print):
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
    tactics = list(tactics or DEFAULT_TACTICS)
    drc_before = None
    if drc_guard:
        drc_before, err = _drc_errors(src, work, 'before')
        if drc_before is None:
            log(f"drc_guard off: {err}")
            drc_guard = False
        else:
            log(f"drc_guard: {drc_before} error-severity violation(s) before")
    rounds_log = []
    for r in range(rounds):
        st = S.extract(src, rules)
        rep = _report(st, rules)
        obl = repair.obligations(rep)
        pads = missing_pads(st)
        m = repair.measure(obl, pads)
        nets = open_nets(st, rep)
        rod = {'round': r, 'digest': st['digest'],
               'measure': [m[0], m[1], list(m[2])],
               'missing_pads': pads,
               'open_nets': nets[:80], 'n_open': len(nets),
               'summary': gate.report_summary(rep)}
        rounds_log.append(rod)
        log(f"round {r}: {m[0]} obligations, {len(nets)} open nets")
        if not nets:
            break
        adopted = None
        for tactic in tactics:
            rw = work / f"round{r}_{tactic}"
            res = router.route_scope(src, nets, rules, workdir=rw,
                                     passes=passes, mode='preserve',
                                     tactic=tactic, log=log)
            att = {'tactic': tactic, 'ok': bool(res.get('ok')),
                   'seconds': res.get('seconds')}
            rod.setdefault('tactics', []).append(att)
            if not res.get('ok'):
                att['error'] = str(res.get('error') or '')[-300:]
                att['reasons'] = list(res.get('reasons', []))[:6]
                why = att['error'] or (att['reasons'] or [''])[0]
                log(f"round {r}/{tactic}: refused ({str(why)[:120]})")
                continue
            cand = Path(res['board'])
            st2 = S.extract(cand, rules)
            rep2 = _report(st2, rules)
            obl2 = repair.obligations(rep2)
            pads2 = missing_pads(st2)
            m2 = repair.measure(obl2, pads2)
            att.update({'carried': int(res['step']['carried'] or 0),
                        'touched': res['step']['touched'],
                        'measure': [m2[0], m2[1], list(m2[2])],
                        'missing_pads': pads2,
                        'n_open': len(open_nets(st2, rep2)),
                        'summary': gate.report_summary(rep2)})
            log(f"round {r}/{tactic}: measure {m[0]}/{pads} -> "
                f"{m2[0]}/{pads2}, open {len(nets)} -> {att['n_open']}")
            if not repair.terminates(obl, obl2, pads, pads2):
                att['reason'] = 'no strict descent; candidate not adopted'
                log(f"round {r}/{tactic}: {att['reason']}")
                continue
            # only a descending candidate is worth the external DRC run
            if drc_guard:
                n2, err = _drc_errors(cand, work, f'r{r}_{tactic}')
                att['drc_errors'] = n2
                if n2 is None:
                    att['reason'] = f'DRC failed: {err}'
                    log(f"round {r}/{tactic}: {att['reason']}")
                    continue
                if drc_before is not None and n2 > drc_before:
                    att['reason'] = (f'DRC regressed {drc_before} -> {n2}; '
                                     f'candidate not adopted')
                    log(f"round {r}/{tactic}: {att['reason']}")
                    continue
            adopted = (tactic, cand, st2, rep2, m2)
            att['adopted'] = True
            if drc_guard:
                drc_before = att['drc_errors']
            break
        if adopted is None:
            rod['reason'] = 'no tactic produced a strict descent; stopping'
            break
        tactic, src, st2, rep2, m2 = adopted
        rod['route'] = {'ok': True, 'tactic': tactic,
                        'seconds': rod['tactics'][-1]['seconds'],
                        'carried': rod['tactics'][-1]['carried'],
                        'touched': rod['tactics'][-1]['touched']}
        rod['after'] = {'digest': st2['digest'],
                        'measure': [m2[0], m2[1], list(m2[2])],
                        'missing_pads': pads2,
                        'n_open': len(open_nets(st2, rep2)),
                        'summary': gate.report_summary(rep2)}
    final = rounds_log[-1]
    return {'schema': 'fluxpcb-repair-1', 'board': str(board),
            'ok': final.get('n_open') == 0 if rounds_log else False,
            'rounds': rounds_log, 'final_board': str(src),
            'seconds': round(time.time() - t0, 1)}
