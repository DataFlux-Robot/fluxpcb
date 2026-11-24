#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Parallel arm evaluation: every (board, arm) cell is one subprocess.

An arm is a complete scheme for components 3 and 4.  Running many arms on
the SAME boards is the intervention: where outcomes differ, the axis the
arms differ on is what made the difference; where they do not, the board
says nothing about that axis.

Rules this runner enforces on itself:
  * the parent never imports pcbnew (a long-lived pcbnew process dies and
    takes the batch with it);
  * a cell is fresh only when its cert carries the arm digest AND the code
    fingerprint, so neither a policy change nor a code change can be served
    a stale result;
  * the outcome is scored by a waiver-free DRC (score_runs.score_one), not
    by the DRC inside the run, which honours repair-generated .kicad_dru;
  * every cell is checked for instrument problems (selection leaks, missing
    repair log); a cell with problems is evidence of a bug, not a result.
"""
import argparse
import dataclasses
import json
import os
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))          # autorouter-bench
sys.path.insert(0, os.path.join(ROOT, '..', 'flux-neurotransmitter'))
sys.path.insert(0, HERE)

RUN_BENCH = os.path.join(HERE, 'run_bench.py')


def _fluxpcb():
    from fluxpcb.meta import arm as _arm, codefp as _codefp
    return _arm, _codefp


def _static_args():
    from fluxpcb.router.freerouting import FreeroutingBackend
    return [RUN_BENCH, FreeroutingBackend().kicad_helper]


def code_fp():
    """Fingerprint of the code EVERY cell runs (the entry script, its
    module-level imports, the helper scripts).  A cell's own fingerprint
    adds what that cell measured loading; see `cell_fp_now`."""
    arm_mod, codefp = _fluxpcb()
    return codefp.code_fingerprint(_static_args())


def _cf():
    """The fingerprint module, with the bench entry's files current."""
    arm_mod, codefp = _fluxpcb()
    codefp.code_fingerprint(_static_args())
    return codefp


def cell_fp_now(cell):
    """The fingerprint `cell` must carry to count as fresh.

    The static half plus the files the cell recorded having loaded,
    hashed as they are on disk now.  None for a cell recorded before the
    measured half existed: it cannot say which code it ran.
    """
    if (cell or {}).get('loaded') is None:
        return None
    return _cf().cell_fingerprint(cell['loaded'])


# ---------------------------------------------------------------- cells
def _inside(board_dir):
    """Path of a board's RUN directory relative to the runs root.

    Sources may live outside boards/ (the sealed controls do, addressed as
    '../controls/<name>').  Their outputs must stay inside the run, never
    beside the sealed source, so the link prefix is folded away here.
    """
    return board_dir[3:] if board_dir.startswith('../') else board_dir


def cell_id(board_dir, arm):
    # a file name: no separators, even if a source were addressed relatively
    safe = _inside(board_dir).replace('/', '__').replace(os.sep, '__')
    return f'{safe}__{arm.digest()[:12]}'


def cell_paths(run, board_dir, arm):
    runs_root = os.path.join(run, 'runs', arm.digest()[:12])
    board_run = os.path.join(runs_root, _inside(board_dir))
    return {'board_run': board_run,
            'cert': os.path.join(board_run, 'cert.json'),
            'pcb': os.path.join(board_run, f'{board_dir}.kicad_pcb'),
            'rows': os.path.join(runs_root, f'{board_dir}.rows.json'),
            'json': os.path.join(run, 'cells', cell_id(board_dir, arm) + '.json')}


