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
                     enable_planning=True, policy=None, arm=None,
                     code_fp=None):
    """Route the paired board (the backend strips it internally) and repair,
    exactly as ``arm`` prescribes.

    An arm fixes the plan mode (component 4), the loop mode (component 3),
    the router settings and which repair operators may be selected.  Every
    stage is transactional, and every round -- committed, rolled back or
    denied -- is recorded with its operator ledger in ``cert['repair']``,
    so a meta loop can attribute an outcome to the operator behind it.
    """
    from fluxpcb.router.freerouting import FreeroutingBackend
    from fluxpcb.repair_drc import (RIP_TYPES, MorphPicker,
                                    reroute_residues_isolated,
                                    repair_with_operators, run_drc,
                                    save_preserving_project)
    from fluxpcb.contract import Executor, RuleModel, ScopeBuilder
    from fluxpcb.design_agent.planner import build_plan
    from fluxpcb.design_agent.preroute import pre_route, survivors
    from fluxpcb.harness import HarnessHook
    from fluxpcb.meta.arm import Arm, default_arm
    from fluxpcb.meta.codefp import (cell_fingerprint, code_fingerprint,
                                     loaded_project_files)
    from fluxpcb.verifiers.binding import (bind_report, check_binding,
                                           project_identity, sha256_file)
    from fluxpcb.verifiers.live import record_board
    from fluxpcb.verifiers.residue import classify_drc
    from fluxpcb.verifiers.txn import claims_from_drc, transactional_repair
    import pcbnew

    t_wall = time.time()
    if arm is None:
        arm = (Arm.from_policy(policy, timeout_s=timeout_s)
               if policy is not None else
               default_arm('default', timeout_s=timeout_s,
                           enable_planning=enable_planning))
    bad = arm.validate()
    if bad:
        raise ValueError('invalid arm: ' + '; '.join(bad))
    if hook is None:
        hook = HarnessHook(arm.harness_policy())
    timeout_s = int(arm.router_timeout_s)
    backend = FreeroutingBackend(threads=arm.router_threads,
                                 max_passes=arm.router_passes)
    # The isolated reroute subprocess builds its own backend; the arm's
    # router settings reach it through the environment.
    os.environ['FLUXPCB_ROUTER_THREADS'] = str(arm.router_threads)
    os.environ['FLUXPCB_ROUTER_PASSES'] = str(arm.router_passes)
    # Which code every cell runs: the entry, its module-level imports and
    # the helper scripts.  Taken here, in this interpreter, so that the
    # per-cell fingerprint below is computed against the right files even
    # when the caller passed a fingerprint of its own.
    code_fp = code_fingerprint(
        [os.path.join(HERE, 'run_bench.py'), backend.kicad_helper])
    child_loaded = []       # project code loaded by isolated reroute interpreters

    def done(cert):
        """Finish a certificate: its wall time, and WHICH CODE this cell ran.

        `static_fp` is the code every cell runs.  `loaded` is what this
        cell's interpreters actually imported on top of it — most of the
        package is imported inside functions, so only a measurement sees
        it — and `code_fp` hashes the two together.  A cell is compared
        on its own files: editing a module that only some cells load
        stales exactly those cells.
        """
        cert['wall_seconds'] = round(time.time() - t_wall, 1)
        cert['static_fp'] = code_fp
        try:
            loaded = sorted(set(loaded_project_files()) | set(child_loaded))
            cert['loaded'] = loaded
            cert['code_fp'] = cell_fingerprint(loaded)
        except Exception as e:               # evidence, not a result
            cert['code_fp_error'] = f'{type(e).__name__}: {e}'
        return cert

    # What is known about the board BEFORE routing: the only input a
    # per-board selector may use.
    try:
        from fluxpcb.design_agent.features import features_of
        features = features_of(paired_pcb)
    except Exception as e:
        features = {'error': f'{type(e).__name__}: {e}'}
    base = {'board': os.path.basename(out_pcb), 'arm': arm.as_dict(),
            'arm_digest': arm.digest(), 'policy_digest': arm.digest(),
            'code_fp': code_fp, 'stages': arm.stages(),
            'features': features}

    # ---- component 4: the plan ---------------------------------------
    plan, plan_error = None, None
    if arm.plan != 'direct':
        try:
            plan = build_plan(
                paired_pcb,
                pre_pair_mm=(arm.params.preroute_pair_mm
                             if arm.plan == 'preroute' else None))
        except Exception as e:      # planning is evidence, not a crash
            plan_error = f'{type(e).__name__}: {e}'
    plan_cert = {
        'plan_digest': plan.digest() if plan is not None else None,
        'plan_claims': plan.claims if plan is not None else None,
        'capacity': plan.capacity.as_dict() if plan is not None else None,
        'plan_error': plan_error,
    }
    if plan is not None and plan.capacity.verdict == 'infeasible':
        # The capacity claim is a hard gate: a proven-infeasible stackup is
        # not sent to the router (H7: 0 traces/channel; every search
        # converges to the same residue).
        cert = done(dict(base, **plan_cert,
                         route={'routed': False, 'seconds': 0.0, 'passes': 0,
                                'error': 'capacity_infeasible'},
                         plan_consumed={'capacity_gate': True,
                                        'pre_route': False},
                         repair=[], board_worst='fail',
                         readiness='infeasible'))
        return {'routed': False, 'error': 'capacity_infeasible',
                'seconds': 0.0, 'cert': cert}

    # ---- pre-route (plan mode 'preroute') -----------------------------
    # Order matters: strip EVERYTHING first, then pre-route on the
    # placement-only board.  The benchmark input is the routed guide board;
    # keeping a net's copper on that board would keep the reference answer.
    route_input, pre, pre_full, keep_nets = paired_pcb, None, None, None
    if (plan is not None and arm.plan == 'preroute'
            and plan.pre_routed_nets):
        try:
            strip_dir = os.path.join(
                os.path.dirname(os.path.abspath(paired_pcb)), 'preroute')
            backend._kicad('strip', paired_pcb, strip_dir, timeout=120)
            pre_src = os.path.join(strip_dir, 'pre.kicad_pcb')
            shutil.copy(os.path.join(strip_dir, 'unrouted.kicad_pcb'), pre_src)
            shutil.copy(pro_src, os.path.join(strip_dir, 'pre.kicad_pro'))
            pre_full = pre_route(
                pre_src, plan,
                max_nets=arm.params.preroute_max_nets,
                max_edges=arm.params.preroute_max_edges,
                strict_pad_clearance=arm.params.pad_clearance_strict)
            pre = {k: v for k, v in pre_full.items()
                   if k not in ('drawn', 'drawn_vias', 'detail')}
            pre['drawn_count'] = (len(pre_full.get('drawn') or [])
                                  + len(pre_full.get('drawn_vias') or []))
            if pre.get('edges_routed'):
                # Certificate before emission: copper that is not clean on
                # its own board must not be handed to the router as fixed.
                pre_drc = run_drc(pre_src, pre_src + '.drc.json')
                pre['drc'] = 'failed' if pre_drc.get('ok') is False else None
                try:
                    with open(pre_src + '.drc.json', encoding='utf-8') as f:
                        pre_err = [v for v in json.load(f).get('violations') or []
                                   if v.get('severity') == 'error'
                                   and not v.get('excluded')]
                except (OSError, ValueError):
                    pre_err = [{'type': 'unreadable'}]
                pre['drc_errors'] = len(pre_err)
                pre['drc_types'] = sorted({v.get('type') for v in pre_err})
                if not pre_err:
                    route_input = pre_src
                    keep_nets = sorted(set(
                        plan.pre_routed_nets[:arm.params.preroute_max_nets]))
                else:
                    # Fall back to routing without the pre-route; recorded,
                    # never silent.
                    pre['rejected'] = True
        except Exception as e:
            pre, pre_full = {'error': f'{type(e).__name__}: {e}'}, None

    fr = backend.route(route_input, out_pcb, timeout_s=timeout_s,
                       use_board_rules=True, keep_nets=keep_nets)
    cert = done(dict(
        base, **plan_cert,
        route={'routed': fr.routed, 'seconds': fr.seconds,
               'passes': fr.passes, 'error': fr.error,
               'timed_out': fr.timed_out, 'threads': arm.router_threads,
               'max_passes': arm.router_passes, 'timeout_s': timeout_s},
        rule_audit_pre=fr.rule_audit, rule_patch=fr.patch,
        rule_congruent=fr.rule_congruence,
        plan_consumed={
            'capacity_gate': plan is not None,
            'pre_route': bool(keep_nets),
            'kept_locked': fr.kept_locked if keep_nets else 0,
            'escape_regions': (len(plan.escape_regions)
                               if plan is not None else 0),
            'pre_routed_nets': (len(plan.pre_routed_nets)
                                if plan is not None else 0),
        },
        pre_route=pre))
    if not fr.routed:
        cert.update(repair=[], board_worst='fail', readiness='router_failed')
        return {'routed': False, 'error': fr.error, 'seconds': fr.seconds,
                'cert': cert}
    if keep_nets and pre_full:
        # Certificate that the pre-routed copper reached the routed board.
        cert['pre_route_survivors'] = survivors(out_pcb, pre_full)

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

    picker = MorphPicker()
    allowed = arm.allowed()

    def action(path):
        if not holder['drc_json']:
            return {}
        classes = None
        if arm.loop == 'morph':
            # One violation morphology per transaction round.
            pick = picker.pick(holder['drc_json'])
            if pick is None:
                return {'no_op': True, 'operators_run': []}
            classes = [pick]
        return repair_with_operators(path, holder['drc_json'],
                                     allowed_ops=allowed, classes=classes,
                                     params=arm.params)

    def stage(name, rep, **extra):
        return dict({'stage': name, 'committed': rep.committed,
                     'rolled_back': rep.rolled_back, 'denied': rep.denied,
                     'log': rep.log}, **extra)

    before = evaluate(out_pcb)
    after = before
    repair_log, main_rep = [], None
    if arm.loop != 'route_only':
        if arm.loop == 'ripup':
            # Rip the nets that carry hard residues and let the router redo
            # only them; everything else stays as locked copper.
            def rip_action(path):
                if not holder['drc_json']:
                    return {'no_op': True, 'operators_run': []}
                out = reroute_residues_isolated(
                    path, holder['drc_json'], timeout_s=timeout_s,
                    types=RIP_TYPES, rip=True, max_nets=arm.rip_nets_max)
                child_loaded.extend(out.get('loaded') or [])
                if not out.get('operators_run') and not out.get('error'):
                    return dict(out, no_op=True)
                return out
            rip_rep = transactional_repair(
                out_pcb, rip_action, evaluate, max_rounds=1, initial=before,
                hook=hook, kind='ripup')
            after = evaluate(out_pcb)
            repair_log.append(stage('ripup', rip_rep, mode='partial-rip',
                                    max_nets=arm.rip_nets_max))
        rep = transactional_repair(
            out_pcb, action, evaluate, max_rounds=arm.rounds(),
            initial=after, hook=hook, kind='repair',
            stop_on_rollback=(arm.loop != 'morph'))
        after = evaluate(out_pcb)
        main_rep = rep
        repair_log.append(stage('repair', rep, mode=arm.loop,
                                rounds=arm.rounds()))
        if arm.loop == 'batch':
            # Width/clearance residues left after repair: one more
            # transactional re-route, judged by the same gate.
            if (after.error_types & {'track_width', 'clearance'}
                    and holder['drc_json']
                    and not (after.error_types - {'track_width', 'clearance'})):
                def reroute_action(path):
                    out = reroute_residues_isolated(
                        path, holder['drc_json'], timeout_s=timeout_s)
                    child_loaded.extend(out.get('loaded') or [])
                    if not out.get('operators_run') and not out.get('error'):
                        return dict(out, no_op=True)
                    return out
                reroute_rep = transactional_repair(
                    out_pcb, reroute_action, evaluate, max_rounds=1,
                    initial=after, hook=hook, kind='reroute')
                after = evaluate(out_pcb)
                repair_log.append(stage('reroute', reroute_rep, mode='full'))

    with open(holder['drc_json'], encoding='utf-8') as f:
        final_drc = json.load(f)

    cert['claims_after'] = after.as_dict()
    cert['txn'] = main_rep.as_dict() if main_rep is not None else {
        'committed': 0, 'rolled_back': 0, 'denied': 0, 'log': [], 'final': {}}
    cert['repair'] = repair_log
    # Every failed attempt, with its quantitative "why not", aggregated
    # from the stage logs: the input the error-model induction reads.
    cert['failures'] = _collect_failures(cert)
    cert['residue'] = classify_drc(final_drc)
    shutil.copy(pro_src, out_pro)

    # ---- scope + live ledger (P1) ------------------------------------
    live = {}
    board_worst = ('pass' if after.total == 0 and after.rule_congruent
                   else 'fail')
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
            extra={'rule_patch_verified': bool(patch.get('verified')),
                   'arm_digest': arm.digest(), 'code_fp': code_fp})
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
            board_worst = live.get('board_worst', board_worst)
    except Exception as e:
        cert['scope_error'] = str(e)
    cert['harness'] = hook.as_dict()
    cert['board_worst'] = board_worst
    cert['readiness'] = (live.get('worst') if live else
                         ('pass' if board_worst == 'pass' else 'fail'))
    cert = done(cert)
    return {'routed': True, 'seconds': fr.seconds,
            'wall_seconds': cert['wall_seconds'], 'passes': fr.passes,
            'cert': cert}


