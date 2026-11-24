#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Run fluxpcb_hybrid on the PCBWorld-Bench boards and score them.

For each board in boards.json:
  1. copy circuit.reference.kicad_pcb -> runs/<dir>/<dir>.kicad_pcb
  2. route with the Freerouting backend (board-true DSN rules)
  3. refill zones + 7 repair operators (multi-round)
  4. leave runs/<dir>/<dir>.kicad_pcb + <dir>.kicad_pro for pcbworld/score.py

Usage (from autorouter-bench):
    python work/pcbworld/run_bench.py [--splits d3a,d3b,d3c] [--timeout 300] [--limit N]
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))  # autorouter-bench
sys.path.insert(0, os.path.join(ROOT, "..", "flux-neurotransmitter"))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from common import KICAD_PYTHON  # noqa: E402

os.environ.setdefault("KICAD_PYTHON", KICAD_PYTHON)

# Guard against a stale duplicate package: the benchmark must run the same
# fluxpcb the contract layer was verified against.
import fluxpcb as _fluxpcb  # noqa: E402
print('fluxpcb:', _fluxpcb.__file__, flush=True)
assert 'flux-neurotransmitter' in os.path.abspath(_fluxpcb.__file__), _fluxpcb.__file__


def strip_board(src_pcb, work_dir, pro_src):
    """strip -> <work_dir>/unrouted.kicad_pcb + board.dsn

    The source board is copied to <work_dir>/board.kicad_pcb with the guide
    project beside it under the SAME basename, so both pcbnew (during strip)
    and our DSN patch read the board's real netclasses.
    """
    paired = os.path.join(work_dir, 'board.kicad_pcb')
    shutil.copy(src_pcb, paired)
    shutil.copy(pro_src, os.path.join(work_dir, 'board.kicad_pro'))
    helper = os.path.join(ROOT, "scripts", "kicad_board.py")
    r = subprocess.run([KICAD_PYTHON, helper, "strip", paired, work_dir],
                       capture_output=True, text=True, timeout=600)
    out = os.path.join(work_dir, "unrouted.kicad_pcb")
    return out if os.path.exists(out) else None


def _rule_register():
    import fluxpcb.contract as _contract
    repo = os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(_contract.__file__))))
    return _contract.RuleRegister.default(
        os.path.join(repo, 'boards', 'rules', '*.json'),
        os.path.join(repo, 'fluxpcb', 'knowhow_constraints.json'))


