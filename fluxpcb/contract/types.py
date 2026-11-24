"""types — the typed contract kernel.

Every observed defect is a seam crossed by a *name* instead of a *type*:

    "completion"  = drawn segments OR electrical connections
    "clearance"   = 0.2 mm from table A OR 0.254 mm from table B
    "floor"       = before OR after the widening action
    "measurement" = a number OR a number plus the state it measured

This module makes those confusions unrepresentable at the boundary:

* ``Clearance`` carries the ``RuleSetId`` that minted it; only the rule
  engine can mint ids, so a clearance from a foreign table cannot be
  constructed.
* ``Completion`` carries the criterion explicitly; there is no default.
* ``BoardState`` is phase-indexed (typestate); an intent legal in one phase
  is rejected in another.
* ``Measurement`` refuses to exist without a state fingerprint and a
  declared resolution.

Python annotations are erased at runtime, so each type validates in
``__post_init__``.  A bare ``dict`` or ``float`` must not cross a component
boundary; ``validate_boundary`` enforces that.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import Enum
from typing import Literal, Optional

Mm = float  # nominal unit marker; use Clearance/Measurement for values


class Phase(str, Enum):
    PRE_FLOOR = 'pre_floor'
    POST_FLOOR = 'post_floor'
    POST_REROUTE = 'post_reroute'


# Intents legal in each phase.  A floor may not be revisited after the
# widening action; a reroute may not run before the floor is established.
_LEGAL = {
    Phase.PRE_FLOOR: {'place', 'route', 'set_floor'},
    Phase.POST_FLOOR: {'widen', 'waive', 'verify', 'route'},
    Phase.POST_REROUTE: {'widen', 'verify'},
}


@dataclass(frozen=True)
class BoardState:
    phase: Phase
    fingerprint: str  # content hash of the artifact + rules + tool versions

    def __post_init__(self):
        if not self.fingerprint or len(self.fingerprint) < 8:
            raise ValueError('BoardState requires a content fingerprint')

    def allows(self, intent: str) -> bool:
        return intent in _LEGAL[self.phase]

    def require(self, intent: str) -> 'BoardState':
        if not self.allows(intent):
            raise ValueError(f'intent {intent!r} is not legal in phase '
                             f'{self.phase.value}')
        return self


class RuleRegistry:
    """Only the rule engine mints RuleSetId values."""

    _minted: dict = {}

    @classmethod
    def mint(cls, name: str, digest: str) -> str:
        rid = f'{name}:{digest[:12]}'
        cls._minted[rid] = True
        return rid

    @classmethod
    def owns(cls, rid: str) -> bool:
        return rid in cls._minted


@dataclass(frozen=True)
class Clearance:
    mm: float
    rule_set: str  # a RuleSetId minted by RuleRegistry

    def __post_init__(self):
        if not isinstance(self.mm, (int, float)):
            raise TypeError('Clearance.mm must be a number')
        if self.mm < 0:
            raise ValueError('Clearance.mm must be non-negative')
        if not RuleRegistry.owns(self.rule_set):
            raise ValueError(f'foreign rule set {self.rule_set!r}: only the '
                             'rule engine may mint a RuleSetId')


@dataclass(frozen=True)
class Completion:
    """A completion figure *with* the criterion that produced it."""
    segments: int
    nets: int
    criterion: Literal['segments', 'nets']

    def __post_init__(self):
        if self.criterion not in ('segments', 'nets'):
            raise ValueError('Completion.criterion must be segments or nets')
        if self.segments < 0 or self.nets < 0:
            raise ValueError('negative counts')

    def value(self) -> int:
        return self.segments if self.criterion == 'segments' else self.nets


@dataclass(frozen=True)
class Measurement:
    """A number that knows which state and channel it came from."""
    value: float
    unit: str
    resolution: float
    state_rho: str
    gate: str
    basis: str = 'model-only'

    def __post_init__(self):
        if not self.state_rho:
            raise ValueError('a measurement without a state fingerprint is '
                             'not comparable to anything')
        if self.resolution < 0:
            raise ValueError('resolution must be non-negative')
        if self.unit not in ('nm', 'mm', 'um'):
            raise ValueError(f'unknown unit {self.unit!r}')

    def comparable_with(self, other: 'Measurement') -> bool:
        return (self.unit == other.unit
                and self.resolution == other.resolution
                and self.state_rho == other.state_rho)

    def to(self, unit: str) -> 'Measurement':
        factor = {'nm': 1.0, 'um': 1e-3, 'mm': 1e-6}[unit] / \
                 {'nm': 1.0, 'um': 1e-3, 'mm': 1e-6}[self.unit]
        return Measurement(self.value * factor, unit, self.resolution,
                           self.state_rho, self.gate, self.basis)


_BOUNDARY_TYPES = (Clearance, Completion, Measurement, BoardState)


def validate_boundary(value, what: str = 'value'):
    """Reject a raw dict/number at a component boundary."""
    if isinstance(value, _BOUNDARY_TYPES):
        return value
    raise TypeError(f'{what}: raw {type(value).__name__} at a component '
                    'boundary; pass a typed contract object')


def state_rho(artifact_bytes: bytes, rules_digest: str,
              tool_version: str) -> str:
    h = hashlib.sha256()
    h.update(artifact_bytes)
    h.update(rules_digest.encode())
    h.update(tool_version.encode())
    return h.hexdigest()