def fresh(path, arm, fp):
    """A cell is fresh when its arm is this arm and the code it ran — the
    files it recorded — is still the code on disk.  `fp` is not compared
    directly: each cell carries its own fingerprint (static half plus
    what it loaded), so a module only some cells run can change without
    staling the others.

    A cell that left no cert (budget, crash) recorded nothing about the
    code it ran; the static half is all it is known to have in common
    with the batch, so that is what it is compared on.
    """
    if not os.path.exists(path):
        return False
    try:
        c = json.load(open(path, encoding='utf-8'))
    except ValueError:
        return False
    if c.get('arm_digest') != arm.digest():
        return False
    if c.get('status') == 'crash':
        # no measurement happened; a crash is re-run, and the retry is
        # counted on the cell (prior_crashes) rather than forgotten
        return False
    if c.get('loaded') is None:
        return c.get('static_fp') == fp
    return c.get('code_fp') == cell_fp_now(c)


def atomic_json(path, body):
    """Write-then-replace, with a temporary name unique to the writer,
    and a replace that waits for concurrent readers.

    Two workers can write the same file (the per-board manifest when
    several arms share a board): with one fixed temporary name the
    second writer's replace found the file already moved, raised, and
    the cell it was recording vanished from the run.  With unique names
    there is a second race on Windows: the replace is refused while
    another worker has the destination open to read it.  That reader is
    done in milliseconds, so the writer waits instead of failing.
    """
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f'{path}.{os.getpid()}.{threading.get_ident()}.tmp'
    with open(tmp, 'w', encoding='utf-8') as fh:
        json.dump(body, fh, indent=1, ensure_ascii=False)
    for attempt in range(50):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if attempt == 49:
                try:
                    os.remove(tmp)
                except OSError:
                    pass
                raise
            time.sleep(0.02 * (attempt + 1))


def instrument(cert, arm, fp, status='ok'):
    """(problems, degraded stages).  A problem invalidates the cell.

    A cell that hit its wall-clock budget has no cert, and that is a
    measured outcome ("did not finish"), not a broken instrument.

    The code checks are three, and each answers a different question:
      * does the cell's fingerprint match the files it says it ran?
        (otherwise the code changed under it, or the record is wrong)
      * did the static half change while the batch was running?  (the
        parent took `fp` when it started; a cell that ran other static
        code is not comparable with the rest of the batch)
      * did the cell load project code the static analysis cannot reach?
        (the cell's fingerprint still covers it; the gap matters for
        code a SUBPROCESS runs by path, which is only ever predicted)
    """
    problems, degraded = [], []
    if cert is None:
        return ([] if status == 'budget' else ['no cert']), degraded
    if cert.get('arm_digest') != arm.digest():
        problems.append('arm digest mismatch')
    loaded = cert.get('loaded')
    if loaded is None:
        problems.append('no measured code set' if not cert.get('code_fp_error')
                        else 'code set error: ' + str(cert['code_fp_error'])[:120])
    else:
        if cert.get('code_fp') != _cf().cell_fingerprint(loaded):
            problems.append('code fingerprint mismatch')
        if cert.get('static_fp', fp) != fp:
            problems.append('static code changed during the run')
        for g in _cf().gaps(loaded):
            problems.append('closure gap: ' + g)
    if cert.get('route', {}).get('routed') and 'repair' not in cert:
        problems.append('no repair log')
    for d in (cert.get('harness') or {}).get('denials') or []:
        if 'not allowed' in d:
            problems.append('selection leak: ' + d)
    if cert.get('scope_error'):
        problems.append('scope_error: ' + str(cert['scope_error'])[:120])
    for st in cert.get('repair') or []:
        if st.get('errors'):
            degraded.append(f"{st['stage']}:action_error")
    return problems, degraded


def stage_summary(cert):
    out = []
    for st in cert.get('repair') or []:
        ops = {}
        for e in st.get('log') or []:
            for op in (e.get('action') or {}).get('operators_run') or []:
                ops[op] = ops.get(op, 0) + 1
        out.append({'stage': st.get('stage'), 'mode': st.get('mode'),
                    'committed': st.get('committed', 0),
                    'rolled_back': st.get('rolled_back', 0),
                    'denied': st.get('denied', 0),
                    'errors': st.get('errors', 0),
                    'decisions': [e.get('decision') for e in st.get('log') or []],
                    'operator_rounds': ops})
    return out