def route_and_repair(paired_pcb, out_pcb, pro_src, timeout_s,
                     ledger_dir=None, board_family='', hook=None,
                     enable_planning=True, policy=None):
    """Route the paired board (the backend strips it internally) and repair.

    Repairs run as transactions: a batch commits only when the post-state's
    hard-claim vector improves and introduces no new violation type; otherwise
    the board is restored byte-for-byte.  Zone fill happens inside the
    evaluation, so a verdict always refers to the state it measured.
    """
    from fluxpcb.router.freerouting import FreeroutingBackend
    from fluxpcb.repair_drc import (reroute_residues_isolated, run_drc,
                                    repair_with_operators,
                                    save_preserving_project)
    from fluxpcb.contract import Executor, RuleModel, ScopeBuilder
    from fluxpcb.verifiers.binding import (bind_report, check_binding,
                                           project_identity, sha256_file)
    from fluxpcb.verifiers.live import record_board
    from fluxpcb.verifiers.residue import classify_drc
    from fluxpcb.harness import load_hook
    from fluxpcb.verifiers.txn import claims_from_drc, transactional_repair
    import pcbnew

    if policy is not None:
        # The policy owns the harness (operator set + budget) and the
        # planning switch; an explicit hook still wins for A/B tests.
        enable_planning = bool(policy.enabled_planning)
        if hook is None:
            from fluxpcb.harness import HarnessHook
            hook = HarnessHook(policy.harness_policy())
    if hook is None:
        hook = load_hook()

    # C4: build the typed plan BEFORE routing.  The capacity claim is a
    # hard gate: a proven-infeasible stackup is not sent to the router
    # (H7: 0 traces/channel; every search converges to the same residue).
    plan = None
    plan_error = None
    if enable_planning:
        try:
            from fluxpcb.design_agent.planner import build_plan
            plan = build_plan(paired_pcb)
        except Exception as e:      # planning is evidence, not a crash
            plan_error = f'{type(e).__name__}: {e}'

    backend = FreeroutingBackend()
    if plan is not None and plan.capacity.verdict == 'infeasible':
        cert = {
            'board': os.path.basename(out_pcb),
            'route': {'routed': False, 'seconds': 0.0, 'passes': 0,
                      'error': 'capacity_infeasible'},
            'plan_digest': plan.digest(),
            'plan_claims': plan.claims,
            'plan_error': plan_error,
            'capacity': plan.capacity.as_dict(),
            'plan_consumed': {'capacity_gate': True, 'pre_route': False},
            'policy_digest': (policy.digest() if policy is not None
                              else None),
            'board_worst': 'fail',
            'readiness': 'infeasible',
        }
        return {'routed': False, 'error': 'capacity_infeasible',
                'seconds': 0.0, 'cert': cert}

    route_input = paired_pcb
    pre = None
    if plan is not None and plan.pre_routed_nets:
        # Plan consumption: escape nets are drawn first as fixed copper,
        # so the global router only has to fill the remaining nets.
        try:
            from fluxpcb.design_agent.preroute import pre_route
            pre_src = os.path.splitext(paired_pcb)[0] + '.pre.kicad_pcb'
            shutil.copy(paired_pcb, pre_src)
            src_pro = os.path.splitext(paired_pcb)[0] + '.kicad_pro'
            if os.path.exists(src_pro):
                shutil.copy(src_pro,
                            os.path.splitext(pre_src)[0] + '.kicad_pro')
            pre = pre_route(pre_src, plan)
            route_input = pre_src
        except Exception as e:
            pre = {'error': f'{type(e).__name__}: {e}'}

    fr = backend.route(route_input, out_pcb, timeout_s=timeout_s, use_board_rules=True)
    cert = {
        'board': os.path.basename(out_pcb),
        'route': {'routed': fr.routed, 'seconds': fr.seconds,
                  'passes': fr.passes, 'error': fr.error},
        'rule_audit_pre': fr.rule_audit,
        'rule_patch': fr.patch,
        'rule_congruent': fr.rule_congruence,
        'plan_digest': plan.digest() if plan is not None else None,
        'plan_claims': plan.claims if plan is not None else None,
        'capacity': (plan.capacity.as_dict() if plan is not None else None),
        'plan_error': plan_error,
        'plan_consumed': {
            'capacity_gate': plan is not None,
            'pre_route': bool(pre and pre.get('edges_routed')),
            'escape_regions': (len(plan.escape_regions)
                               if plan is not None else 0),
            'pre_routed_nets': (len(plan.pre_routed_nets)
                                if plan is not None else 0),
        },
        'pre_route': pre,
        'policy_digest': (policy.digest() if policy is not None else None),
    }
    if not fr.routed:
        return {'routed': False, 'error': fr.error, 'seconds': fr.seconds,
                'cert': cert}

    # ensure the correct project sits beside the routed board
    out_pro = os.path.splitext(out_pcb)[0] + '.kicad_pro'
    shutil.copy(pro_src, out_pro)

    holder = {'drc_json': None}

    def evaluate(path):
        b = pcbnew.LoadBoard(path)
        if b is not None:
            filler = pcbnew.ZONE_FILLER(b)
            filler.Fill(b.Zones())
            # Do not let the save rewrite the project: the DRC that follows
            # reads it from disk in another process.
            save_preserving_project(b, path)
            shutil.copy(pro_src, os.path.splitext(path)[0] + '.kicad_pro')
        drc_json = path + '.drc.json'
        d = run_drc(path, drc_json)
        if d.get('ok') is False:
            raise RuntimeError(d.get('error', 'DRC failed'))
        # Bind the report to this exact board state so a stale/foreign
        # zero-error report cannot pass.
        bind_report(path, drc_json)
        holder['drc_json'] = drc_json
        return claims_from_drc(d, rule_congruent=fr.rule_congruence)

    def action(path):
        if not holder['drc_json']:
            return {}
        return repair_with_operators(path, holder['drc_json'])

    before = evaluate(out_pcb)
    rep = transactional_repair(out_pcb, action, evaluate, max_rounds=3,
                               initial=before, hook=hook)
    after = evaluate(out_pcb)

    # P4: if width/clearance residues remain, try a transactional net-level
    # re-route.  A worse result is rolled back by the same gate.
    reroute_rep = None
    if after.total > 0 and holder['drc_json'] and not (
            after.error_types - {'track_width', 'clearance'}):
        def reroute_action(path):
            return reroute_residues_isolated(path, holder['drc_json'],
                                             timeout_s=timeout_s)
        reroute_rep = transactional_repair(
            out_pcb, reroute_action, evaluate, max_rounds=1,
            initial=after, hook=hook)
        after = evaluate(out_pcb)
    if reroute_rep is not None:
        cert['reroute'] = reroute_rep.as_dict()

    with open(holder['drc_json'], encoding='utf-8') as f:
        final_drc = json.load(f)
    cert['claims_before'] = before.as_dict()
    cert['claims_after'] = after.as_dict()
    cert['txn'] = rep.as_dict()
    cert['residue'] = classify_drc(final_drc)
    shutil.copy(pro_src, out_pro)

    # ---- scope + live ledger (P1) ------------------------------------
    try:
        reg = _rule_register()
        dsn_path = os.path.join(os.path.dirname(os.path.abspath(paired_pcb)),
                                'board.dsn')
        model = None
        try:
            pro_json = json.load(open(pro_src, encoding='utf-8'))
            if os.path.exists(dsn_path):
                dsn_model = RuleModel.from_dsn(
                    open(dsn_path, encoding='utf-8', errors='replace').read())
                model = RuleModel.from_project(
                    pro_json, net_names=sorted(dsn_model.all_nets()))
            else:
                model = RuleModel.from_project(pro_json)
        except Exception:
            model = None
        assumptions = []
        patch = cert.get('rule_patch') or {}
        if patch.get('default_rule_injected'):
            assumptions.append('default_rule_policy')
        if any('unclaimed' in n for n in (patch.get('notes') or [])):
            assumptions.append('netclass_unclaimed_nets')
        executor = Executor(backend='freerouting',
                            version=os.path.basename(backend.jar or ''),
                            jar=backend.jar or '', java=backend.java or '',
                            timeout_s=timeout_s)
        scope = ScopeBuilder(
            reg.digest(), os.environ.get('KICAD_CLI', 'kicad-cli')).build(
            out_pcb, pro_src, dsn_path, executor=executor,
            claim_set=('rule_congruence', 'project_identity',
                       'report_binding', 'rule_scope', 'drc_clean',
                       'unconnected_zero'),
            assumptions=tuple(assumptions), board_family=board_family,
            register=reg, model=model,
            extra={'rule_patch_verified': bool(patch.get('verified'))})
        cert['scope'] = scope.as_dict(include_ids=True)
        cert['rule_register'] = reg.report(model)
        cert['assumptions'] = assumptions
        if ledger_dir:
            os.makedirs(ledger_dir, exist_ok=True)
            lp = os.path.join(ledger_dir, 'ledger.jsonl')
            if os.path.exists(lp):
                os.remove(lp)
            live = record_board(
                lp, scope, rule_verified=bool(fr.rule_congruence),
                project_ctx=project_identity(pro_src,
                                             expected_sha=sha256_file(pro_src)),
                binding_ctx=check_binding(out_pcb,
                                          out_pcb + '.drc.json.binding.json'),
                claims=after, register=reg, model=model,
                assumptions=assumptions)
            cert['ledger'] = live
        if hook is not None:
            cert['harness'] = hook.as_dict()
            cert['board_worst'] = live.get('board_worst')
            cert['readiness'] = live.get('worst')
    except Exception as e:
        cert['scope_error'] = str(e)
    return {'routed': True, 'seconds': fr.seconds, 'passes': fr.passes,
            'cert': cert}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--splits', default='d3a,d3b,d3c')
    ap.add_argument('--timeout', type=int, default=300)
    ap.add_argument('--limit', type=int, default=0)
    ap.add_argument('--manifest', default=os.path.join(HERE, 'boards.json'))
    ap.add_argument('--max-new', type=int, default=0,
                    help='process at most N not-yet-done boards, then exit')
    ap.add_argument('--append', action='store_true',
                    help='resume: keep existing run_rows.json and skip routed boards')
    ap.add_argument('--runs-dir', default='',
                    help='round-scoped runs directory (default: HERE/runs)')
    ap.add_argument('--rows', default='',
                    help='round-scoped rows file (default: HERE/run_rows.json)')
    ap.add_argument('--policy', default='',
                    help='PolicyArtifact JSON: harness ops/budget + planning')
    args = ap.parse_args()
    policy = None
    if args.policy:
        from fluxpcb.meta.policy import PolicyArtifact
        policy = PolicyArtifact.from_json(args.policy)
    want = set(args.splits.split(','))

    boards = json.load(open(args.manifest))
    sel = [b for b in boards if b['set'] in want]
    if args.limit:
        sel = sel[:args.limit]
    runs = args.runs_dir or os.path.join(HERE, 'runs')
    rows = []
    rows_path = args.rows or os.path.join(HERE, 'run_rows.json')
    rows_parent = os.path.dirname(rows_path)
    if rows_parent:
        os.makedirs(rows_parent, exist_ok=True)
    if args.append and os.path.exists(rows_path):
        rows = json.load(open(rows_path))
        done = {r['dir'] for r in rows if r.get('routed')}
        sel = [b for b in sel if b['dir'] not in done]
        print(f'resume: {len(done)} done, {len(sel)} to run', flush=True)
    if args.max_new:
        sel = sel[:args.max_new]
    print(f'running {len(sel)} boards (splits {sorted(want)})', flush=True)
    for i, b in enumerate(sel, 1):
        d = b['dir']
        src_dir = os.path.join(HERE, 'boards', d)
        src_pcb = os.path.join(src_dir, 'circuit.reference.kicad_pcb')
        pro_src = os.path.join(src_dir, 'circuit.kicad_pro')

        board_run = os.path.join(runs, d)
        os.makedirs(board_run, exist_ok=True)
        out_pcb = os.path.join(board_run, f'{d}.kicad_pcb')
        t0 = time.time()
        try:
            # Pair the reference board with the guide project under the SAME
            # basename, then let the backend strip + patch + route.
            paired = os.path.join(board_run, 'board.kicad_pcb')
            shutil.copy(src_pcb, paired)
            shutil.copy(pro_src, os.path.join(board_run, 'board.kicad_pro'))
            # A stale cert from an earlier policy must never survive: a
            # killed run leaves a new board beside an old verdict.
            cert_path = os.path.join(board_run, 'cert.json')
            for stale in (cert_path, out_pcb):
                try:
                    if os.path.exists(stale):
                        os.remove(stale)
                except OSError:
                    pass
            info = route_and_repair(paired, out_pcb, pro_src, args.timeout,
                                    ledger_dir=board_run,
                                    board_family=b['set'], policy=policy)
            cert = info.get('cert') or {}
            if cert:
                tmp_cert = cert_path + '.tmp'
                with open(tmp_cert, 'w', encoding='utf-8') as f:
                    json.dump(cert, f, indent=1)
                os.replace(tmp_cert, cert_path)
            rows.append({**b, 'routed': info.get('routed', False),
                         'routeSeconds': info.get('seconds'),
                         'error': info.get('error'),
                         'claims_before': (cert.get('claims_before') if cert else None),
                         'claims_after': (cert.get('claims_after') if cert else None),
                         'txn': (cert.get('txn') if cert else None),
                         'residue': (cert.get('residue') if cert else None),
                         'rule_congruent': cert.get('rule_congruent') if cert else None,
                         'rule_audit_pre': (cert.get('rule_audit_pre') if cert else None),
                         'rule_patch': (cert.get('rule_patch') if cert else None)})
            print(f"[{i}/{len(sel)}] {d}: routed={info.get('routed')} "
                  f"{info.get('seconds')}s total={time.time()-t0:.0f}s", flush=True)
        except Exception as e:
            rows.append({**b, 'routed': False, 'error': str(e)})
            print(f'[{i}/{len(sel)}] {d}: EXC {e}', flush=True)

        # Atomic write: a crash or power loss must not leave a truncated
        # (or NUL-filled) result file.  Write a sibling, then replace.
        tmp_rows = rows_path + '.tmp'
        with open(tmp_rows, 'w', encoding='utf-8') as f:
            json.dump(rows, f, indent=1)
        os.replace(tmp_rows, rows_path)

    print('done', flush=True)


if __name__ == '__main__':
    main()
