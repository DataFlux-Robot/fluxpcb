"""FailureCert — the certificate every failed repair attempt must leave.

A repair that failed is the only place a "why not" can be observed.  The
schema is one dict so operators, the ledger, and the meta loops all carry
the same keys; `why_not` holds the quantitative payload from
kicadgen/whynot.py (margin, blocker, w_max, sealed, budget).

An attempt with outcome 'ok' is a capability WIN for the error model the
attempt belonged to; anything else is a trial with a reason.
"""
from __future__ import annotations

import hashlib
import json

# attempt outcomes
OK = 'ok'
NO_PATH = 'no_path'
VERIFY_REJECTED = 'verify_rejected'
SEALED = 'sealed'
BUDGET = 'budget_exhausted'
REFUSED = 'refused'
NOT_APPLICABLE = 'not_applicable'
DRC_REJECTED = 'drc_rejected'

OUTCOMES = (OK, NO_PATH, VERIFY_REJECTED, SEALED, BUDGET, REFUSED,
            NOT_APPLICABLE, DRC_REJECTED)


def cert_entry(op, item, outcome, why_not=None, attempt=None,
               requirement=None, board=None) -> dict:
    """One attempt's certificate, with everything the error model reads."""
    if outcome not in OUTCOMES:
        raise ValueError(f'unknown outcome {outcome!r}')
    entry = {
        'operator': op,            # operators' ledger consumers read this key
        'op': op,
        'item': dict(item or {}),
        'outcome': outcome,
        'why_not': dict(why_not or {}),
        'attempt': dict(attempt or {}),
        'requirement': dict(requirement or {}),
        'board': board,
    }
    entry['cert_id'] = digest(entry)
    return entry


def digest(entry: dict) -> str:
    body = json.dumps(entry, sort_keys=True, separators=(',', ':'),
                      default=str)
    return hashlib.sha256(body.encode('utf-8')).hexdigest()[:16]


def outcome_ok(entry: dict) -> bool:
    return entry.get('outcome') == OK


def is_failure(entry: dict) -> bool:
    return bool(entry) and entry.get('outcome') != OK