def classify(status, score):
    if status != 'ok':
        return status
    if score is None:
        return 'unscored'
    if score.get('errors') == 0 and score.get('unconnected') == 0:
        return 'clean'
    return 'fail'


def classify_strict(cell):
    """The error criterion, plus what it does not see: a track or via left
    unconnected at one end is a warning to the DRC.  None when the cell
    has no strict score (an older run, or no board)."""
    score = cell.get('score') or {}
    if cell.get('outcome') != 'clean':
        return cell.get('outcome')
    if score.get('dangling') is None:
        return None
    return 'clean' if score.get('dangling') == 0 else 'fail'


def run_cell(board, arm, arm_path, run, fp, budget, router_timeout, score_fn):
    cid = cell_id(board['dir'], arm)
    paths = cell_paths(run, board['dir'], arm)
    os.makedirs(paths['board_run'], exist_ok=True)
    manifest = os.path.join(run, 'one', board['dir'] + '.json')
    if not os.path.exists(manifest):
        atomic_json(manifest, [board])
    runs_root = os.path.join(run, 'runs', arm.digest()[:12])
    # run_bench joins <runs-dir>/<board dir>; for a '../controls/x' source
    # that lands inside the run only if the runs root ends in 'boards'.
    runs_arg = (os.path.join(runs_root, 'src', 'boards')
                if board['dir'].startswith('../') else runs_root)
    cmd = [sys.executable, RUN_BENCH, '--manifest', manifest,
           '--splits', board['set'], '--runs-dir', runs_arg,
           '--rows', paths['rows'], '--arm', arm_path,
           '--timeout', str(router_timeout)]
    t0 = time.time()
    status, err = 'ok', ''
    # The worker hashes the static half the parent hashed: hand it the
    # list, so a module-level file edited in between cannot make the two
    # disagree about what "every cell runs" means.
    env = dict(os.environ)
    env[_cf().FILES_ENV] = os.pathsep.join(_cf().closure_files())
    # what a cell may spend: a retry inside the cell has to know it
    env['FLUXPCB_CELL_BUDGET'] = str(budget)
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                            text=True, encoding='utf-8', errors='replace', env=env)
    try:
        _, err = proc.communicate(timeout=budget + 120)
    except subprocess.TimeoutExpired:
        # Kill the whole tree: java and kicad-cli outlive their parent.
        subprocess.run(['taskkill', '/F', '/T', '/PID', str(proc.pid)],
                       capture_output=True)
        try:
            proc.communicate(timeout=60)
        except subprocess.TimeoutExpired:
            pass
        status = 'budget'
    wall = round(time.time() - t0, 1)
    cert = None
    if os.path.exists(paths['cert']):
        try:
            cert = json.load(open(paths['cert'], encoding='utf-8'))
        except ValueError:
            cert = None
    if status == 'ok':
        if cert is None:
            status = 'crash'
        elif (cert.get('route') or {}).get('error') == 'capacity_infeasible':
            status = 'capacity_infeasible'
        elif not (cert.get('route') or {}).get('routed'):
            status = 'router_failed'
    score = None
    if status == 'ok' and os.path.exists(paths['pcb']):
        pro = os.path.join(HERE, 'boards', board['dir'], 'circuit.kicad_pro')
        try:
            score = score_fn(paths['pcb'], pro)
        except Exception as e:
            score = {'error': f'{type(e).__name__}: {e}'}
    problems, degraded = instrument(cert, arm, fp, status)
    cell = {
        'board': board['dir'], 'set': board['set'],
        'arm': arm.name, 'arm_digest': arm.digest(),
        'arm_axes': {'plan': arm.plan, 'loop': arm.loop},
        # every typed variable this arm changed against the default
        'param_overrides': [{'name': n, 'default': d, 'value': v}
                            for n, d, v in
                            arm.params.deltas_from(type(arm.params)())],
        # THIS cell's fingerprint: the static half plus what it loaded.
        # Freshness is judged per cell (see fresh); `static_fp` is the
        # batch's, taken by the parent when it started.
        'code_fp': (cert or {}).get('code_fp'), 'static_fp': fp,
        'loaded': (cert or {}).get('loaded'),
        'budget_s': budget, 'status': status,
        'outcome': classify(status, score),
        'score': score,
        'claims_after': (cert or {}).get('claims_after'),
        'claims_before': (cert or {}).get('claims_before'),
        'wall_seconds': (cert or {}).get('wall_seconds') or wall,
        'route_seconds': ((cert or {}).get('route') or {}).get('seconds'),
        'features': (cert or {}).get('features'),
        'stages': stage_summary(cert or {}),
        'pre_route_survivors': (cert or {}).get('pre_route_survivors'),
        'harness_denials': ((cert or {}).get('harness') or {}).get('denials') or [],
        'instrument_ok': not problems, 'instrument_problems': problems,
        'degraded_stages': degraded,
        # how many earlier attempts of this cell ended in a crash; the
        # outcome above is the latest attempt's
        'prior_crashes': _prior_crashes(paths['json']),
        'error': ((cert or {}).get('route') or {}).get('error')
                 or (err or '')[-400:] if status != 'ok' else None,
        'cert': os.path.relpath(paths['cert'], run),
    }
    cell['outcome_strict'] = classify_strict(cell)
    atomic_json(paths['json'], cell)
    return cell


