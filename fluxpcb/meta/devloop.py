#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""devloop — the loop above the development loop (Component 1).

Components 3 (the repair loop) and 4 (the plan) execute an ARM: a typed
configuration.  This loop improves them from evidence, in three stages:

  diagnose   read the traces of one arm on real boards and name the failure
             patterns, with the typed variable each one points at;
  evolve     turn the diagnosis into candidate arms, each ONE variable away
             from the base — proposed by an LLM from the traces when one is
             configured, otherwise by a rule table;
  select     evaluate the candidates where the current arm actually has
             trouble (on-policy) and keep only what cleans more than it
             dirties.

plus a per-board selector: which arm suits which kind of board, chosen
from placement features alone.

Nothing here invents a score.  `select` needs a real evaluator, and a
generation reports whatever that evaluator measured — including nothing.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Optional

from ..contracts import RoutingCandidate
from .diagnose import diagnose
from .feedback import FailurePacket
from .memory import Memory, delta_key
from .proposer import as_record, candidate_arm, propose
from .strategy_store import fit_selector


@dataclass
class MetaRunResult:
    """One generation of the meta-dev loop."""
    generation: int = 0
    base_arm: Optional[str] = None
    diagnosis: dict = field(default_factory=dict)
    proposals: list = field(default_factory=list)   # what was proposed, by whom
    candidates: list = field(default_factory=list)  # candidate arm names
    results: list = field(default_factory=list)     # evaluator output per candidate
    admitted: list = field(default_factory=list)    # coreset-level acceptance
    selector: Optional[dict] = None
    candidates_evaluated: int = 0
    seconds: float = 0.0
    status: str = 'no_data'      # ok | no_data | blocked | no_proposal | no_evaluator
    blocked_by: Optional[str] = None

    def as_dict(self) -> dict:
        return {'generation': self.generation, 'base_arm': self.base_arm,
                'diagnosis': self.diagnosis, 'proposals': self.proposals,
                'candidates': self.candidates, 'results': self.results,
                'admitted': self.admitted, 'selector': self.selector,
                'candidates_evaluated': self.candidates_evaluated,
                'seconds': self.seconds, 'status': self.status,
                'blocked_by': self.blocked_by}


def _delta(candidate) -> str:
    p = candidate.params
    return delta_key(p.get('base', ''), p.get('variable', ''),
                     p.get('value'))


