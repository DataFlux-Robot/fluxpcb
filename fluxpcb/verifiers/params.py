"""Typed operator parameters — the variables the meta loops optimise.

Until now every ladder and cap lived as an inline literal in an operator,
so the only way to change one was to edit code by hand.  Collected here
they become named variables with a schema: a proposal from component 1 is
a (name, value) pair that must validate against the schema, and a value
that does not compile cannot change behaviour.

The defaults reproduce the hand-written values exactly, so constructing
`OperatorParams()` does not change any operator's behaviour.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, fields, replace


def _int(lo, hi):
    return {'kind': 'int', 'min': lo, 'max': hi}


def _float(lo, hi):
    return {'kind': 'float', 'min': lo, 'max': hi}


def _num_tuple(lo, hi, n_min, n_max, integer=False):
    return {'kind': 'int_tuple' if integer else 'float_tuple',
            'min': lo, 'max': hi, 'n_min': n_min, 'n_max': n_max}


# name -> schema.  A parameter that is not in here is not a variable.
SCHEMA = {
    # polyline connector
    'connect_cap': _int(1, 60),
    'corridor_offsets_mm': _num_tuple(0.05, 20.0, 1, 12),
    'via_slot_radii_mm': _num_tuple(0.1, 5.0, 1, 12),
    'jitter_candidates': _int(1, 20),
    'jitter_steps_mm': _num_tuple(0.005, 2.0, 1, 12),
    # clearance nudge
    'nudge_cap': _int(1, 60),
    'nudge_steps_um': _num_tuple(2.0, 1000.0, 1, 12),
    # grid maze
    'maze_cap': _int(1, 40),
    'maze_max_expand': _int(10000, 1000000),
    'maze_grid_extra_mm': _float(0.0, 0.5),
    # short rip-up
    'rip_cap': _int(1, 60),
    # width
    'blind_widen': {'kind': 'bool'},
    # after a blind widening, measure the tracks the cheap check flagged
    # and settle the ones that really conflict (instead of losing the
    # whole batch to the transaction gate)
    'widen_measure': {'kind': 'bool'},
    # settle a real conflict by re-planning that connection at full width
    'widen_replan': {'kind': 'bool'},
    # conflict reroute: lift the boxed-in connection, re-plan it (other
    # layers allowed); when its layer is closed, lift the conflict set
    # too and route it again after
    'conflict_reroute': {'kind': 'bool'},
    'conflict_cap': _int(1, 60),
    'conflict_max_set': _int(1, 4),
    'conflict_trials': _int(1, 24),
    'conflict_lifts': _int(1, 8),
    # conflict sets tried per connection: when one cannot be routed again
    # the next cheapest is priced
    'conflict_sets': _int(1, 8),
    # and no longer than this per call, whatever the count
    'conflict_budget_s': _float(10.0, 3000.0),
    # the router ran out of time (the bench loop): route once more with
    # other settings and a longer cap
    'router_retry': {'kind': 'bool'},
    'router_retry_threads': _int(1, 64),
    'router_retry_scale': _float(1.0, 6.0),
    # take dangling stubs off at the end (the bench loop)
    'trim_dangling': {'kind': 'bool'},
    # the repair round as one transaction per operator (the bench loop)
    'stepwise_repair': {'kind': 'bool'},
    'width_cascade': _num_tuple(0.05, 0.5, 1, 8),
    'signal_floor_mm': _float(0.05, 0.3),
    'power_floor_mm': _float(0.05, 0.5),
    'jlc_clearance_floor_mm': _float(0.05, 0.3),
    'waiver_band_mm': _float(0.0, 0.2),
    # plan pre-route
    'preroute_max_nets': _int(1, 40),
    'preroute_max_edges': _int(1, 200),
    'preroute_pair_mm': _float(1.0, 30.0),
    # geometric verifier
    'pad_clearance_strict': {'kind': 'bool'},
}


def check_value(name: str, value):
    """Return the normalised value or raise ValueError with the reason."""
    spec = SCHEMA.get(name)
    if spec is None:
        raise ValueError(f'unknown variable {name!r}')
    kind = spec['kind']
    if kind == 'bool':
        if not isinstance(value, bool):
            raise ValueError(f'{name}: expected bool, got {type(value).__name__}')
        return value
    if kind in ('int', 'float'):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f'{name}: expected number, got {value!r}')
        if kind == 'int':
            if float(value) != int(value):
                raise ValueError(f'{name}: expected integer, got {value!r}')
            value = int(value)
        else:
            value = float(value)
        if not spec['min'] <= value <= spec['max']:
            raise ValueError(f'{name}: {value} outside '
                             f'[{spec["min"]}, {spec["max"]}]')
        return value
    # tuple kinds
    if isinstance(value, (str, bytes)) or not hasattr(value, '__iter__'):
        raise ValueError(f'{name}: expected a list, got {type(value).__name__}')
    items = list(value)
    if not spec['n_min'] <= len(items) <= spec['n_max']:
        raise ValueError(f'{name}: {len(items)} items outside '
                         f'[{spec["n_min"]}, {spec["n_max"]}]')
    out = []
    for it in items:
        if isinstance(it, bool) or not isinstance(it, (int, float)):
            raise ValueError(f'{name}: non-numeric item {it!r}')
        v = float(it)
        if kind == 'int_tuple':
            if v != int(v):
                raise ValueError(f'{name}: expected integers, got {it!r}')
            v = int(v)
        if not spec['min'] <= v <= spec['max']:
            raise ValueError(f'{name}: item {v} outside '
                             f'[{spec["min"]}, {spec["max"]}]')
        out.append(v)
    return tuple(out)


@dataclass(frozen=True)
class OperatorParams:
    # polyline connector
    connect_cap: int = 20
    corridor_offsets_mm: tuple = (0.5, 1.0, 1.5, 2.0, 4.0, 8.0)
    via_slot_radii_mm: tuple = (0.3, 0.5, 0.8, 1.1, 1.5)
    jitter_candidates: int = 4
    jitter_steps_mm: tuple = (0.02, 0.05, 0.1, 0.2, 0.3)
    # clearance nudge
    nudge_cap: int = 20
    nudge_steps_um: tuple = (10, 20, 40, 80, 160, 320)
    # grid maze
    maze_cap: int = 6
    maze_max_expand: int = 120000
    maze_grid_extra_mm: float = 0.0
    # short rip-up
    rip_cap: int = 20
    # width
    blind_widen: bool = True
    widen_measure: bool = True
    widen_replan: bool = True
    # conflict reroute
    conflict_reroute: bool = True
    conflict_cap: int = 20
    conflict_max_set: int = 2
    conflict_trials: int = 6
    conflict_lifts: int = 4
    conflict_sets: int = 3
    conflict_budget_s: float = 300.0
    # router timeout retry: 0344 needs 5 passes and 157-221 s of a loaded
    # machine; 2 threads were the slowest setting measured, 8 the fastest
    router_retry: bool = True
    router_retry_threads: int = 8
    router_retry_scale: float = 2.5
    trim_dangling: bool = True
    stepwise_repair: bool = True
    width_cascade: tuple = (0.2, 0.15, 0.127, 0.11)
    signal_floor_mm: float = 0.11
    power_floor_mm: float = 0.15
    jlc_clearance_floor_mm: float = 0.09
    waiver_band_mm: float = 0.03
    # plan pre-route
    preroute_max_nets: int = 8
    preroute_max_edges: int = 24
    preroute_pair_mm: float = 6.0      # a pad pair at most this far apart is "local"
    # Judge track-vs-pad at the netclass clearance (what DRC does) instead
    # of the pad-to-pad rule.
    pad_clearance_strict: bool = True

    def __post_init__(self):
        # Normalise every variable through the schema, so an arm built in
        # memory and the same arm read back from JSON hash identically
        # (integer ladders would otherwise come back as floats).
        for f in fields(self):
            if f.name in SCHEMA:
                object.__setattr__(self, f.name,
                                   check_value(f.name, getattr(self, f.name)))

    @classmethod
    def variables(cls) -> list:
        """[(name, schema, current value)] — what a proposer may change."""
        base = cls()
        return [(f.name, SCHEMA[f.name], getattr(base, f.name))
                for f in fields(cls) if f.name in SCHEMA]

    def with_delta(self, name: str, value) -> 'OperatorParams':
        return replace(self, **{name: check_value(name, value)})

    def as_dict(self) -> dict:
        return {f.name: (list(getattr(self, f.name))
                         if isinstance(getattr(self, f.name), tuple)
                         else getattr(self, f.name))
                for f in fields(self)}

    @classmethod
    def from_dict(cls, d: dict = None) -> 'OperatorParams':
        d = d or {}
        kw = {}
        for f in fields(cls):
            if f.name in d:
                kw[f.name] = check_value(f.name, d[f.name])
        return cls(**kw)

    def digest(self) -> str:
        body = json.dumps(self.as_dict(), sort_keys=True,
                          separators=(',', ':'))
        return hashlib.sha256(body.encode('utf-8')).hexdigest()[:16]

    def deltas_from(self, other: 'OperatorParams') -> list:
        """[(name, before, after)] for every variable that differs."""
        out = []
        for f in fields(self):
            a, b = getattr(other, f.name), getattr(self, f.name)
            if a != b:
                out.append((f.name, a, b))
        return out