def _prior_crashes(cell_path):
    try:
        old = json.load(open(cell_path, encoding='utf-8'))
    except (OSError, ValueError):
        return 0
    if old.get('status') != 'crash':
        return 0
    return int(old.get('prior_crashes') or 0) + 1


# ---------------------------------------------------------------- arms
def gen0_arms():
    """12 factorial arms + an A/A duplicate + one single-variable ablation."""
    from fluxpcb.meta.arm import factorial_arms
    arms = factorial_arms(threads=2, timeout_s=240)
    gate_batch = [a for a in arms if a.name == 'gate+batch'][0]
    aa = dataclasses.replace(gate_batch, name='gate+batch~aa')
    lenient = dataclasses.replace(
        gate_batch, name='gate+batch~lenientpad',
        params=gate_batch.params.with_delta('pad_clearance_strict', False))
    return arms + [aa, lenient]


def write_arms(run, arms):
    out = []
    for a in arms:
        p = os.path.join(run, 'arms', a.name.replace('+', '_').replace('~', '-') + '.json')
        os.makedirs(os.path.dirname(p), exist_ok=True)
        a.to_json(p)
        out.append(p)
    return out


def build_coreset(run, n_dep=7, n_guard=8):
    """Failure-stratified coreset from the latest full run.

    Sorting boards by name lands on clean boards and yields a coreset with
    nothing to propose (the smoke-test trap).  The coreset is instead the
    current failures, boards that only pass because repair committed, and
    a few boards that pass without repair as regression guards.
    """
    src = os.path.join(HERE, 'rounds', 'r3')
    rows = json.load(open(os.path.join(src, 'score.json'), encoding='utf-8'))
    full = {b['dir']: b for b in json.load(
        open(os.path.join(HERE, 'boards_full.json'), encoding='utf-8'))}
    failing, dependent, guards = [], [], []
    for r in sorted(rows, key=lambda r: (r['set'], r['dir'])):
        b = full[r['dir']]
        cp = os.path.join(src, 'runs', r['dir'], 'cert.json')
        committed = 0
        if os.path.exists(cp):
            try:
                c = json.load(open(cp, encoding='utf-8'))
                committed = (c.get('txn') or {}).get('committed', 0) or 0
            except ValueError:
                pass
        if not r.get('cleanPass'):
            failing.append((b, 'failing'))
        elif committed > 0:
            dependent.append((b, 'repair-dependent'))
        else:
            guards.append((b, 'guard'))
    # guards: mostly small boards, but keep the size bands represented
    by_set = {}
    for g in guards:
        by_set.setdefault(g[0]['set'], []).append(g)
    for v in by_set.values():
        v.sort(key=lambda g: g[0]['dir'])
    picked = []
    for s, n in (('d3a', 5), ('d3b', 2), ('d3c', 1)):
        picked += by_set.get(s, [])[:n]
    coreset = failing + dependent[:n_dep] + picked[:n_guard]
    body = [{**b, 'coreset_role': role} for b, role in coreset]
    atomic_json(os.path.join(run, 'coreset.json'), body)
    return body


