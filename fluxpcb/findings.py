"""One finding, one vocabulary (leaf module: no imports from the package).

Every conclusion any verifier or gate reaches is a :class:`Finding`: a
stable rule id, where it is, what was measured against what limit, the
assumption it rests on, how sure it is, and which board state it was
measured on.  The point is that a reader — a meta loop, a page, a
person — can tell a confirmed manufacturing defect from a pre-route
estimate without asking the code that produced it.

The four rules, enforced here rather than by convention:

  * ``status='estimate'`` caps severity at P1.  An estimate may be
    alarming, never as alarming as a measured defect (P0).
  * ``status='unknown'`` carries no measurement and must say what
    information is missing.  "I do not know" is a result; "0" is not.
  * a measurement that was not taken stays ``None`` — never 0.0
    (``operators.Violation.has_measurement`` set the precedent).
  * every finding names the assumption it rests on ('none' when it
    stands on the measurement alone).

``as_dict`` also emits the legacy keys (``issue/type/net/actual/
required/location/items``) that ``engine.run_pipeline``, the pre-route
screen in ``stages`` and ``verifiers.suite._extract_violations`` read,
so adopting the type does not break the consumers that predate it.
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from typing import Any, Optional

# P0 is the most severe.  The order matters: estimates are capped at P1.
SEVERITIES = ('P0', 'P1', 'P2', 'P3')
_RANK = {s: i for i, s in enumerate(SEVERITIES)}
STATUSES = ('confirmed', 'estimate', 'unknown')
PHASES = ('pre_route', 'post_route')
ESTIMATE_CAP = 'P1'
NO_ASSUMPTION = 'none'


def cap(severity: str, status: str) -> str:
    """The severity a finding may carry: an estimate never outranks a
    measurement."""
    if status == 'estimate' and _RANK[severity] < _RANK[ESTIMATE_CAP]:
        return ESTIMATE_CAP
    return severity


@dataclass(frozen=True)
class Finding:
    rule_id: str
    where: dict = field(default_factory=dict)
    measured: Optional[float] = None
    limit: Optional[float] = None
    units: str = ''
    severity: str = 'P1'
    status: str = 'confirmed'
    phase: str = 'post_route'
    assumption: str = NO_ASSUMPTION
    needed_information: tuple = ()
    source: dict = field(default_factory=dict)
    board_fp: str = ''
    rule_version: str = '1'
    # what the item is: a DRC type when the finding comes from the DRC,
    # else the rule's own family (used by the legacy aliases below)
    kind: str = ''
    net: str = ''
    net2: str = ''
    items: tuple = ()

    def __post_init__(self):
        if not self.rule_id or not isinstance(self.rule_id, str):
            raise ValueError('a finding needs a rule_id')
        if self.severity not in SEVERITIES:
            raise ValueError(f'{self.rule_id}: severity {self.severity!r} '
                             f'not one of {SEVERITIES}')
        if self.status not in STATUSES:
            raise ValueError(f'{self.rule_id}: status {self.status!r} not '
                             f'one of {STATUSES}')
        if self.phase not in PHASES:
            raise ValueError(f'{self.rule_id}: phase {self.phase!r} not one '
                             f'of {PHASES}')
        if not isinstance(self.assumption, str) or not self.assumption.strip():
            raise ValueError(f'{self.rule_id}: a finding must state the '
                             f'assumption it rests on (use {NO_ASSUMPTION!r})')
        if self.status == 'estimate':
            object.__setattr__(self, 'severity',
                               cap(self.severity, self.status))
        if self.status == 'unknown':
            if self.measured is not None:
                raise ValueError(f'{self.rule_id}: an unknown finding carries '
                                 f'no measurement')
            if not self.needed_information:
                raise ValueError(f'{self.rule_id}: an unknown finding must say '
                                 f'what information is missing')
        if self.measured is not None and not self.units:
            raise ValueError(f'{self.rule_id}: a measurement needs units')
        if isinstance(self.needed_information, list):
            object.__setattr__(self, 'needed_information',
                               tuple(self.needed_information))

    # ------------------------------------------------------------ reading
    @property
    def rank(self) -> int:
        """0 for P0; lower is more severe."""
        return _RANK[self.severity]

    @property
    def blocking(self) -> bool:
        """A confirmed P0 is a manufacturing failure, not advice."""
        return self.status == 'confirmed' and self.severity == 'P0'

    @property
    def at(self) -> Optional[tuple]:
        x, y = self.where.get('x_mm'), self.where.get('y_mm')
        return (x, y) if x is not None and y is not None else None

    def issue(self) -> str:
        """One line, human readable: rule, measurement, limit, assumption."""
        body = self.rule_id
        if self.measured is not None and self.limit is not None:
            body += (f': {_num(self.measured)} {self.units} '
                     f'against {_num(self.limit)} {self.units}')
        elif self.measured is not None:
            body += f': {_num(self.measured)} {self.units}'
        elif self.status == 'unknown':
            body += ': not measured (' + '; '.join(self.needed_information) + ')'
        if self.assumption and self.assumption != NO_ASSUMPTION:
            body += f' [assumes {self.assumption}]'
        if self.status == 'estimate':
            body += ' (estimate)'
        return body

    def as_dict(self) -> dict:
        """The finding as a plain dict, with the keys consumers already read."""
        kind = self.kind or self.rule_id.split('.')[0].lower()
        at = self.at or (0.0, 0.0)
        d = {
            'rule_id': self.rule_id,
            'rule_version': self.rule_version,
            'where': dict(self.where),
            'measured': self.measured,
            'limit': self.limit,
            'units': self.units,
            'severity': self.severity,
            'status': self.status,
            'phase': self.phase,
            'assumption': self.assumption,
            'needed_information': list(self.needed_information),
            'source': dict(self.source),
            'board_fp': self.board_fp,
            'net': self.net,
            'net2': self.net2,
            'items': [dict(i) if isinstance(i, dict) else i
                      for i in self.items],
            # legacy aliases (engine retry feedback, pre-route screen,
            # suite._extract_violations read these)
            'issue': self.issue(),
            'type': kind,
            'actual': self.measured,
            'required': self.limit,
            'location': [at[0], at[1]],
        }
        return d

    def digest(self) -> str:
        body = json.dumps(self.as_dict(), sort_keys=True, default=str)
        return hashlib.sha256(body.encode('utf-8')).hexdigest()[:16]


def _num(v: float) -> str:
    if v is None:
        return '?'
    if isinstance(v, bool):
        return str(v)
    if float(v) == int(v):
        return str(int(v))
    return f'{v:.4g}'


def now_iso() -> str:
    return time.strftime('%Y-%m-%dT%H:%M:%S', time.localtime())


def source(solver: str, version: str = '', detail: Any = None) -> dict:
    """Who measured it, with what, when — the provenance a reader needs."""
    s = {'solver': solver, 'version': version or '', 'at': now_iso()}
    if detail is not None:
        s['detail'] = detail
    return s


def unknown(rule_id: str, missing, **kw) -> Finding:
    """A finding that says what is missing instead of guessing."""
    return Finding(rule_id, status='unknown',
                   needed_information=tuple(missing), **kw)


def verify_index(pairs) -> list:
    """Problems with a set of findings (used by tests and the page).

    `pairs` is an iterable of findings; returns a list of messages, empty
    when every finding obeys the four rules.
    """
    out = []
    for f in pairs:
        if not isinstance(f, Finding):
            out.append(f'not a Finding: {type(f).__name__}')
            continue
        if f.status == 'estimate' and f.rank < _RANK[ESTIMATE_CAP]:
            out.append(f'{f.rule_id}: estimate at {f.severity}')
        if f.status == 'unknown' and (f.measured is not None
                                      or not f.needed_information):
            out.append(f'{f.rule_id}: unknown without a stated gap')
        if f.measured is not None and f.limit is not None and f.units == '':
            out.append(f'{f.rule_id}: measurement without units')
        if not f.assumption:
            out.append(f'{f.rule_id}: no assumption')
    return out
