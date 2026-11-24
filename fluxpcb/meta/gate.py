"""C2 gate: a policy candidate activates only with held-out evidence.

Pure function so the driver, tests and any future sandbox share one rule.
"""
from __future__ import annotations

AHR_TOLERANCE = 0.02


def validate_candidate(incumbent: dict, candidate: dict, *,
                       incumbent_heldout: dict, candidate_heldout: dict,
                       scope_narrowed: bool = False,
                       ahr_delta: float = 0.0,
                       regressed: list = None) -> dict:
    """Decide activation.

    incumbent/candidate: {'routing_clean': int, 'boards': int,
                          'total_errors': int, 'seconds': float}
    heldout dicts: same shape (a different board family).
    """
    reasons = []
    if scope_narrowed:
        reasons.append('scope narrowed (SII)')
    if ahr_delta > AHR_TOLERANCE:
        reasons.append(f'JLC AHR regressed by {ahr_delta:.3f}')
    regressed = regressed or []
    if regressed:
        reasons.append(f'{len(regressed)} clean training boards became dirty')

    def delta(inc, cand):
        n = max(1, inc.get('boards', 0))
        return (cand.get('routing_clean', 0)
                - inc.get('routing_clean', 0)) / n

    train_delta = delta(incumbent, candidate)
    held_delta = delta(incumbent_heldout, candidate_heldout)
    if held_delta <= 0:
        reasons.append(f'held-out delta {held_delta:+.3f} <= 0')
    if train_delta < 0:
        reasons.append(f'training delta {train_delta:+.3f} < 0')
    # Pareto companion: total errors must not rise on held-out.
    if (candidate_heldout.get('total_errors', 0)
            > incumbent_heldout.get('total_errors', 0)):
        reasons.append('held-out total errors increased')
    return {
        'ok': not reasons,
        'train_delta': round(train_delta, 4),
        'heldout_delta': round(held_delta, 4),
        'ahr_delta': round(ahr_delta, 4),
        'reasons': reasons,
    }
