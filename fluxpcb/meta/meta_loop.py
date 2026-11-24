#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""meta_loop — the loop above the loop (Component 2).

Component 1 proposes changes to components 3 and 4.  This loop governs
that: it decides what may be CALLED an improvement, and it adjusts how
component 1 spends its next generation.

The gate is the important half.  A change is admitted only on evidence
that component 1 could not have produced by itself:

  * the instruments are clean (no selection leak, no stage that failed to
    start) — a broken measurement is not a result;
  * on the boards used to choose (the coreset) it cleaned more than it
    dirtied, and dirtied nothing beyond the A/A noise;
  * on boards NEVER used to choose (the held-out set) it did not regress;
  * on sealed known-bad controls nothing was judged clean — the scorer
    was not blinded;
  * the scope of what is checked did not narrow (SII);
  * the second, fab-profile oracle did not regress.

Each check is reported as pass / fail / not_run; a gate that was not
measured does not pass silently.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Optional

from ..contracts import HarnessDiff

AHR_TOLERANCE = 0.02


def _digest(evidence: dict) -> str:
    body = json.dumps(evidence, sort_keys=True, separators=(',', ':'),
                      default=str)
    return hashlib.sha256(body.encode('utf-8')).hexdigest()[:16]


def confirm(evidence: dict) -> dict:
    """Decide whether a change to components 3/4 may be called an improvement.

    `evidence` is produced by the evaluation, never by the proposer:
      kind               'param' | 'selector' | 'harness'
      instrument_ok      every cell's instruments were clean
      instrument_problems
      action_errors      stages that failed to start
      coreset            {'wins', 'losses', 'boards'}
      noise_flips        boards the A/A duplicate flipped on its own
      heldout            {'n', 'incumbent_clean', 'candidate_clean',
                          'lost': [boards], 'gained': [boards]} or None
      controls           {'n', 'clean': [names]} or None
      scope_narrowed     bool
      ahr                {'incumbent': float, 'candidate': float} or None
    """
    ev = evidence or {}
    checks, reasons = {}, []

    if not ev.get('instrument_ok', False):
        checks['instrument'] = 'fail'
        reasons.append('instrument: ' + '; '.join(
            (ev.get('instrument_problems') or ['unclean'])[:3]))
    else:
        checks['instrument'] = 'pass'

    if ev.get('action_errors', 0):
        checks['execution'] = 'fail'
        reasons.append(f"{ev['action_errors']} stage(s) failed to start")
    else:
        checks['execution'] = 'pass'

    co = ev.get('coreset') or {}
    noise = int(ev.get('noise_flips') or 0)
    wins, losses = int(co.get('wins') or 0), int(co.get('losses') or 0)
    if wins <= losses:
        checks['coreset'] = 'fail'
        reasons.append(f'coreset: no net gain ({wins} wins / {losses} losses)')
    elif losses > noise:
        checks['coreset'] = 'fail'
        reasons.append(f'coreset: dirtied {losses} boards, A/A noise is {noise}')
    else:
        checks['coreset'] = 'pass'

    ho = ev.get('heldout')
    if not ho:
        checks['heldout'] = 'not_run'
        reasons.append('held-out: not measured')
    else:
        n = max(1, int(ho.get('n') or 1))
        inc = int(ho.get('incumbent_clean') or 0)
        cand = int(ho.get('candidate_clean') or 0)
        lost = list(ho.get('lost') or [])
        if cand < inc - noise:
            checks['heldout'] = 'fail'
            reasons.append(f'held-out regressed: {inc} -> {cand} of {n}')
        elif len(lost) > noise:
            checks['heldout'] = 'fail'
            reasons.append(f'held-out: {len(lost)} boards lost '
                           f'(A/A noise {noise}): {lost[:5]}')
        else:
            checks['heldout'] = 'pass'

    ct = ev.get('controls')
    if not ct:
        checks['controls'] = 'not_run'
        reasons.append('controls: not measured')
    elif ct.get('clean'):
        checks['controls'] = 'fail'
        reasons.append(f"controls: {len(ct['clean'])} known-bad boards "
                       f"judged clean: {list(ct['clean'])[:4]}")
    else:
        checks['controls'] = 'pass'

    if ev.get('scope_narrowed', False):
        checks['scope'] = 'fail'
        reasons.append('scope narrowed (SII)')
    else:
        checks['scope'] = 'pass'

    ahr = ev.get('ahr')
    if not ahr:
        checks['ahr'] = 'not_run'
        reasons.append('JLC fab-profile oracle: not measured')
    else:
        delta = float(ahr.get('candidate', 0)) - float(ahr.get('incumbent', 0))
        if delta > AHR_TOLERANCE:
            checks['ahr'] = 'fail'
            reasons.append(f'JLC AHR regressed by {delta:.3f}')
        else:
            checks['ahr'] = 'pass'

    return {'ok': not reasons, 'reasons': reasons, 'checks': checks,
            'kind': ev.get('kind'), 'evidence_digest': _digest(ev),
            'heldout_delta': (round(
                (int(ho['candidate_clean']) - int(ho['incumbent_clean']))
                / max(1, int(ho.get('n') or 1)), 4) if ho else None)}