class MetaDevLoop:
    """diagnose -> evolve -> select, over arms instead of over thresholds."""

    def __init__(self, task: Any = None, optimizer: Any = None,
                 store: Any = None, memory: Any = None,
                 allow_llm: bool = True, max_candidates: int = 3):
        self.task = task
        self.optimizer = optimizer
        self.store = store
        self.memory = memory
        self.allow_llm = allow_llm
        self.max_candidates = max_candidates
        self._arms: dict = {}          # candidate name -> Arm
        self._base_arm = None
        self.last_proposals: list = []
        self._history: list = []

    def arm(self, name: str):
        return self._arms.get(name)

    # -- stage 1 ------------------------------------------------------
    @staticmethod
    def diagnose(trajectories: list) -> dict:
        """Failure patterns with the typed variables they point at."""
        return diagnose(trajectories)

    # -- stage 2 ------------------------------------------------------
    def evolve(self, diagnosis: dict, packets: list = None,
               base_arm=None) -> list:
        """Candidate arms, each one variable away from the base arm."""
        if base_arm is not None:
            self._base_arm = base_arm
        if self._base_arm is None:
            return []
        if not diagnosis.get('instrument_ok', False):
            self.last_proposals = []
            return []
        resumed = self._resumed(self._base_arm)
        if resumed:
            # An interrupted generation left proposals without a verdict.
            # Finish those before asking for anything new, otherwise they
            # would be skipped as "already tried" and never measured.
            out = {'proposals': resumed}
        else:
            out = propose(packets or [], self._base_arm, memory=self.memory,
                          max_deltas=self.max_candidates,
                          allow_llm=self.allow_llm)
            if self.memory is not None:
                for p in out['proposals']:
                    self.memory.append(
                        'proposal', delta=p.key(self._base_arm.name),
                        variable=p.variable, value=p.value,
                        proposer=p.proposer, pattern=p.pattern, why=p.why)
        self.last_proposals = out['proposals']
        cands = []
        for p in out['proposals']:
            arm = candidate_arm(self._base_arm, p)
            self._arms[arm.name] = arm
            cands.append(RoutingCandidate(
                name=arm.name, category='one_variable',
                params={'base': self._base_arm.name,
                        'variable': p.variable, 'value': p.value,
                        'proposer': p.proposer, 'pattern': p.pattern,
                        'why': p.why, 'arm_digest': arm.digest()},
                expected_improvement=0.0, risk=0.0))
        return cands

    def _resumed(self, base_arm) -> list:
        """Proposals made against `base_arm` that were never evaluated."""
        if self.memory is None:
            return []
        from .proposer import Proposal
        pending = self.memory.pending()
        out, seen = [], set()
        for e in self.memory.entries('proposal'):
            key = e.get('delta')
            if (key in pending and key not in seen
                    and key.startswith(base_arm.name + '|')):
                seen.add(key)
                out.append(Proposal(
                    variable=e['variable'], value=e['value'],
                    why=e.get('why', ''), proposer=e.get('proposer', 'rules'),
                    pattern=e.get('pattern', '')))
        return out[:self.max_candidates]

    # -- stage 3 ------------------------------------------------------
    def select(self, candidates: list, board: Any = None,
               evaluator: Any = None) -> tuple:
        """The candidate whose real evaluation cleaned most, net of what it dirtied.

        `evaluator(candidate)` returns {'wins', 'losses', ...}: boards the
        candidate cleaned that the base arm did not, and the reverse.
        """
        if evaluator is None:
            # Refusing to fabricate: a meta layer that invents its own
            # scores cannot be wrong, which is the definition of decorative.
            raise RuntimeError(
                'MetaDevLoop.select requires a real evaluator; '
                'fabricated scores are not permitted')
        best, best_key = None, None
        for candidate in candidates:
            result = evaluator(candidate)
            if result is not None:
                self._history.append(dict(result, candidate=candidate.name))
                key = (result.get('wins', 0) - result.get('losses', 0),
                       -result.get('losses', 0),
                       -result.get('mean_wall', 0))
                if result.get('wins', 0) > result.get('losses', 0) and (
                        best_key is None or key > best_key):
                    best, best_key = candidate, key
        del board
        return best, best_key

    # -- channel A ----------------------------------------------------
    @staticmethod
    def fit(packets: list) -> dict:
        """A per-board selector fitted on the given packets."""
        return fit_selector(packets)

    # -- on-policy ----------------------------------------------------
    @staticmethod
    def on_policy(packets: list, base: str) -> list:
        """Boards the current arm fails, or passes only because repair fired.

        The next generation is evaluated where the incumbent has trouble:
        its training distribution is its deployment distribution.
        """
        out = []
        for p in packets:
            if p.arm != base or p.status not in ('ok', 'budget'):
                continue
            dependent = any(r.get('decision') == 'commit'
                            for r in (p.rounds or []))
            if p.outcome != 'clean' or dependent:
                out.append(p.board)
        return sorted(set(out))

    # -- one generation -----------------------------------------------
    def generation(self, packets: list, base_arm, evaluator=None,
                   generation: int = 0) -> MetaRunResult:
        t0 = time.time()
        self._base_arm = base_arm
        res = MetaRunResult(generation=generation, base_arm=base_arm.name)
        mine = [p for p in packets if p.arm == base_arm.name]
        res.selector = fit_selector(packets)
        if not mine:
            res.status, res.seconds = 'no_data', round(time.time() - t0, 1)
            return res
        res.diagnosis = diagnose(mine)
        if not res.diagnosis['instrument_ok']:
            res.status, res.blocked_by = 'blocked', 'instrument'
            res.seconds = round(time.time() - t0, 1)
            return res
        cands = self.evolve(res.diagnosis, mine)
        res.proposals = as_record(self.last_proposals)
        res.candidates = [c.name for c in cands]
        res.candidates_evaluated = len(cands)
        if not cands:
            res.status = 'no_proposal'
        elif evaluator is None:
            res.status = 'no_evaluator'
        else:
            for c in cands:
                out = evaluator(c)
                if out is None:
                    res.status = 'no_evaluator'
                    break
                res.results.append(dict(out, candidate=c.name))
                won = out.get('wins', 0) > out.get('losses', 0)
                if won:
                    res.admitted.append(c.name)
                if self.memory is not None:
                    self.memory.append(
                        'result', delta=_delta(c), candidate=c.name,
                        outcome='admitted' if won else 'rejected',
                        wins=out.get('wins', 0), losses=out.get('losses', 0),
                        generation=generation)
            else:
                res.status = 'ok'
        res.seconds = round(time.time() - t0, 1)
        return res

    # -- legacy entry point -------------------------------------------
    def run(self, budget_s: int = 300, packets: list = None,
            base_arm=None, evaluator: Any = None) -> MetaRunResult:
        if not packets or base_arm is None:
            return MetaRunResult(status='no_data')
        return self.generation(packets, base_arm, evaluator=evaluator)

    @property
    def history(self) -> list:
        return list(self._history)


__all__ = ['MetaDevLoop', 'MetaRunResult', 'FailurePacket', 'Memory']
