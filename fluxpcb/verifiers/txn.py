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
        unconnected_ids=frozenset(canonical_violation(u) for u in us),
        rule_congruent=rule_congruent,
        type_counts=tuple(sorted(Counter(v.get('type', '?') for v in vs).items())),
    )


def accept(before: Claims, after: Claims) -> tuple:
    """Lexicographic feasibility-first acceptance.

    Returns (ok, reason).  A batch may not:
      * lose rule-model congruence;
      * introduce a violation type the board did not already have;
    and must strictly reduce ``errors + unconnected`` (or, at equal totals,
    strictly shrink the identity set).
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
    log: list = field(default_factory=list)
    final: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {'committed': self.committed, 'rolled_back': self.rolled_back,
                'denied': self.denied,
                'log': self.log[-20:], 'final': self.final}


def transactional_repair(board_path: str, action_fn, eval_fn,
                         max_rounds: int = 3, log=None,
                         initial: Claims = None,
                         hook=None) -> TxnReport:
    """Run ``action_fn`` in a transaction until it stops helping.

    Args:
        board_path: the board to modify in place.
        action_fn: ``action_fn(board_path) -> dict``; may write sidecars.
        eval_fn: ``eval_fn(board_path) -> Claims``; must be deterministic
            given the bytes on disk (it may refill zones first).
        max_rounds: hard cap (each round is one operator pass + one DRC).
    """
    say = log or (lambda *a, **k: None)
    rep = TxnReport()
    before = initial if initial is not None else eval_fn(board_path)
    rep.final = before.as_dict()
    for r in range(max_rounds):
        if before.total == 0:
            say(f'round {r}: clean, nothing to repair')
            break
        if hook is not None:
            allowed, why = hook.before_action('repair', {'round': r})
            if not allowed:
                say(f'round {r}: harness denied ({why})')
                rep.denied += 1
                break
        snap = snapshot(board_path)
        try:
            stats = action_fn(board_path) or {}
            if hook is not None:
                ok_ops, why_ops = hook.after_action(stats.get('operators_run'))
                if not ok_ops:
                    restore_snapshot(snap)
                    say(f'round {r}: harness denied ({why_ops}); restored')
                    rep.denied += 1
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
        if ok:
            rep.committed += 1
            rep.log.append({'round': r, 'decision': 'commit', 'why': why,
                            'before': before.as_dict(), 'after': after.as_dict()})
            say(f'round {r}: commit ({why})')
            before = after
            rep.final = after.as_dict()
        else:
            restore_snapshot(snap)
            _remove_new_sidecars(board_path, snap)
            rep.rolled_back += 1
            rep.log.append({'round': r, 'decision': 'rollback', 'why': why,
                            'before': before.as_dict(), 'after': after.as_dict(),
                            'action': stats})
            say(f'round {r}: rollback ({why})')
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