def diff_from(evidence: dict, description: str = '') -> HarnessDiff:
    """A HarnessDiff carrying the gate's evidence (for the contract type)."""
    verdict = confirm(evidence)
    return HarnessDiff(
        description=description or f"change to components 3/4: {evidence.get('kind')}",
        changes={'kind': evidence.get('kind'),
                 'delta': evidence.get('delta'),
                 'evidence_digest': verdict['evidence_digest']},
        heldout_delta=verdict['heldout_delta'],
        scope_narrowed=bool(evidence.get('scope_narrowed', False)))


@dataclass
class HarnessPlan:
    """How component 1 should spend its next generation."""
    changes: dict = field(default_factory=dict)
    description: str = ''
    validated: bool = False


class MetaMetaLoop:
    """Governs component 1: the gate, and how the next generation is spent."""

    def __init__(self, harness_version: Any = None, workspace: Any = None,
                 memory: Any = None):
        self.harness_version = harness_version
        self.workspace = workspace
        self.memory = memory
        self._diffs: list = []
        self.confirmations: list = []

    # -- the gate -----------------------------------------------------
    def confirm(self, evidence: dict) -> dict:
        verdict = confirm(evidence)
        self.confirmations.append(verdict)
        if self.memory is not None:
            self.memory.append('judgment', **{k: v for k, v in verdict.items()
                                              if k != 'reasons'},
                               reasons=verdict['reasons'],
                               delta=evidence.get('delta'),
                               checks=evidence.get('checks_extra'))
        return verdict

    def validate(self, diff: HarnessDiff, frozen_tests: Any = None) -> bool:
        """A diff counts only if it is held-out positive and narrows nothing.

        No evidence -> not validated.
        """
        if diff is None or not getattr(diff, 'changes', None):
            return False
        if getattr(diff, 'scope_narrowed', False):
            return False
        delta = getattr(diff, 'heldout_delta', None)
        if delta is None or delta <= 0:
            return False
        self._diffs.append(diff)
        return True

    # -- improving component 1's own process --------------------------
    def improve(self, meta_trajectories: list) -> HarnessDiff:
        """From the generations so far: what component 1 should do next.

        * retire a variable that has been tried at least twice and never
          admitted (it stops consuming the candidate budget);
        * focus the budget on the variables that were admitted and on the
          variables behind the largest remaining failure pattern;
        * if no candidate has been evaluated for the largest pattern,
          widen the candidate budget for one generation.

        The plan is advisory until the configuration it produces passes
        `confirm`; it is recorded either way.
        """
        gens = [t if isinstance(t, dict) else getattr(t, 'as_dict', lambda: {})()
                for t in (meta_trajectories or [])]
        tried: dict = {}
        for g in gens:
            for r in g.get('results') or []:
                for p in g.get('proposals') or []:
                    if p.get('variable') and any(
                            c.get('variable') == p['variable'] for c in []):
                        pass
            for r in g.get('results') or []:
                name = r.get('candidate', '')
                for p in g.get('proposals') or []:
                    key = f"{p.get('variable')}={json.dumps(p.get('value'), default=str)}"
                    if name.endswith(key) or name.endswith(key[:40]):
                        bucket = tried.setdefault(p['variable'], [0, 0])
                        bucket[1] += 1
                        bucket[0] += 1 if r.get('wins', 0) > r.get('losses', 0) else 0
        retire = sorted(v for v, (won, n) in tried.items() if n >= 2 and won == 0)
        focus = sorted(v for v, (won, n) in tried.items() if won > 0)
        largest = _largest_pattern(gens)
        widen = int(bool(largest) and not any(tried.get(v, [0, 0])[1]
                                              for v in largest.get('variables', [])))
        changes = {'retire': retire, 'focus': focus,
                   'largest_pattern': largest.get('name') if largest else None,
                   'extra_candidates': widen}
        diff = HarnessDiff(
            description=(f'from {len(gens)} generation(s): retire {retire}, '
                         f'focus {focus}, largest pattern '
                         f"{largest.get('name') if largest else 'none'}"),
            changes=changes)
        self._diffs.append(diff)
        if self.memory is not None:
            self.memory.append('harness', **changes, validated=False)
        return diff

    @property
    def diff_count(self) -> int:
        return len(self._diffs)


def _largest_pattern(gens: list) -> dict:
    best = {}
    for g in gens:
        for pat in (g.get('diagnosis') or {}).get('patterns') or []:
            if isinstance(pat, dict) and pat.get('count', 0) > best.get('count', -1):
                best = pat
    return best


__all__ = ['MetaMetaLoop', 'confirm', 'diff_from', 'HarnessPlan']
