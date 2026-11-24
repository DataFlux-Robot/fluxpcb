#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Arm campaign: components 1 and 2 improving components 3 and 4, on evidence.

  gen0      12 factorial arms + an A/A duplicate + one ablation on a
            failure-stratified coreset (arms_run.py); read it with
            distinguish.py.
  gen 1..N  component 1 (MetaDevLoop): diagnose the incumbent arm's traces,
            propose one-variable candidate arms (LLM when configured, else
            rules), evaluate each on the boards the incumbent actually
            fails (on-policy), keep what cleans more than it dirties.
            component 2 (MetaMetaLoop) retires variables that failed twice.
  final     component 2's gate: the resulting configuration against the
            hand-built starting configuration on the boards NEVER used to
            choose, on sealed known-bad controls, and under the JLC fab
            profile.

Only what passes the final gate is called an improvement due to 1 and 2.
Everything else is reported as measured.
"""
import argparse
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, '..', 'flux-neurotransmitter'))
sys.path.insert(0, HERE)

import arms_run  # noqa: E402
import controls  # noqa: E402
import redteam  # noqa: E402
from fluxpcb.meta import distinguish as D  # noqa: E402
from fluxpcb.meta.arm import Arm  # noqa: E402
from fluxpcb.meta.devloop import MetaDevLoop  # noqa: E402
from fluxpcb.meta.feedback import load_packets  # noqa: E402
from fluxpcb.meta.memory import Memory  # noqa: E402
from fluxpcb.meta.meta_loop import MetaMetaLoop, confirm  # noqa: E402
from fluxpcb.meta.strategy_store import choose, cluster_key  # noqa: E402

# The configuration components 1 and 2 start from: the hand-built default.
RECORD = 'gate+batch'
PCBR = {'d3a': 94.9, 'd3b': 100.0, 'd3c': 40.0}


def factorial():
    return arms_run.gen0_arms()[:12]


def record_arm():
    return [a for a in arms_run.gen0_arms() if a.name == RECORD][0]


def clean(c) -> bool:
    return bool(c) and c.get('outcome') == 'clean'


def cells_index(run):
    return {(c['board'], c['arm']): c for c in D.load_cells(run)}


def all_boards():
    return json.load(open(os.path.join(HERE, 'boards_full.json'),
                          encoding='utf-8'))


def best_single(cells, arms):
    """Best one arm for every board: most clean, then fastest."""
    tally = {}
    for a in arms:
        mine = [c for c in cells if c['arm'] == a.name
                and c.get('instrument_ok') and c.get('outcome') in
                ('clean', 'fail', 'budget')]
        if mine:
            tally[a.name] = (sum(1 for c in mine if clean(c)),
                             -sum(c.get('wall_seconds') or 0 for c in mine) / len(mine))
    return max(tally, key=lambda n: tally[n]) if tally else None


REPEATS = 4          # extra runs of each arm on a board the two disagree on
MARGIN = 0.6         # clean-rate difference that counts as an effect


def series(run, board, arm, kw, k=REPEATS):
    """Outcomes of one (board, arm): the original cell plus k repeat runs.

    A single run cannot tell an effect from run-to-run variation.  On the
    first board this was measured on, the base arm itself passed 1 time in
    5, and a one-run comparison read that as a win for an unrelated change.
    """
    import dataclasses
    reps = [dataclasses.replace(arm, name=f'{arm.name}~rep{i}')
            for i in range(1, k + 1)]
    arms_run.evaluate(run, [board], reps, kw['budget'], kw['timeout'],
                      kw['workers'], 'repeat')
    idx = cells_index(run)
    outs = [(idx.get((board['dir'], arm.name)) or {}).get('outcome')]
    outs += [(idx.get((board['dir'], r.name)) or {}).get('outcome')
             for r in reps]
    return [o for o in outs if o]


def judge(run, board, base, cand, kw, margin=MARGIN):
    """{'verdict': win|loss|none, 'base_rate', 'cand_rate', 'runs'}.

    Only called where the single runs disagree.  An effect must separate
    the two arms' clean rates by `margin`; anything less is noise.
    """
    sb, sc = series(run, board, base, kw), series(run, board, cand, kw)
    rb = sum(1 for o in sb if o == 'clean') / max(1, len(sb))
    rc = sum(1 for o in sc if o == 'clean') / max(1, len(sc))
    diff = rc - rb
    verdict = ('win' if diff >= margin else
               'loss' if -diff >= margin else 'none')
    return {'verdict': verdict, 'base_rate': round(rb, 2),
            'cand_rate': round(rc, 2), 'runs': [len(sb), len(sc)],
            'base': sb, 'cand': sc}


def make_evaluator(run, loop, base, coreset, gen, budget, timeout, workers):
    """Evaluate a candidate on the boards the incumbent struggles with."""
    def evaluate(candidate):
        arm = loop.arm(candidate.name)
        if arm is None:
            return None
        targets = set(MetaDevLoop.on_policy(load_packets(run), base.name))
        idx = cells_index(run)
        # A board the incumbent cannot finish inside the budget cannot judge
        # a parameter change; spending the budget on it buys nothing.
        blind = sorted({b['dir'] for b in coreset if b['dir'] in targets
                        and (idx.get((b['dir'], base.name)) or {}).get('outcome') == 'budget'})
        subset = [b for b in coreset if b['dir'] in targets
                  and b['dir'] not in blind]
        cells, _ = arms_run.evaluate(run, subset, [arm], budget, timeout,
                                     workers, f'gen{gen}')
        del cells
        idx = cells_index(run)
        kw = {'budget': budget, 'timeout': timeout, 'workers': workers}
        judged = []
        for b in subset:
            c = idx.get((b['dir'], arm.name))
            r = idx.get((b['dir'], base.name))
            if not (c and c.get('instrument_ok') and r and r.get('instrument_ok')):
                unusable.append(b['dir'])
                continue
            walls.append(c.get('wall_seconds') or 0)
            if clean(c) == clean(r):
                continue                      # the single runs agree
            # they disagree: settle it with repeats, not with one run each
            v = judge(run, b, base, arm, kw)
            judged.append(dict(v, board=b['dir']))
            if v['verdict'] == 'win':
                wins.append(b['dir'])
            elif v['verdict'] == 'loss':
                losses.append(b['dir'])
        return {'wins': len(wins), 'losses': len(losses),
                'boards': len(subset), 'unusable': unusable,
                'budget_blind_skipped': blind,
                'win_boards': wins, 'loss_boards': losses,
                'disagreements': judged,
                'noise_boards': [j['board'] for j in judged
                                 if j['verdict'] == 'none'],
                'mean_wall': round(sum(walls) / max(1, len(walls)), 1)}
    return evaluate


def jlc_clean(cell, run):
    """Waiver-free JLC fab-profile verdict for one finished board."""
    rel = cell.get('cert')
    if not rel:
        return None
    pcb = os.path.join(os.path.dirname(os.path.join(run, rel)),
                       os.path.basename(cell['board']) + '.kicad_pcb')
    pro = os.path.join(HERE, 'boards', cell['board'], 'circuit.kicad_pro')
    if not (os.path.exists(pcb) and os.path.exists(pro)):
        return None
    import shutil
    import tempfile
    tmp = tempfile.mkdtemp(prefix='ahr_')
    jb = os.path.join(tmp, 'j.kicad_pcb')
    shutil.copy(pcb, jb)
    redteam._jlc_profile(pro, os.path.join(tmp, 'j.kicad_pro'))
    redteam._jlc_dru(os.path.join(tmp, 'j.kicad_dru'))
    out = redteam._drc(jb, os.path.join(tmp, 'jlc.json'))
    if not out.get('ok'):
        return None
    return out['errors'] == 0 and out['unconnected'] == 0


def ahr(cells, run, workers):
    """Share of project-clean boards that fail the fab profile (lower is better)."""
    project_clean = [c for c in cells if clean(c)]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        verdicts = list(pool.map(lambda c: jlc_clean(c, run), project_clean))
    judged = [v for v in verdicts if v is not None]
    return {'project_clean': len(project_clean), 'judged': len(judged),
            'fab_clean': sum(1 for v in judged if v),
            'ahr': round(1 - sum(1 for v in judged if v) / max(1, len(judged)), 4),
            'complete': len(judged) == len(project_clean)}


def final_confirmation(run, args, final, record, selector, coreset,
                       use_selector):
    """Component 2's gate on the resulting configuration."""
    cores_dirs = {b['dir'] for b in coreset}
    heldout = [b for b in all_boards() if b['dir'] not in cores_dirs]
    kw = dict(budget=args.budget, timeout=args.timeout, workers=args.workers)
    arms_run.evaluate(run, heldout, [record], kw['budget'], kw['timeout'],
                      kw['workers'], 'heldout-record')
    if final.name != record.name:
        arms_run.evaluate(run, coreset, [final], kw['budget'], kw['timeout'],
                          kw['workers'], 'final-coreset')
        arms_run.evaluate(run, heldout, [final], kw['budget'], kw['timeout'],
                          kw['workers'], 'heldout-final')
    by_name = {a.name: a for a in factorial()}
    idx = cells_index(run)

    def pick(board):
        """The arm the candidate configuration uses on a board."""
        if not use_selector or not selector or not selector.get('rules'):
            return final.name
        feats = ((idx.get((board['dir'], record.name)) or {}).get('features')) or {}
        return choose(selector, feats) or final.name

    # selector rules may name factorial arms: give them cells where picked
    need = {}
    for b in heldout:
        name = pick(b)
        if name not in (record.name, final.name):
            need.setdefault(name, []).append(b)
    for name, boards in need.items():
        arms_run.evaluate(run, boards, [by_name[name]], kw['budget'],
                          kw['timeout'], kw['workers'], 'heldout-selector')
    idx = cells_index(run)

    def outcome(board, name):
        return idx.get((board['dir'], name))

    def arm_named(name):
        return final if name == final.name else (
            record if name == record.name else by_name[name])

    noise = []
    flaky = []

    def paired(boards, cand):
        """Boards gained and lost, each one settled by repeats.

        A board where the single runs disagree is re-run REPEATS times per
        arm and counts only if the clean rates separate by MARGIN; the
        rest is reported as noise, not as a win or a loss.
        """
        wins, losses, missing = [], [], []
        for b in boards:
            name = cand(b) if callable(cand) else cand
            c, r = outcome(b, name), outcome(b, record.name)
            if not (c and r and c.get('instrument_ok') and r.get('instrument_ok')):
                missing.append(b['dir'])
                continue
            if clean(c) == clean(r):
                continue
            v = judge(run, b, record, arm_named(name), kw)
            if v['verdict'] == 'win':
                wins.append(b['dir'])
            elif v['verdict'] == 'loss':
                losses.append(b['dir'])
            else:
                noise.append(dict(v, board=b['dir']))
            for rate in (v['base_rate'], v['cand_rate']):
                if 0 < rate < 1:
                    flaky.append(b['dir'])
                    break
        return wins, losses, missing

    # the candidate on the coreset is the final arm (the selector is fitted
    # here, so on this set it would be an oracle, not a choice)
    co_wins, co_losses, co_missing = paired(coreset, final.name)
    co_noise = list(noise)
    del noise[:]
    ho_wins, ho_losses, ho_missing = paired(heldout, pick)
    ho_noise = list(noise)
    rec_held = [outcome(b, record.name) for b in heldout]
    cand_held = [outcome(b, pick(b)) for b in heldout]
    rec_clean = sum(1 for c in rec_held if clean(c))
    cand_clean = sum(1 for c in cand_held if clean(c))

    used = [c for c in list(idx.values())
            if c['arm'] in ({record.name, final.name} | set(need))
            and c.get('outcome') in ('clean', 'fail', 'budget')]
    bad_instrument = sorted({f"{c['board']}:{c['arm']}" for c in used
                             if not c.get('instrument_ok')})
    action_errors = sorted({f"{c['board']}:{c['arm']}" for c in used
                            if c.get('degraded_stages')})

    # sealed known-bad controls: nothing may call them clean
    ctl_index = controls.load_or_build(
        [b for b in coreset if b.get('coreset_role') == 'guard'], 8)
    tampered = controls.integrity(ctl_index)
    ctl_index = controls.materialize(ctl_index)
    ctl_arms = {record.name: record, final.name: final}
    for name in need:
        ctl_arms[name] = by_name[name]
    for b in ctl_index:
        name = pick(b) if use_selector else final.name
        if name in ctl_arms and name not in (record.name, final.name):
            ctl_arms.setdefault(name, by_name[name])
    arms_run.evaluate(run, ctl_index, list(ctl_arms.values()), kw['budget'],
                      kw['timeout'], kw['workers'], 'controls')
    idx = cells_index(run)
    ctl_clean = []
    ctl_unjudged = []
    for b in ctl_index:
        for name in ctl_arms:
            c = idx.get((b['dir'], name))
            if c is None:
                ctl_unjudged.append(f'{b["name"]}:{name}')
            elif clean(c):
                ctl_clean.append(f'{b["name"]}:{name}')

    every = coreset + heldout
    rec_cells = [outcome(b, record.name) for b in every]
    cand_cells = [outcome(b, pick(b)) for b in every]
    rec_ahr = ahr([c for c in rec_cells if c], run, args.workers)
    cand_ahr = ahr([c for c in cand_cells if c], run, args.workers)

    noise = D.noise(D.load_cells(run), D.by_board(D.load_cells(run)))
    evidence = {
        'kind': ('selector+param' if (use_selector and selector.get('rules'))
                 else 'param'),
        'delta': {'arm': final.name, 'arm_digest': final.digest(),
                  'param_deltas': final.params.deltas_from(record.params),
                  'selector': selector if use_selector else None},
        'instrument_ok': not bad_instrument,
        'instrument_problems': bad_instrument[:10],
        'action_errors': len(action_errors),
        'action_error_cells': action_errors[:10],
        'coreset': {'wins': len(co_wins), 'losses': len(co_losses),
                    'boards': len(coreset), 'unjudged': co_missing},
        # single-run A/A result, for the record only: the noise that
        # matters is settled per board by repeats (see repeatability)
        'noise_flips': 0,
        'noise_aa_single_pair': (len(noise.get('flips', []))
                                 if noise.get('available') else 0),
        'repeatability': {'repeats_per_arm': REPEATS, 'margin': MARGIN,
                          'flaky_boards': sorted(set(flaky)),
                          'unresolved_disagreements': co_noise + ho_noise,
                          'coreset_disagreements': co_noise,
                          'heldout_disagreements': ho_noise},
        'heldout': {'n': len(heldout), 'incumbent_clean': rec_clean,
                    'candidate_clean': cand_clean,
                    'lost': ho_losses, 'gained': ho_wins,
                    'unjudged': ho_missing},
        'controls': ({'n': len(ctl_index), 'clean': ctl_clean,
                      'tampered': tampered, 'unjudged': ctl_unjudged}
                     if not tampered and not ctl_unjudged else None),
        'scope_narrowed': not set(record.allowed_ops or ()) <= set(final.allowed_ops or ()),
        'ahr': ({'incumbent': rec_ahr['ahr'], 'candidate': cand_ahr['ahr'],
                 'detail': {'incumbent': rec_ahr, 'candidate': cand_ahr}}
                if rec_ahr['complete'] and cand_ahr['complete'] else None),
    }
    verdict = confirm(evidence)

    def split_table(cells_by_board):
        out = {}
        for b in every:
            s = out.setdefault(b['set'], {'clean': 0, 'n': 0})
            s['n'] += 1
            c = cells_by_board.get(b['dir'])
            if c and clean(c):
                s['clean'] += 1
        tot = {'clean': sum(v['clean'] for v in out.values()),
               'n': sum(v['n'] for v in out.values())}
        out['all'] = tot
        return {k: dict(v, rate=round(100.0 * v['clean'] / v['n'], 1))
                for k, v in out.items()}

    table = {
        'record': split_table({b['dir']: outcome(b, record.name) for b in every}),
        'candidate': split_table({b['dir']: outcome(b, pick(b)) for b in every}),
        'pcbrouteagent': PCBR,
    }
    return evidence, verdict, table