def _collect_failures(cert):
    out = []
    seen = set()
    for stage in cert.get('repair') or []:
        for e in stage.get('log') or []:
            if not isinstance(e, dict):
                continue
            act = e.get('action') or {}
            for entry in act.get('ledger') or []:
                if not isinstance(entry, dict) or not entry.get('outcome'):
                    continue
                key = entry.get('cert_id') or json.dumps(
                    entry, sort_keys=True, default=str)
                if key in seen:
                    continue
                seen.add(key)
                out.append(dict(entry, stage=stage.get('stage'),
                                decision=e.get('decision')))
    return out


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
    ap.add_argument('--arm', default='',
                    help='Arm JSON: plan mode, loop mode, router settings, '
                         'operator set and typed operator parameters')
    args = ap.parse_args()
    policy = None
    arm = None
    if args.arm:
        from fluxpcb.meta.arm import Arm
        arm = Arm.from_json(args.arm)
    if args.policy:
        from fluxpcb.meta.policy import PolicyArtifact
        policy = PolicyArtifact.from_json(args.policy)
    from fluxpcb.router.freerouting import FreeroutingBackend
    from fluxpcb.meta.codefp import code_fingerprint
    code_fp = code_fingerprint([
        os.path.join(HERE, 'run_bench.py'),
        FreeroutingBackend().kicad_helper])
    print(f'code fingerprint: {code_fp}', flush=True)
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
                                    board_family=b['set'], policy=policy,
                                    arm=arm, code_fp=code_fp)
            cert = info.get('cert') or {}
            if cert:
                tmp_cert = cert_path + '.tmp'
                with open(tmp_cert, 'w', encoding='utf-8') as f:
                    json.dump(cert, f, indent=1)
                os.replace(tmp_cert, cert_path)
            rows.append({**b, 'routed': info.get('routed', False),
                         'routeSeconds': info.get('seconds'),
                         'wallSeconds': info.get('wall_seconds'),
                         'error': info.get('error'),
                         'arm_digest': cert.get('arm_digest') if cert else None,
                         'code_fp': cert.get('code_fp') if cert else None,
                         'board_worst': cert.get('board_worst') if cert else None,
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