def _code_problem(p):
    return p.startswith(('code fingerprint', 'no measured code set',
                         'code set error', 'static code changed',
                         'closure gap'))


def adopt_cert_fingerprint(run, arms, boards):
    """Trust a cell whose CERT still describes the code on disk.

    The cell file is bookkeeping written by the parent; the cert is
    written by the worker from what it executed and loaded.  When the
    cert's own fingerprint — the static half plus its measured set —
    still matches those files as they are now, the cell is genuinely
    fresh and only its bookkeeping is old: a long run that outlived an
    edit elsewhere, or a cell written before a bookkeeping field
    existed.  The cell's instrument problems are then recomputed from
    the cert; anything else is left alone and re-run.
    """
    fp = code_fp()
    adopted = stale = 0
    for b in boards:
        for a in arms:
            path = cell_paths(run, b['dir'], a)['json']
            if not os.path.exists(path):
                continue
            try:
                cell = json.load(open(path, encoding='utf-8'))
            except ValueError:
                continue
            cert_p = os.path.join(run, cell.get('cert') or '')
            try:
                cert = json.load(open(cert_p, encoding='utf-8'))
            except (OSError, ValueError):
                cert = {}
            if cell.get('status') == 'budget' and not cert:
                if cell.get('instrument_problems') == ['no cert']:
                    # bookkeeping from before the budget/no-cert distinction
                    cell['instrument_problems'] = []
                    cell['instrument_ok'] = True
                    atomic_json(path, cell)
                    adopted += 1
                continue
            want = cell_fp_now(cert)
            if (want is None or cert.get('code_fp') != want
                    or cert.get('arm_digest') != a.digest()):
                stale += 1
                continue
            problems, _ = instrument(cert, a, fp, cell.get('status', 'ok'))
            if any(_code_problem(p) for p in problems):
                stale += 1
                continue
            if (cell.get('code_fp') == want and cell.get('static_fp') == fp
                    and cell.get('loaded') == cert.get('loaded')
                    and cell.get('instrument_problems') == problems):
                continue                          # already in order
            cell['code_fp'] = want
            cell['static_fp'] = fp
            cell['loaded'] = cert.get('loaded')
            cell['instrument_problems'] = problems
            cell['instrument_ok'] = not problems
            atomic_json(path, cell)
            adopted += 1
    print(f'fingerprint {fp}: adopted {adopted} cells from their certs, '
          f'{stale} stale (will re-run)', flush=True)


