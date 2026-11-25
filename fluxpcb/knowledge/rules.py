"""The rule index: stable ids, default severity/status, the assumption each
gate's findings rest on, and where the rule is documented.

Data lives in ``rules_index.json`` beside this module.  It exists so that
a finding produced by a gate that only knows about its own numbers still
comes out with an id a manual can cite, a severity a reader can rank,
and an assumption a reviewer can disagree with.

    rule_entry('DRC.report')        -> {...} or {}
    gate_defaults('drc_zero')       -> {'rule_id': ..., 'severity': ...}
    normalise(findings, gate)       -> [dict]   (Finding.as_dict for each)
"""
from __future__ import annotations

import json
import os
from functools import lru_cache

_HERE = os.path.dirname(os.path.abspath(__file__))
_PATH = os.path.join(_HERE, 'rules_index.json')


@lru_cache(maxsize=1)
def index() -> dict:
    try:
        with open(_PATH, encoding='utf-8') as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {'version': '', 'gates': {}, 'rules': {}}


@lru_cache(maxsize=1)
def version() -> str:
    return index().get('version', '')


def gate_defaults(gate_name: str) -> dict:
    """How findings of `gate_name` are read when it does not say more."""
    return dict(index().get('gates', {}).get(gate_name) or {})


def rule_entry(rule_id: str) -> dict:
    g = index().get('rules', {}).get(rule_id)
    if g is not None:
        return dict(g)
    # a rule declared only in the gate table still exists
    for entry in (index().get('gates') or {}).values():
        if entry.get('rule_id') == rule_id:
            return dict(entry)
    return {}


def known(rule_id: str) -> bool:
    return bool(rule_entry(rule_id))


def normalise(findings, gate_name: str, **shared) -> list:
    """Every finding of one gate as a Finding dict.

    A dict that already carries ``rule_id`` is left as it is (the gate
    knew what it was saying).  Anything else is wrapped with the gate's
    entry: rule id, severity, status, assumption, documentation anchor.
    `shared` (board_fp, source, phase) reaches every wrapped finding.
    """
    from ..findings import Finding, NO_ASSUMPTION

    d = gate_defaults(gate_name)
    out = []
    for i, f in enumerate(findings or []):
        body = f if isinstance(f, dict) else {'issue': str(f)}
        # A gate may name its own rule; it still gets the full vocabulary,
        # because a partial dict leaking out is how a finding ends up with
        # no severity and an `issue` a consumer has to parse.
        own_rule = str(body.get('rule_id') or '')
        where = body.get('where')
        if not where:
            loc = body.get('location') or body.get('pos')
            if isinstance(loc, (list, tuple)) and len(loc) >= 2:
                where = {'x_mm': loc[0], 'y_mm': loc[1]}
            elif isinstance(loc, dict):
                where = {'x_mm': loc.get('x'), 'y_mm': loc.get('y')}
        measured = body.get('measured', body.get('actual'))
        limit = body.get('limit', body.get('required'))
        try:
            measured = None if measured is None else float(measured)
        except (TypeError, ValueError):
            measured = None
        try:
            limit = None if limit is None else float(limit)
        except (TypeError, ValueError):
            limit = None
        status = body.get('status') or d.get('status', 'confirmed')
        unknown = status == 'unknown'
        missing = body.get('needed_information') or (
            [str(body.get('issue') or 'measurement missing')] if unknown else [])
        rule_meta = rule_entry(own_rule) if own_rule else {}
        try:
            fd = Finding(
                rule_id=own_rule or d.get('rule_id') or f'{gate_name}.{i}',
                rule_version=body.get('rule_version') or version(),
                where=dict(where or {}),
                measured=None if unknown else measured,
                limit=limit if not unknown else None,
                units=body.get('units') or ('mm' if measured is not None
                                            or limit is not None else ''),
                severity=body.get('severity') or d.get('severity', 'P1'),
                status=status,
                phase=body.get('phase') or shared.get('phase') or 'post_route',
                assumption=(body.get('assumption')
                            or rule_meta.get('assumption')
                            or d.get('assumption') or NO_ASSUMPTION),
                needed_information=tuple(missing),
                source=body.get('source') or shared.get('source') or {},
                board_fp=body.get('board_fp') or shared.get('board_fp') or '',
                kind=body.get('type') or body.get('kind') or '',
                net=str(body.get('net') or ''),
                net2=str(body.get('net2') or ''),
                items=tuple(body.get('items') or ()),
            )
        except ValueError as e:
            # a gate finding that cannot obey the rules is not silently
            # dropped: it becomes an unknown finding that says why
            fd = Finding(f'{d.get("rule_id") or gate_name}.malformed',
                         status='unknown',
                         needed_information=[f'gate produced a malformed '
                                             f'finding: {e}'],
                         severity='P1',
                         assumption=d.get('assumption') or NO_ASSUMPTION,
                         source=shared.get('source') or {},
                         board_fp=shared.get('board_fp') or '')
        out.append(fd.as_dict())
    return out
