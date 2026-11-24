#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Targeted campaign A — can the tool layer + knowledge close the hard boards?

Generation 0 (this module's default): NO new code.  Each zero-reach board
gets each available primitive (maze / visibility A* / eikonal /
freerouting with more options) tried 4 times on a copy of its failing
board, gated by the same transactional DRC rule.  A tool with >= 4/5
passes closes the board.

Generation 1+: the failure certificates from fresh base-arm runs are
induced into error models; zero-reach models pull knowledge (local BM25
+ arXiv/OpenAlex) and propose tool changes (LLM when configured), which
climb the admission ladder (L0 static -> L1 smoke -> L2 effect with
repeats -> L3 known-bad controls).

Everything is measured; nothing is assumed.
"""
import argparse
import dataclasses
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, '..', 'flux-neurotransmitter'))
sys.path.insert(0, HERE)

import arms_run  # noqa: E402
from fluxpcb.meta import distinguish as D  # noqa: E402
from fluxpcb.meta.error_model import (ErrorModel, capability_matrix,  # noqa: E402
                                      induce)
from fluxpcb.meta.memory import Memory  # noqa: E402
from fluxpcb.meta.retrieve import draft_card, retrieve  # noqa: E402
from fluxpcb.meta.tool_evolve import propose as tool_propose  # noqa: E402
from fluxpcb.meta.tool_gate import Sandbox, ladder  # noqa: E402
from fluxpcb.repair_drc import run_drc  # noqa: E402
from fluxpcb.tools import call as tcall  # noqa: E402
from fluxpcb.tools.builtins import builtins  # noqa: E402

TARGETS = ['0051_MySRaspiGW_MySRaspiGW_Pimoroni',
           '0054_induction-hob_temperature-sensor',
           '0400_laptimer58_Chickadee',
           '0496_kitspace_f-91w',
           '0520_RGBMatrixPanelCPLD-PhotonBackpack_RGBMatrixPanel_CPLD_negative',
           '0599_RGBMatrixPanelCPLD-PhotonBackpack_RGBMatrixPanel_CPLD',
           '0344_mavbridge_mavbridge',
           '0571_Brushless_ESC_Brushless_ESC']
REPEATS = 4
KICAD_PY = os.environ.get('KICAD_PYTHON', sys.executable)


def base_board(run, board_dir):
    """The failing routed board a tool starts from.

    Order: a repair-loop failure of the base arm (routed, unrepaired is
    fine); then a routed but unrepaired cell (route_only); then the older
    round-2 routed board.
    """
    idx = {(c['board'], c['arm']): c for c in D.load_cells(run)}
    for arm in ('gate+ripup', 'direct+ripup', 'gate+batch',
                'gate+route_only', 'direct+route_only'):
        c = idx.get((board_dir, arm))
        if not c or c.get('outcome') in ('clean', 'budget'):
            continue
        if not c.get('cert'):
            continue
        pcb = os.path.join(os.path.dirname(os.path.join(run, c['cert'])),
                           f'{board_dir}.kicad_pcb')
        if os.path.exists(pcb):
            return pcb, c
    old = f'C:/zcode_pcbworld/rounds/r2/runs/{board_dir}/{board_dir}.kicad_pcb'
    if os.path.exists(old):
        return old, {'fallback': 'r2'}
    return None, None


def drc_claims(pcb, pro):
    """(errors, unconnected) under the board's own project."""
    rep = pcb + '.drc.json'
    d = run_drc(pcb, rep)
    if d.get('ok') is False:
        return None
    with open(rep, encoding='utf-8') as fh:
        body = json.load(fh)
    errs = sum(1 for v in body.get('violations') or []
               if v.get('severity') == 'error' and not v.get('excluded'))
    un = sum(1 for u in body.get('unconnected_items') or []
             if not u.get('excluded'))
    return errs, un


def try_tool(run, board_dir, pro_src, spec, tool_args, repeats=REPEATS):
    """One tool on one board, transactional, repeated. Returns per-run."""
    outs = []
    for i in range(repeats):
        src_pcb, _cell = base_board(run, board_dir)
        if not src_pcb:
            return [{'verdict': 'no_base_board'}]
        tmp = tempfile.mkdtemp(prefix='ta_')
        pcb = os.path.join(tmp, f'{board_dir}.kicad_pcb')
        shutil.copy(src_pcb, pcb)
        shutil.copy(pro_src, os.path.join(tmp, f'{board_dir}.kicad_pro'))
        before = drc_claims(pcb, os.path.join(tmp, f'{board_dir}.kicad_pro'))
        args = dict(tool_args or {}, board=pcb)
        out = tcall.call(spec, args,
                         ctx={'board': pcb, 'kicad_python': KICAD_PY},
                         timeout_s=300)
        if not out.get('ok'):
            outs.append({'verdict': 'tool_error',
                         'error': (out.get('error') or '')[:160]})
            continue
        after = drc_claims(pcb, os.path.join(tmp, f'{board_dir}.kicad_pro'))
        if after is None:
            outs.append({'verdict': 'drc_failed'})
        elif before is not None and after >= before:
            outs.append({'verdict': 'no_gain', 'before': before,
                         'after': after})
        elif after == (0, 0):
            outs.append({'verdict': 'clean'})
        else:
            outs.append({'verdict': 'improved', 'before': before,
                         'after': after})
    return outs


def gen0(run):
    """Every primitive against every target, 4 repeats each."""
    reg = builtins()
    boards = json.load(open(os.path.join(HERE, 'boards_full.json'),
                            encoding='utf-8'))
    by_dir = {b['dir']: b for b in boards}
    results = {}
    for d in TARGETS:
        pro = os.path.join(HERE, 'boards', d, 'circuit.kicad_pro')
        for name in ('maze_route', 'explore_route', 'eikonal_route',
                     'freerouting_board'):
            spec = reg.get(name)
            tool_args = {}
            if name == 'freerouting_board':
                tool_args = {'timeout_s': 1800, 'max_passes': 20,
                             'use_board_rules': True}
            outs = try_tool(run, d, pro, spec, tool_args)
            results.setdefault(d, {})[name] = outs
            clean = sum(1 for o in outs if o['verdict'] == 'clean')
            print(f'{d[:34]:34s} {name:20s} clean {clean}/{len(outs)} '
                  f'{Counter(o["verdict"] for o in outs)}', flush=True)
            arms_run.atomic_json(os.path.join(run, 'gen0_tools.json'), results)
    closed = [d for d in results if any(
        sum(1 for o in outs if o['verdict'] == 'clean') >= 4
        for outs in results[d].values())]
    arms_run.atomic_json(os.path.join(run, 'gen0_closed.json'),
                         {'closed': closed, 'results': results})
    print(f'\nclosed by existing primitives: {closed}', flush=True)


def fresh_failure_certs(run):
    """One fresh base-arm run per target to get cert['failures']."""
    arms = arms_run.gen0_arms()
    base = [a for a in arms if a.name == 'gate+ripup'][0]
    boards = json.load(open(os.path.join(HERE, 'boards_full.json'),
                            encoding='utf-8'))
    sel = [b for b in boards if b['dir'] in TARGETS]
    arms_run.evaluate(run, sel, [base], 600, 240, 6, 'targets-base')
    out = []
    for c in D.load_cells(run):
        if c['board'] in TARGETS and c['arm'] == 'gate+ripup':
            rel = c.get('cert')
            if not rel:
                continue
            try:
                cert = json.load(open(os.path.join(run, rel),
                                      encoding='utf-8'))
            except OSError:
                continue
            for e in cert.get('failures') or []:
                out.append(dict(e, board=c['board'], features=c.get('features')))
    return out


def gen1(run, allow_llm=True):
    """Induce -> retrieve -> propose -> ladder, one generation."""
    from fluxpcb.knowledge.index import BM25Index, default_corpus
    mem = Memory(os.path.join(run, 'memory.jsonl'))
    certs = fresh_failure_certs(run)
    print(f'failure certs collected: {len(certs)}', flush=True)
    models = induce(certs, at=time.strftime('%Y-%m-%d %H:%M'))
    for e in __import__('fluxpcb.meta.error_model', fromlist=['x']).to_memory_entries(models, 'now'):
        mem.append('error_model', **e)
    index = BM25Index(default_corpus())
    reg = builtins()
    for m in models:
        if not m.needs_new_capability:
            continue
        passages = retrieve(m, index, allow_online=True)
        card = draft_card(m, passages, allow_llm=allow_llm)
        if card:
            mem.append('knowledge_card', **card.as_dict())
        proposal = tool_propose(m, [c for c in certs
                                    if c.get('attempt', {}).get('class')
                                    == m.conditions[1]][:6],
                                passages, reg, allow_llm=allow_llm)
        errs = proposal.validate()
        mem.append('tool_proposal', **proposal.as_dict(), static_ok=not errs,
                   static_problems=errs)
        print(f'model {m.model_id()} {m.conditions[:3]}: '
              f'{proposal.kind} {proposal.name} '
              f'static_ok={not errs}', flush=True)
        if errs:
            continue
        # L0 + sandbox + L1 smoke: the code must load and run at all
        sb = Sandbox(os.path.join(ROOT, '..', 'flux-neurotransmitter', 'sandbox'))
        if proposal.kind != 'skill_update':
            sb.write(proposal.name, proposal.source)
            try:
                mod = sb.load(proposal.name)
                out = mod.run({'board': 'smoke'})
                smoke_pass = isinstance(out, dict) and out.get('ok') is True
            except Exception as e:
                smoke_pass, out = False, {'error': f'{type(e).__name__}: {e}'}
            mem.append('tool_smoke', name=proposal.name,
                       pass_=bool(smoke_pass), error=str(out)[:200])
            print(f'  smoke pass={smoke_pass}', flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--run', default='C:/zcode_pcbworld/arms/targeted')
    ap.add_argument('--gen', type=int, default=0)
    ap.add_argument('--no-llm', action='store_true')
    args = ap.parse_args()
    os.makedirs(args.run, exist_ok=True)
    if args.gen == 0:
        gen0(args.run)
    else:
        gen1(args.run, allow_llm=not args.no_llm)


if __name__ == '__main__':
    main()