# ---------------------------------------------------------------- driver
def evaluate(run, boards, arms, budget, router_timeout, workers, label):
    from score_runs import score_one
    fp = code_fp()
    arm_paths = write_arms(run, arms)
    # Bound each router process: cells run side by side and an uncapped
    # JVM heap on each would exhaust the machine's memory.
    os.environ.setdefault('FLUXPCB_JAVA_XMX', '2g')
    todo = [(b, a, p) for a, p in zip(arms, arm_paths) for b in boards
            if not fresh(cell_paths(run, b['dir'], a)['json'], a, fp)]
    total = len(boards) * len(arms)
    print(f'[{label}] {total} cells, {total - len(todo)} fresh, '
          f'{len(todo)} to run, {workers} workers, code {fp}', flush=True)
    results = []
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = {pool.submit(run_cell, b, a, p, run, fp, budget,
                            router_timeout, score_one): (b, a)
                for b, a, p in todo}
        for i, f in enumerate(as_completed(futs), 1):
            b, a = futs[f]
            try:
                c = f.result()
            except Exception as e:
                # The runner itself failed on this cell.  The record goes
                # to the cell file like any other: the cell list is
                # rebuilt from disk below, and a crash kept only in
                # memory used to vanish from the run — a smaller
                # denominator with nothing to show for it.
                c = _crash_cell(run, b, a, fp, budget,
                                f'runner exception: {type(e).__name__}: {e}')
            results.append(c)
            flag = '' if c.get('instrument_ok') else '  <-- INSTRUMENT'
            print(f"[{label} {i}/{len(todo)}] {c.get('outcome', '?'):18s} "
                  f"{c.get('wall_seconds', 0)!s:>6}s {a.name:22s} "
                  f"{b['dir'][:34]}{flag}", flush=True)
    # every cell, fresh or just run — and every cell that should exist.
    # A cell with no file after the run is an instrument fault on
    # record, never a denominator that quietly shrank.
    cells = []
    for b in boards:
        for a in arms:
            p = cell_paths(run, b['dir'], a)['json']
            if not os.path.exists(p):
                c = _crash_cell(run, b, a, fp, budget, 'cell vanished: no cell file after the run')
                print(f"[{label}] INSTRUMENT {a.name} {b['dir'][:34]}: {c['instrument_problems'][0]}",
                      flush=True)
            else:
                try:
                    c = json.load(open(p, encoding='utf-8'))
                except ValueError:
                    c = _crash_cell(run, b, a, fp, budget, 'cell file unreadable')
            cells.append(c)
    summary = summarise(cells, arms, boards)
    summary.update({'label': label, 'code_fp': fp, 'budget_s': budget,
                    'cells': len(cells), 'expected': total,
                    'seconds': round(time.time() - t0, 1)})
    if len(cells) != total:
        summary['instrument_bad_cells'] += total - len(cells)
    atomic_json(os.path.join(run, f'summary_{label}.json'), summary)
    print(json.dumps(summary['by_arm'], indent=1), flush=True)
    return cells, summary


def _crash_cell(run, board, arm, fp, budget, why):
    """The record of a cell that produced no measurement, written to its
    cell file so that it is counted, shown, and re-run."""
    cell = {'board': board['dir'], 'set': board['set'], 'arm': arm.name,
            'arm_digest': arm.digest(),
            'arm_axes': {'plan': arm.plan, 'loop': arm.loop},
            'code_fp': None, 'static_fp': fp, 'loaded': None,
            'budget_s': budget, 'status': 'crash', 'outcome': 'crash',
            'score': None, 'wall_seconds': 0,
            'instrument_ok': False, 'instrument_problems': [why[:300]],
            'degraded_stages': [], 'error': why[:400],
            'prior_crashes': _prior_crashes(cell_paths(run, board['dir'], arm)['json'])}
    atomic_json(cell_paths(run, board['dir'], arm)['json'], cell)
    return cell


def summarise(cells, arms, boards):
    by_arm = {}
    for a in arms:
        mine = [c for c in cells if c.get('arm') == a.name]
        by_arm[a.name] = {
            'clean': sum(1 for c in mine if c.get('outcome') == 'clean'),
            'fail': sum(1 for c in mine if c.get('outcome') == 'fail'),
            'budget': sum(1 for c in mine if c.get('outcome') == 'budget'),
            # the error criterion and the strict one (stubs counted)
            'clean_strict': sum(1 for c in mine
                                if c.get('outcome_strict') == 'clean'),
            'unscored_strict': sum(1 for c in mine
                                   if c.get('outcome') == 'clean'
                                   and c.get('outcome_strict') is None),
            'dangling_left': sum((c.get('score') or {}).get('dangling') or 0
                                 for c in mine),
            'other': sum(1 for c in mine if c.get('outcome') not in
                         ('clean', 'fail', 'budget')),
            'mean_wall_s': round(sum(c.get('wall_seconds') or 0 for c in mine)
                                 / max(1, len(mine)), 1),
            'instrument_bad': sum(1 for c in mine if not c.get('instrument_ok')),
            'degraded': sum(1 for c in mine if c.get('degraded_stages')),
        }
    return {'by_arm': by_arm, 'boards': len(boards),
            'instrument_bad_cells': sum(1 for c in cells
                                        if not c.get('instrument_ok'))}


