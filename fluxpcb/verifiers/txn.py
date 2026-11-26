"""txn — certified transactional repair.

Every repair batch is a transaction: snapshot, act on the copy, evaluate the
post-state against the *full* set of hard claims, and commit only if the
lexicographic order

    (new hard violation types, errors + unconnected)

strictly improves while no new type appears.  A rejected batch is rolled
back byte-for-byte.  This is what makes operator composition safe: the
acceptance decision is defined on the post-state's claim vector, not on the
operator's local target.

Termination uses the error **identity set**, not the count: a plateau in the
count can hide a changed failing set (the GenProg over-fitting lesson).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from collections import Counter
from dataclasses import dataclass, field


def _norm_pos(pos) -> str:
    try:
        x = round(float(pos.get('x', 0.0)), 3)
        y = round(float(pos.get('y', 0.0)), 3)
        return f'{x:.3f},{y:.3f}'
    except Exception:
        return '?'


def canonical_violation(v: dict) -> str:
    """A stable identity for one violation.

    UUIDs survive refills; descriptions carry lengths and net names that
    change when copper changes, so they are the fallback only.
    """
    vtype = v.get('type', '?')
    parts = []
    for it in v.get('items', []) or []:
        uid = it.get('uuid') or ''
        if uid:
            parts.append(uid)
        else:
            parts.append((it.get('description') or '')[:80] + '@' + _norm_pos(it.get('pos', {})))
    if not parts:
        parts = [(v.get('description') or '')[:80] + '@' + _norm_pos(v.get('pos', {}))]
    return vtype + '|' + ';'.join(sorted(parts))


def unconnected_ids(us) -> frozenset:
    """Per net, as many identities as the net has missing connections.

    The DRC names the two items of a ratsnest edge, and which two it
    names depends on the spanning tree it picked: on 0496 two runs on the
    same bytes gave the same 12 opens with one of them between a
    different track and the same pad.  A net's count does not move.  The
    identity set is what tells a plateau from progress, so it must be
    the count.
    """
    by_net = Counter()
    for u in us or []:
        names = set()
        for i in u.get('items') or []:
            m = re.search(r'\[([^\]]+)\]', i.get('description') or '')
            if m:
                names.add(m.group(1))
        by_net['/'.join(sorted(names)) or '?'] += 1
    return frozenset(f'unconnected|{net}#{k}'
                     for net, n in by_net.items() for k in range(n))


# Manufacturing severity, used only to order two feasible states.  A short
# is worse than a clearance miss, which is worse than a thin track.  These
# weights never let a hard constraint be traded for a soft gain: every term
# is a violation.
SEVERITY_WEIGHT = {
    'shorting_items': 50,
    'clearance': 10,
    'hole_clearance': 10,
    'copper_edge_clearance': 10,
    'hole_to_hole': 10,
    'unconnected': 10,
    'track_width': 1,
}


def _type_weight(t: str) -> int:
    return SEVERITY_WEIGHT.get(t, 5)


@dataclass(frozen=True)
class Claims:
    """The hard claim vector of one board state."""
    n_errors: int = 0
    n_unconnected: int = 0
    error_types: frozenset = frozenset()
    error_ids: frozenset = frozenset()
    unconnected_ids: frozenset = frozenset()
    rule_congruent: bool = True
    type_counts: tuple = ()   # ((type, count), ...) sorted

    @property
    def total(self) -> int:
        return self.n_errors + self.n_unconnected

    @property
    def weighted(self) -> int:
        """Severity-weighted violation load.

        Used only to order two feasible-in-the-hard-sense states; it never
        lets a violation be traded for a non-violation.
        """
        body = sum(_type_weight(t) * n for t, n in self.type_counts)
        return body + 10 * self.n_unconnected

    @property
    def ids(self) -> frozenset:
        return self.error_ids | self.unconnected_ids

    def as_dict(self) -> dict:
        return {
            'n_errors': self.n_errors,
            'n_unconnected': self.n_unconnected,
            'total': self.total,
            'error_types': sorted(self.error_types),
            'type_counts': dict(self.type_counts),
            'weighted': self.weighted,
            'rule_congruent': self.rule_congruent,
            'error_id_digest': hashlib.sha256(
                '\n'.join(sorted(self.ids)).encode()).hexdigest()[:16],
        }


def claims_from_drc(drc: dict, rule_congruent: bool = True) -> Claims:
    vs = [v for v in (drc.get('violations') or [])
          if v.get('severity') == 'error' and not v.get('excluded')]
    us = [u for u in (drc.get('unconnected_items') or [])
          if not u.get('excluded')]
    return Claims(
        n_errors=len(vs),
        n_unconnected=len(us),
        error_types=frozenset(v.get('type', '?') for v in vs),
        error_ids=frozenset(canonical_violation(v) for v in vs),
        unconnected_ids=unconnected_ids(us),
        rule_congruent=rule_congruent,
        type_counts=tuple(sorted(Counter(v.get('type', '?') for v in vs).items())),
    )


def union_claims(*claims: Claims) -> Claims:
    """The findings of several evaluations of one state.

    kicad-cli's DRC does not report every violation every time.  When
    several violations crowd onto one item it reports a random subset of
    them: on a board where three segments of one net run too close to one
    track, 14 runs of the same bytes gave 2, 3 or 4 errors (measured on
    KiCad 10.0.6, 2026-11-25); on 0496, 26 to 35.  It never reports one
    that is not there.  The union of three runs was the same ten times
    out of ten on the small board; one run, never.
    """
    ids = frozenset().union(*[c.ids for c in claims])
    errors = [i for i in ids if not i.startswith('unconnected|')]
    opens = [i for i in ids if i.startswith('unconnected|')]
    types = Counter(i.split('|', 1)[0] for i in errors)
    return Claims(n_errors=len(errors), n_unconnected=len(opens),
                  error_types=frozenset(types), error_ids=frozenset(errors),
                  unconnected_ids=frozenset(opens),
                  rule_congruent=all(c.rule_congruent for c in claims),
                  type_counts=tuple(sorted(types.items())))


def steady(evaluate, runs: int = 3):
    """`evaluate` asked `runs` times, judged on the union.

    Two runs that happen to agree can agree on the same under-report
    (a test's width repair was still thrown away as "total increased"
    about one time in three), so agreement is not trusted: a state with
    something to report is always asked three times.  A state with
    nothing to report is not asked again: the DRC never leaves a board
    with a violation at zero (one report per crowd of violations always
    comes out).  Returns a function with `evaluate`'s contract; it costs
    two more DRCs on every evaluation that finds something.
    """
    def ev(path):
        first = evaluate(path)
        if first.total == 0 or runs <= 1:
            return first
        out = first
        for _ in range(runs - 1):
            out = union_claims(out, evaluate(path))
        return out
    return ev


def accept(before: Claims, after: Claims) -> tuple:
    """Lexicographic feasibility-first acceptance.

    Returns (ok, reason).  A batch may not:
      * lose rule-model congruence;
      * introduce a violation type the board did not already have;
    and must strictly reduce ``errors + unconnected`` (or, at equal totals,
    strictly shrink the identity set).

    The second rule is not relaxed for an operator that removes more
    than it brings in (0599: 36 too-thin tracks widened to 1, with 3
    clearance misses).  It was tried: the right fix is in the
    operators — one that brings in a class has not measured its own
    work with the same DRC the gate uses (see txn.steady).
    """
    if before.rule_congruent and not after.rule_congruent:
        return False, 'rule congruence lost'
    # A repair may never introduce a violation class the board did not
    # already have: that is how a local fix hides a new global failure.
    new_types = after.error_types - before.error_types
    if new_types:
        return False, f'new violation types: {sorted(new_types)}'
    # A higher total can never be accepted, even when the weighted load
    # falls (e.g. many thin tracks traded for one short is not progress).
    if after.total > before.total:
        return False, (f'total increased {before.total} -> {after.total} '
                       f'(weighted {before.weighted} -> {after.weighted})')
    # Every term of ``weighted`` is a real violation, so a decrease is a
    # decrease in hard damage.  Severity orders the trade: a short costs
    # more than a clearance miss, which costs more than a thin track.
    if after.weighted < before.weighted:
        return True, (f'weighted {before.weighted} -> {after.weighted} '
                      f'(total {before.total} -> {after.total})')
    if after.total == before.total and after.ids < before.ids:
        return True, f'set shrank at equal total {after.total}'
    return False, (f'no improvement (weighted {before.weighted} -> '
                   f'{after.weighted}; total {before.total} -> {after.total})')


# ──────────────────────────────────────────────────────────────────────
# board transactions
# ──────────────────────────────────────────────────────────────────────

_SIDECARS = ('.kicad_dru',)


def _snapshot_paths(board_path: str) -> list:
    paths = [board_path]
    base = os.path.splitext(board_path)[0]
    for suf in _SIDECARS:
        if os.path.exists(base + suf):
            paths.append(base + suf)
    pro = base + '.kicad_pro'
    if os.path.exists(pro):
        paths.append(pro)
    return paths


@dataclass
class TxnReport:
    committed: int = 0
    rolled_back: int = 0
    denied: int = 0
    errors: int = 0
    proof_denied: int = 0
    log: list = field(default_factory=list)
    final: dict = field(default_factory=dict)
    # the claims of the state the board was left in, as the object (not
    # the dict): a chain of transactions judges each link against the
    # one before it, and two evaluations of one state are not the same
    # object.  Not serialized.
    last: object = None

    def as_dict(self) -> dict:
        return {'committed': self.committed, 'rolled_back': self.rolled_back,
                'denied': self.denied, 'errors': self.errors,
                'proof_denied': self.proof_denied,
                'log': self.log[-40:], 'final': self.final}


def board_fingerprint(board_path: str) -> str:
    """The bytes-hash of the board file (16 hex): what a number was measured on."""
    try:
        with open(board_path, 'rb') as fh:
            return hashlib.sha256(fh.read()).hexdigest()[:16]
    except OSError:
        return ''


def transactional_repair(board_path: str, action_fn, eval_fn,
                         max_rounds: int = 3, log=None,
                         initial: Claims = None,
                         hook=None, kind: str = 'repair',
                         stop_on_rollback: bool = True,
                         decider: dict = None, board_fp: str = None,
                         proof_mode: str = None) -> TxnReport:
    """Run ``action_fn`` in a transaction until it stops helping.

    Args:
        board_path: the board to modify in place.
        action_fn: ``action_fn(board_path) -> dict``; may write sidecars.
        eval_fn: ``eval_fn(board_path) -> Claims``; must be deterministic
            given the bytes on disk (it may refill zones first).
        max_rounds: hard cap (each round is one operator pass + one DRC).
        kind: stage name for the harness budget ('repair' / 'reroute'), so
            two stages do not spend each other's action budget.
        stop_on_rollback: end the loop at the first rejected batch.  A loop
            that handles one violation class per round turns this off, so a
            class that cannot be repaired does not block the others.

    Every round — committed, rolled back or denied — is logged WITH the
    action's statistics (operators run, repaired, failed, operator ledger).
    A meta loop that only sees rolled-back rounds cannot attribute a pass.
    """
    say = log or (lambda *a, **k: None)
    rep = TxnReport()
    # who decided this round, and which board state it was measured on.
    # The board hash is taken once, before any round: a hash that changes
    # with every round cannot be compared across the log.
    who = dict(decider or {'kind': 'rule', 'id': kind})
    fp = board_fingerprint(board_path) if board_fp is None else board_fp
    before = initial if initial is not None else eval_fn(board_path)
    # per-step proof gate: every committed mutation is verified; enforced only
    # when FLUXPCB_PROOF=enforce (default audit records the certificate).
    proof_mode = (os.environ.get('FLUXPCB_PROOF', 'audit')
                  if proof_mode is None else proof_mode)
    pctx = {'state': None, 'report': None, 'fp': None}
    pf_rules = None
    if proof_mode != 'off':
        try:
            from fluxpcb.proof import state as _S, step as _step
            pf_rules = _S.board_rules(board_path)[0]
            st0, rep0 = _step.cached(board_path)
            if st0 is None:
                st0 = _S.extract(board_path, pf_rules)
                rep0 = _step.audit(st0, pf_rules,
                                   board_path=board_path)['report']
            pctx = {'state': st0, 'report': rep0, 'fp': fp}
        except Exception as e:
            say(f'proof: begin failed ({e}); proof gate off for this call')
            proof_mode = 'off'
    rep.final = before.as_dict()
    rep.last = before
    for r in range(max_rounds):
        if before.total == 0:
            say(f'round {r}: clean, nothing to repair')
            break
        if hook is not None:
            allowed, why = hook.before_action(kind, {'round': r})
            if not allowed:
                say(f'round {r}: harness denied ({why})')
                rep.denied += 1
                rep.log.append({'round': r, 'decision': 'denied',
                                'stage': kind, 'why': why,
                                'before': before.as_dict(),
                                'decider': who, 'board_fp': fp})
                break
        snap = snapshot(board_path)
        try:
            stats = action_fn(board_path) or {}
            if stats.get('error') and not stats.get('operators_run'):
                # The action failed before doing anything (an isolated
                # reroute that could not start, say).  That is an instrument
                # error, not evidence that the repair does not help, and it
                # must not be filed as a "no improvement" rollback.
                restore_snapshot(snap)
                _remove_new_sidecars(board_path, snap)
                rep.errors += 1
                rep.log.append({'round': r, 'decision': 'action_error',
                                'stage': kind,
                                'why': str(stats.get('error'))[:400],
                                'before': before.as_dict(), 'action': stats,
                                'decider': who, 'board_fp': fp})
                say(f'round {r}: action error ({stats.get("error")}); restored')
                break
            if stats.get('no_op'):
                # Nothing was left to select: the loop is done, and an
                # evaluation round (a full DRC) would buy nothing.
                break
            if hook is not None:
                ok_ops, why_ops = hook.after_action(stats.get('operators_run'))
                if not ok_ops:
                    restore_snapshot(snap)
                    say(f'round {r}: harness denied ({why_ops}); restored')
                    rep.denied += 1
                    rep.log.append({'round': r, 'decision': 'denied',
                                    'stage': kind, 'why': why_ops,
                                    'before': before.as_dict(),
                                    'action': stats,
                                    'decider': who, 'board_fp': fp})
                    break
        except Exception as e:
            restore_snapshot(snap)
            _remove_new_sidecars(board_path, snap)
            say(f'round {r}: action error {e}; restored')
            rep.rolled_back += 1
            break
        try:
            after = eval_fn(board_path)
        except Exception as e:
            restore_snapshot(snap)
            _remove_new_sidecars(board_path, snap)
            say(f'round {r}: evaluation error {e}; restored')
            rep.rolled_back += 1
            break
        ok, why = accept(before, after)
        proof_blocked = False
        if ok and proof_mode != 'off':
            try:
                from fluxpcb.proof import state as _S, step as _step
                st_after = _S.extract(board_path, pf_rules)
                pr = _step.preserve(pctx['state'], st_after, pf_rules,
                                    before_report=pctx['report'],
                                    board_path=board_path)
                if not pr['ok']:
                    if proof_mode == 'enforce':
                        restore_snapshot(snap)
                        _remove_new_sidecars(board_path, snap)
                        rep.proof_denied += 1
                        rep.rolled_back += 1
                        rep.log.append({'round': r, 'decision': 'proof_denied',
                                        'stage': kind, 'why': pr['reasons'][:4],
                                        'proof': pr['summary'],
                                        'before': before.as_dict(),
                                        'after': after.as_dict(),
                                        'decider': who, 'board_fp': fp})
                        say(f'round {r}: proof denied ({pr["reasons"][:2]}); restored')
                        _step.seed(board_path, pctx['state'], pctx['report'])
                        before = eval_fn(board_path)
                        if stop_on_rollback:
                            break
                        ok = False
                        proof_blocked = True
                    else:
                        rep.log.append({'round': r, 'decision': 'proof_audit_fail',
                                        'stage': kind, 'why': pr['reasons'][:4],
                                        'proof': pr['summary'],
                                        'decider': who, 'board_fp': fp})
                if ok:
                    pctx = {'state': st_after, 'report': pr['report'],
                            'fp': board_fingerprint(board_path)}
            except Exception as e:
                if proof_mode == 'enforce':
                    restore_snapshot(snap)
                    _remove_new_sidecars(board_path, snap)
                    rep.errors += 1
                    rep.log.append({'round': r, 'decision': 'proof_error',
                                    'stage': kind, 'why': str(e)[:400],
                                    'decider': who, 'board_fp': fp})
                    say(f'round {r}: proof error ({e}); restored')
                    break
                say(f'round {r}: proof error ({e}); audit only')
        if proof_blocked:
            continue
        if ok:
            rep.committed += 1
            rep.log.append({'round': r, 'decision': 'commit', 'why': why,
                            'stage': kind,
                            'before': before.as_dict(), 'after': after.as_dict(),
                            'action': stats, 'decider': who, 'board_fp': fp})
            say(f'round {r}: commit ({why})')
            before = after
            rep.final = after.as_dict()
            rep.last = after
        else:
            restore_snapshot(snap)
            _remove_new_sidecars(board_path, snap)
            rep.rolled_back += 1
            rep.log.append({'round': r, 'decision': 'rollback', 'why': why,
                            'stage': kind,
                            'before': before.as_dict(), 'after': after.as_dict(),
                            'action': stats, 'decider': who, 'board_fp': fp})
            say(f'round {r}: rollback ({why})')
            if stop_on_rollback:
                break
    return rep


def restore_snapshot(snap: dict):
    for p, blob in snap.items():
        with open(p, 'wb') as f:
            f.write(blob)


def _remove_new_sidecars(board_path: str, snap: dict):
    """Remove sidecars created during a transaction that is being rolled back."""
    base = os.path.splitext(board_path)[0]
    for suf in _SIDECARS:
        p = base + suf
        if p not in snap and os.path.exists(p):
            try:
                os.remove(p)
            except OSError:
                pass


def snapshot(board_path: str) -> dict:
    out = {}
    for p in _snapshot_paths(board_path):
        with open(p, 'rb') as f:
            out[p] = f.read()
    return out
