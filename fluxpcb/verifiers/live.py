"""live — record one board's evidence into a live ledger.

The ledger is no longer reconstructed after the fact from cert integers:
the runner writes it while it measures, with the scope id, rule ids, and
binding context.  `audit_ledger.py` can then read the same file.
"""
from __future__ import annotations

from .ledger import Claim, Ledger, Outcome, Verdict, default_registry, rho


def record_board(ledger_path: str, scope, *, rule_verified: bool,
                 project_ctx: dict, binding_ctx: dict,
                 claims, register=None, model=None,
                 assumptions=()) -> dict:
    """Record the board's claims; return {worst, by_claim, rho_chain_ok}."""
    reg = default_registry()
    led = Ledger(ledger_path)

    def rec(suffix, gate, ctx, **kw):
        c = Claim(f'{scope.board}/{suffix}', suffix, owner='live',
                  gate=gate, scope_id=scope.scope_id(),
                  rule_id=kw.pop('rule_id', ''), source=kw.pop('source', ''),
                  **kw)
        v = reg.evaluate(gate, ctx)
        led.record(c, v)
        return v

    rec('rule_congruence', 'rule_congruence', {'verified': bool(rule_verified)})
    rec('project_identity', 'project_identity', project_ctx or {})
    rec('report_binding', 'report_binding', binding_ctx or {})
    if register is not None:
        rec('rule_scope', 'rule_scope',
            {'declared_rule_ids': sorted(register.declared_ids()),
             'represented_rule_ids': sorted(register.represented_ids(model))})
    rec('drc_clean', 'drc_clean',
        {'n_error_violations': getattr(claims, 'n_errors', None)})
    rec('unconnected_zero', 'unconnected_zero',
        {'n_unconnected': getattr(claims, 'n_unconnected', None)})

    for a in assumptions or ():
        c = Claim(f'{scope.board}/assumption:{a}', f'assumption {a}',
                  mechanism='assumed', owner='live', scope_id=scope.scope_id())
        led.record(c, Verdict(Outcome.ASSUMED, gate='',
                              note=f'unverified premise: {a}',
                              rho=rho('assumption', '1',
                                      {'name': a,
                                       'scope': scope.scope_id()})))

    board_ids = [f'{scope.board}/{s}' for s in
                 ('rule_congruence', 'project_identity', 'report_binding',
                  'drc_clean', 'unconnected_zero')]
    scope_ids = [f'{scope.board}/rule_scope']
    assumed_ids = [f'{scope.board}/assumption:{a}' for a in (assumptions or ())]
    ok, why = led.verify_chain()
    return {
        # Physical verdict under the declared claim set (the benchmark's
        # clean-pass equivalent).
        'board_worst': led.worst(board_ids).value,
        # Readiness: worst-first including the scope gap and assumptions.
        'worst': led.worst(board_ids + scope_ids + assumed_ids).value,
        'scope_worst': led.worst(scope_ids).value,
        'by_claim': {i.split('/', 1)[1]: led.status(i).value
                     for i in board_ids + scope_ids + assumed_ids},
        'chain_ok': ok, 'chain_note': why,
    }