def selfcheck(run):
    """One board, one arm: prove the instruments before spending a budget."""
    from fluxpcb.meta.arm import Arm
    boards = json.load(open(os.path.join(HERE, 'boards_full.json'),
                            encoding='utf-8'))
    small = [b for b in boards if b['set'] == 'd3a'
             and b['dir'].startswith('0023_')][0]
    arm = Arm(name='selfcheck', plan='gate', loop='batch',
              router_threads=2, router_timeout_s=120)
    run_dir = os.path.join(run, 'selfcheck')
    os.makedirs(run_dir, exist_ok=True)
    cells, summary = evaluate(run_dir, [small], [arm], 300, 120, 1, 'selfcheck')
    c = cells[0]
    ok = bool(c.get('instrument_ok')) and 'repair' not in str(
        c.get('instrument_problems'))
    print(json.dumps({k: c.get(k) for k in (
        'outcome', 'status', 'instrument_ok', 'instrument_problems',
        'degraded_stages', 'harness_denials', 'wall_seconds', 'score')},
        indent=1, ensure_ascii=False))
    print('SELFCHECK', 'PASS' if ok and summary['instrument_bad_cells'] == 0
          and c.get('outcome') in ('clean', 'fail') else 'FAIL')
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--run', required=True)
    ap.add_argument('--gen0', action='store_true',
                    help='12 factorial arms + A/A + one ablation on the coreset')
    ap.add_argument('--arms', nargs='*', default=[], help='arm JSON files')
    ap.add_argument('--boards', default='', help='board manifest JSON')
    ap.add_argument('--label', default='gen0')
    ap.add_argument('--build-coreset', action='store_true')
    ap.add_argument('--coreset', default='')
    ap.add_argument('--workers', type=int, default=6)
    ap.add_argument('--budget', type=int, default=300,
                    help='wall-clock cap per cell, seconds')
    ap.add_argument('--timeout', type=int, default=240,
                    help='router timeout passed to run_bench')
    ap.add_argument('--selfcheck', action='store_true')
    ap.add_argument('--adopt-cert-fp', action='store_true',
                    help='accept cells whose cert carries the current code '
                         'fingerprint before deciding what to re-run')
    args = ap.parse_args()
    os.makedirs(args.run, exist_ok=True)

    if args.selfcheck:
        sys.exit(0 if selfcheck(args.run) else 1)

    from fluxpcb.meta.arm import Arm
    arms = ([Arm.from_json(p) for p in args.arms] if args.arms
            else gen0_arms())
    bad = [(a.name, a.validate()) for a in arms if a.validate()]
    if bad:
        sys.exit(f'invalid arms: {bad}')

    if args.build_coreset or args.coreset:
        if args.build_coreset:
            boards = build_coreset(args.run)
        else:
            boards = json.load(open(args.coreset, encoding='utf-8'))
    else:
        boards = json.load(open(args.boards, encoding='utf-8'))
    roles = {}
    for b in boards:
        r = b.get('coreset_role', 'unlabelled')
        roles[r] = roles.get(r, 0) + 1
    print(f'boards: {len(boards)}; roles: {roles}', flush=True)
    if args.adopt_cert_fp:
        adopt_cert_fingerprint(args.run, arms, boards)
    evaluate(args.run, boards, arms, args.budget, args.timeout,
             args.workers, args.label)


if __name__ == '__main__':
    main()
