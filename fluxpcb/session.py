"""session — one board's measure/act/commit/verdict path.

The benchmark and the 12-component pipeline used to have separate commit
paths and evaluators.  A DesignSession owns the four things a verdict needs:
scope, ledger, transaction, and the worst-first verdict.  Callers supply
`eval_fn` (measure) and `action_fn` (repair), so the same session works for
KiCad DRC today and another referee later.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .verifiers.binding import check_binding
from .verifiers.live import record_board
from .verifiers.txn import Claims, transactional_repair


@dataclass
class DesignSession:
    board_path: str
    scope: object
    ledger_path: Optional[str] = None
    hook: object = None
    register: object = None
    model: object = None
    claims: Optional[Claims] = None
    txn: object = None
    readiness: dict = field(default_factory=dict)

    def measure(self, eval_fn) -> Claims:
        self.claims = eval_fn(self.board_path)
        return self.claims

    def repair(self, action_fn, eval_fn, max_rounds: int = 3,
               initial: Claims = None):
        self.txn = transactional_repair(
            self.board_path, action_fn, eval_fn, max_rounds=max_rounds,
            initial=initial, hook=self.hook)
        self.claims = eval_fn(self.board_path)
        return self.txn

    def record(self, rule_verified: bool, project_ctx: dict,
               binding_ctx: dict = None, assumptions=()) -> dict:
        if not self.ledger_path or self.claims is None:
            return {}
        ctx = binding_ctx or check_binding(
            self.board_path, self.board_path + '.drc.json.binding.json')
        self.readiness = record_board(
            self.ledger_path, self.scope, rule_verified=rule_verified,
            project_ctx=project_ctx or {}, binding_ctx=ctx,
            claims=self.claims, register=self.register, model=self.model,
            assumptions=assumptions)
        return self.readiness

    def verdict(self) -> dict:
        """Board verdict + readiness; never a bare scalar pass."""
        if self.claims is None:
            return {'board_worst': 'unknown', 'readiness': 'unknown'}
        board = ('pass' if (self.claims.total == 0
                            and self.claims.rule_congruent) else 'fail')
        return {'board_worst': board,
                'readiness': (self.readiness or {}).get('worst', 'unknown'),
                'claims': self.claims.as_dict(),
                'txn': (self.txn.as_dict() if self.txn is not None else None)}