def write_report(run, report):
    lines = ['# Arm campaign', '']
    g0 = report.get('gen0') or {}
    lines.append('## Gen 0: %s arms on %s boards' % (
        g0.get('cells'), g0.get('boards')))
    for name, s in sorted((g0.get('per_arm') or {}).items(),
                          key=lambda kv: -kv[1]['clean']):
        lines.append(f"- {name}: {s['clean']}/{s['boards']} clean, "
                     f"{s['mean_wall_s']} s mean")
    aa = g0.get('noise_aa') or {}
    lines.append('')
    lines.append(f"A/A noise: {len(aa.get('flips', []))}/{aa.get('boards')} "
                 f"boards flip on their own")
    lines.append(f"Best single arm {g0.get('best_single_arm')}; oracle "
                 f"{g0.get('oracle_any_arm')}; selector headroom "
                 f"{g0.get('selector_headroom')}")
    for gen in report.get('generations', []):
        lines.append('')
        lines.append(f"## Generation {gen['generation']} (base {gen['base_arm']})")
        for p in gen.get('proposals') or []:
            lines.append(f"- proposed {p['variable']}={p['value']} "
                         f"[{p['proposer']}; {p['pattern']}]: {p['why'][:90]}")
        for r in gen.get('results') or []:
            lines.append(f"- {r['candidate']}: +{r['wins']} / -{r['losses']} "
                         f"on {r['boards']} boards")
        lines.append(f"- status: {gen['status']}; admitted: {gen.get('admitted')}")
    ev, verdict = report.get('evidence') or {}, report.get('verdict') or {}
    lines.append('')
    lines.append('## Final gate (component 2)')
    lines.append(f"verdict: {'ADMITTED' if verdict.get('ok') else 'NOT admitted'}")
    for r in verdict.get('reasons') or []:
        lines.append(f'- {r}')
    lines.append(f"- coreset: +{ev.get('coreset', {}).get('wins')} / "
                 f"-{ev.get('coreset', {}).get('losses')} on "
                 f"{ev.get('coreset', {}).get('boards')} boards")
    ho = ev.get('heldout') or {}
    lines.append(f"- held-out ({ho.get('n')} boards never used to choose): "
                 f"{ho.get('incumbent_clean')} -> {ho.get('candidate_clean')}")
    ct = ev.get('controls') or {}
    lines.append(f"- sealed known-bad controls: {ct.get('n')} run, "
                 f"{len(ct.get('clean') or [])} judged clean")
    a = ev.get('ahr') or {}
    lines.append(f"- JLC fab profile AHR: {a.get('incumbent')} -> {a.get('candidate')}")
    t = report.get('table') or {}
    lines.append('')
    lines.append('## All 118 boards')
    lines.append('| split | starting configuration | after 1/2 | PCBRouteAgent |')
    lines.append('|---|---|---|---|')
    for s in ('d3a', 'd3b', 'd3c', 'all'):
        r = (t.get('record') or {}).get(s) or {}
        c = (t.get('candidate') or {}).get(s) or {}
        pc = f"{PCBR[s]}%" if s in PCBR else '-'
        lines.append(f"| {s} | {r.get('clean')}/{r.get('n')} ({r.get('rate')}%) | "
                     f"{c.get('clean')}/{c.get('n')} ({c.get('rate')}%) | {pc} |")
    path = os.path.join(run, 'CAMPAIGN.md')
    with open(path, 'w', encoding='utf-8') as fh:
        fh.write('\n'.join(lines) + '\n')
    print('\n'.join(lines), flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--run', default=os.path.join(HERE, 'arms', 'gen0'))
    ap.add_argument('--gens', type=int, default=3)
    ap.add_argument('--max-candidates', type=int, default=3)
    ap.add_argument('--workers', type=int, default=6)
    ap.add_argument('--budget', type=int, default=300)
    ap.add_argument('--timeout', type=int, default=240)
    ap.add_argument('--no-llm', action='store_true')
    ap.add_argument('--skip-final', action='store_true')
    args = ap.parse_args()
    run = args.run
    t0 = time.time()

    # ---- gen 0: complete, then read --------------------------------
    arms = arms_run.gen0_arms()
    coreset = json.load(open(os.path.join(run, 'coreset.json'), encoding='utf-8'))
    arms_run.adopt_cert_fingerprint(run, arms, coreset)
    arms_run.evaluate(run, coreset, arms, args.budget, args.timeout,
                      args.workers, 'gen0')
    d0 = D.analyse(run)
    d0.pop('per_board', None)
    print(f"gen0: best single arm {d0['best_single_arm']}, oracle "
          f"{d0['oracle_any_arm']}, headroom {d0['selector_headroom']}, "
          f"instrument-bad {d0['instrument_bad_cells']}", flush=True)

    memory = Memory(os.path.join(run, 'memory.jsonl'))
    loop = MetaDevLoop(memory=memory, allow_llm=not args.no_llm,
                       max_candidates=args.max_candidates)
    c2 = MetaMetaLoop(memory=memory)
    # d0 reports how many boards the best arm cleans; the arm itself is
    # picked by the same rule (most clean, then fastest)
    base_name = best_single(D.load_cells(run), factorial())
    base = [a for a in factorial() if a.name == base_name][0]
    memory.append('constraint', rule='held-out boards are never used to choose')
    memory.append('plan', stage='gen0', base=base.name,
                  coreset=len(coreset), noise=d0.get('noise_aa'))

    # ---- generations -----------------------------------------------
    gens, chain, no_admit = [], [], 0
    for g in range(1, args.gens + 1):
        packets = load_packets(run)
        res = loop.generation(
            packets, base,
            evaluator=make_evaluator(run, loop, base, coreset, g,
                                     args.budget, args.timeout, args.workers),
            generation=g)
        entry = res.as_dict()
        entry['diagnosis'] = {
            'instrument_ok': res.diagnosis.get('instrument_ok'),
            'patterns': [{'name': p.name, 'count': p.count,
                          'variables': [n for n, _ in p.variables],
                          'evidence': p.evidence}
                         for p in res.diagnosis.get('patterns', [])]}
        gens.append(entry)
        print(f"gen {g}: {res.status}; proposed {len(res.proposals)}; "
              f"admitted {res.admitted}", flush=True)
        diff = c2.improve(gens)
        for v in diff.changes.get('retire', []):
            memory.append('constraint', retire=v,
                          why='tried at least twice, never admitted')
        memory.append('plan', stage=f'gen{g}', harness=diff.changes,
                      admitted=res.admitted)
        if res.admitted:
            scored = [(r['wins'] - r['losses'], -r['losses'], r['candidate'])
                      for r in res.results if r['candidate'] in res.admitted]
            base = loop.arm(max(scored)[2])
            chain.append(base.name)
            no_admit = 0
        elif res.status in ('ok', 'no_proposal'):
            no_admit += 1
            if no_admit >= 2:
                print('two generations without an admitted change: stopping',
                      flush=True)
                break
        else:
            break
    final = base
    packets = load_packets(run)
    selector = dict(MetaDevLoop.fit([p for p in packets if '~' not in p.arm]),
                    default=final.name)

    report = {'run': run, 'gen0': d0, 'generations': gens, 'chain': chain,
              'final_arm': final.name, 'selector': selector,
              'proposer': 'llm+rules' if not args.no_llm else 'rules',
              'memory_entries': len(memory.entries()), 'memory_chain_ok': memory.chain_ok}
    if not args.skip_final:
        # in-sample on the coreset: with or without the cluster rules
        ev_sel, verdict_sel, table_sel = final_confirmation(
            run, args, final, record_arm(), selector, coreset, True)
        report.update(evidence=ev_sel, verdict=verdict_sel, table=table_sel)
        memory.append('judgment', ok=verdict_sel['ok'],
                      reasons=verdict_sel['reasons'], arm=final.name)
    report['seconds'] = round(time.time() - t0, 1)
    arms_run.atomic_json(os.path.join(run, 'campaign.json'), report)
    write_report(run, report)


if __name__ == '__main__':
    main()
