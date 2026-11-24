#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""End-to-end on ONE board: certificate -> error model -> knowledge ->
LLM-written tool -> sandbox -> real evaluation with repeats.

    python e2e_one.py <board_dir> [--no-llm]

This is the smallest honest test of the action side: with the base model
fixed, does the loop produce a tool that closes a board no existing
primitive closes?  Every step is printed with its evidence.
"""
import argparse
import json
import os
import shutil
import sys
import tempfile
import time
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
FLUX = os.path.join(ROOT, '..', 'flux-neurotransmitter')
sys.path.insert(0, FLUX)
sys.path.insert(0, HERE)

import arms_run  # noqa: E402
from fluxpcb.knowledge.index import BM25Index, default_corpus  # noqa: E402
from fluxpcb.meta import distinguish as D  # noqa: E402
from fluxpcb.meta.error_model import counterfactual_of, induce  # noqa: E402
from fluxpcb.meta.retrieve import draft_card, retrieve  # noqa: E402
from fluxpcb.meta.tool_evolve import build_context, propose  # noqa: E402
from fluxpcb.meta.tool_gate import Sandbox, check_static  # noqa: E402
from fluxpcb.repair_drc import run_drc  # noqa: E402
from fluxpcb.tools.builtins import builtins  # noqa: E402

GEN0 = 'C:/zcode_pcbworld/arms/gen0'


def base_cell(board):
    idx = {(c['board'], c['arm']): c for c in D.load_cells(GEN0)}
    for arm in ('gate+ripup', 'direct+ripup', 'gate+batch'):
        c = idx.get((board, arm))
        if c and c.get('outcome') == 'fail' and c.get('cert'):
            return c
    return None


def base_pcb(board):
    c = base_cell(board)
    if not c:
        return None
    pcb = os.path.join(os.path.dirname(os.path.join(GEN0, c['cert'])),
                       f'{board}.kicad_pcb')
    return pcb if os.path.exists(pcb) else None


def failures_of(board):
    """cert['failures'] from a fresh base-arm run (the stored ones predate
    the cert plumbing, so re-run the base arm once)."""
    cert = os.path.join(GEN0, 'runs', 'e2e', board, 'cert.json')
    if not os.path.exists(cert):
        boards = json.load(open(os.path.join(HERE, 'boards_full.json'),
                                encoding='utf-8'))
        entry = [b for b in boards if b['dir'] == board][0]
        arm = [a for a in arms_run.gen0_arms() if a.name == 'gate+batch'][0]
        arms_run.evaluate(os.path.join(GEN0, 'e2e'), [entry], [arm], 600, 240,
                          1, 'e2e')
        cert = os.path.join(GEN0, 'e2e', 'runs', arm.digest()[:12], board,
                            'cert.json')
    body = json.load(open(cert, encoding='utf-8'))
    return [dict(e, board=board, features=body.get('features') or {})
            for e in body.get('failures') or []], body


def drc_counts(pcb):
    rep = pcb + '.drc.json'
    d = run_drc(pcb, rep)
    if d.get('ok') is False:
        return None
    body = json.load(open(rep, encoding='utf-8'))
    errs = sum(1 for v in body.get('violations') or []
               if v.get('severity') == 'error' and not v.get('excluded'))
    un = sum(1 for u in body.get('unconnected_items') or []
             if not u.get('excluded'))
    return errs, un


def tool_args(pcb, cert_item, prior=None):
    """What a tool is called with: the board, the failing item's numbers
    (net, position, class, requirement) and what earlier tools reported."""
    cert_item = cert_item or {}
    item = dict(cert_item.get('item') or {})
    pos = list(item.pop('pos', None) or [None, None])
    attempt = dict(cert_item.get('attempt') or {})
    requirement = dict(cert_item.get('requirement') or {})
    args = {'board': pcb, 'repeats': 1,
            'x': pos[0], 'y': pos[1], 'pos': pos, 'item': item,
            'attempt': attempt, 'requirement': requirement,
            'why_not': dict(cert_item.get('why_not') or {}),
            'prior': prior or []}
    for k in ('net', 'width_mm', 'clearance_mm'):
        for src in (item, attempt, requirement):
            if src.get(k) is not None:
                args[k] = src[k]
                break
    if 'width_mm' in args and 'width' not in args:
        args['width'] = args['width_mm']
    return args


def evaluate_tool(mod, board, pro_src, cert_item=None, repeats=2,
                  prior=None, probe_only=False):
    """Copy the failing board, run the tool, judge by waiver-free DRC.

    `probe_only` runs the tool once to collect what it reports: an
    analysis tool's output is the next round's evidence.
    """
    src = base_pcb(board)
    outs = []
    for i in range(1 if probe_only else repeats):
        tmp = tempfile.mkdtemp(prefix='e2e_')
        pcb = os.path.join(tmp, f'{board}.kicad_pcb')
        shutil.copy(src, pcb)
        shutil.copy(pro_src, os.path.join(tmp, f'{board}.kicad_pro'))
        before = drc_counts(pcb)
        t0 = time.time()
        try:
            res = mod.run(tool_args(pcb, cert_item, prior))
            err = None
        except Exception as e:
            res, err = None, f'{type(e).__name__}: {e}'
        after = drc_counts(pcb)
        if err:
            v = 'tool_error'
        elif after is None:
            v = 'drc_failed'
        elif after == (0, 0):
            v = 'clean'
        elif before and after < before:
            v = 'improved'
        else:
            v = 'no_gain'
        outs.append({'verdict': v, 'before': before, 'after': after,
                     'seconds': round(time.time() - t0, 1), 'error': err,
                     'result': (str(res)[:200] if res is not None else None)})
        print(f'   run {i + 1}: {v} {before} -> {after} '
              f'{outs[-1]["seconds"]}s {err or ""}', flush=True)
    return outs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('board')
    ap.add_argument('--no-llm', action='store_true')
    ap.add_argument('--repeats', type=int, default=4)
    ap.add_argument('--rounds', type=int, default=3)
    args = ap.parse_args()
    board = args.board
    pro = os.path.join(HERE, 'boards', board, 'circuit.kicad_pro')
    cell = base_cell(board)
    print(f'== {board}: base outcome {cell and cell.get("outcome")}', flush=True)

    certs, body = failures_of(board)
    print(f'failure certificates: {len(certs)} '
          f'{Counter((c["operator"], c["outcome"]) for c in certs)}', flush=True)
    if not certs:
        print('no failure certificates: nothing to induce')
        return
    models = induce(certs, at=time.strftime('%Y-%m-%d %H:%M'))
    for m in models[:4]:
        print(f'  model {m.model_id()} support={m.support} '
              f'{m.conditions}\n     counterfactual: {m.counterfactual}',
              flush=True)
    model = models[0]

    index = BM25Index(default_corpus())
    passages = retrieve(model, index, allow_online=not args.no_llm)
    print(f'knowledge passages: {len(passages)}', flush=True)
    for p in passages[:4]:
        print(f'   [{p.get("score")}] {str(p.get("path") or p.get("url"))[-60:]}'
              f' :: {(p.get("snippet") or "")[:110]}', flush=True)
    card = draft_card(model, passages, allow_llm=not args.no_llm)
    print(f'card: {card and card.technique} | sources: '
          f'{card and len(card.sources)}', flush=True)

    reg = builtins()
    proposal = propose(model, [c for c in certs][:6], passages, reg,
                       nearest_source='', allow_llm=not args.no_llm)
    errs = proposal.validate()
    print(f'== round 1: {proposal.kind} "{proposal.name}" '
          f'by {proposal.proposer} | {proposal.reasoning[:300]}'
          f'\n   address: {proposal.address}'
          f'\n   static: {"ok" if not errs else errs}', flush=True)
    if errs or proposal.kind == 'skill_update':
        print('no executable tool proposed; stopping')
        return
    sb = Sandbox(os.path.join(FLUX, 'sandbox'))
    rounds, prior, closed = [], [], False
    item = next((c for c in certs if c['operator'] == 'connect_unconnected'),
                certs[0])
    for rnd in range(1, args.rounds + 1):
        if rnd > 1:
            # the next proposal sees what the previous tool reported
            proposal = propose(model, certs[:6], passages, reg,
                               nearest_source=json.dumps(prior[-1:])[:2500],
                               allow_llm=not args.no_llm)
            errs = proposal.validate()
            print(f'== round {rnd}: {proposal.kind} "{proposal.name}" '
                  f'by {proposal.proposer} | {proposal.reasoning[:260]}',
                  flush=True)
            if errs or proposal.kind == 'skill_update':
                print('   no executable tool; stopping', errs[:2])
                break
        print(f'--- round {rnd} tool source (head) ---\n'
              + proposal.source[:900] + '\n--- end ---', flush=True)
        sb.write(f'{proposal.name}_r{rnd}', proposal.source)
        try:
            mod = sb.load(f'{proposal.name}_r{rnd}')
        except Exception as e:
            print(f'   sandbox load failed: {type(e).__name__}: {e}',
                  flush=True)
            prior.append({'tool': proposal.name, 'error': str(e)[:300]})
            continue
        # L1 smoke + what the tool reports (analysis tools speak here)
        probe = evaluate_tool(mod, board, pro, item, prior=prior,
                              probe_only=True)
        print(f'   probe: {probe[0]["verdict"]} {probe[0]["before"]} -> '
              f'{probe[0]["after"]} result={str(probe[0]["result"])[:300]}',
              flush=True)
        if probe[0]['verdict'] == 'tool_error':
            prior.append({'tool': proposal.name,
                          'error': probe[0].get('error')})
            continue
        outs = evaluate_tool(mod, board, pro, item, repeats=args.repeats,
                             prior=prior)
        clean = sum(1 for o in outs if o['verdict'] == 'clean')
        rounds.append({'tool': proposal.name, 'runs': outs, 'clean': clean})
        prior.append({'tool': proposal.name, 'clean': f'{clean}/{len(outs)}',
                      'last_result': outs[-1].get('result')})
        if clean >= max(1, len(outs) - 1):
            closed = True
            break
    print(f'VERDICT {board}: closed={closed} rounds='
          f'{[(r["tool"], r["clean"]) for r in rounds]}', flush=True)
    arms_run.atomic_json(os.path.join(GEN0, f'e2e_{board[:4]}.json'), {
        'board': board, 'model': model.as_dict(), 'passages': passages[:6],
        'rounds': rounds, 'closed': closed, 'prior': prior})


if __name__ == '__main__':
    main()
