"""Operator capability registry.

Dispatch used to be a literal if/elif chain over DRC types.  A capability
registry makes the same order data: each entry declares which violation
classes it owns, its priority, whether it is available at all (a stub must
say so instead of reporting success), and which operator names it EMITS.
Dispatch then walks the registry; adding an operator is a declaration, not
a new branch.

`emits` exists because selection and reporting use different names: the
clearance chain is one registry entry but reports three operators.  A
policy is expressed in emitted names, so dispatch needs the mapping to
decide what it may run.  (When this mapping lived only in the harness, a
policy that disallowed one operator vetoed whole batches.)
"""
from __future__ import annotations

from dataclasses import dataclass

TEXT_TO_SILK = 'copper_text_to_silk'
REROUTE_NETS = 'reroute_violating_nets'


@dataclass(frozen=True)
class OperatorSpec:
    name: str
    method: str                 # RepairOperators method to call
    classes: tuple              # violation types (DRC type strings)
    priority: int
    available: bool = True
    reason: str = ""            # required when available is False
    emits: tuple = ()           # operator names this entry reports

    def emitted(self) -> tuple:
        return self.emits or (self.method if self.available else (),)

    def applicable(self, violation) -> bool:
        return self.available and violation.type in self.classes


class OperatorRegistry:
    def __init__(self):
        self._specs = []

    def register(self, spec: OperatorSpec) -> None:
        if any(s.name == spec.name for s in self._specs):
            raise ValueError(f"duplicate operator {spec.name!r}")
        if not spec.classes and spec.available:
            raise ValueError(f"{spec.name}: empty classes")
        if not spec.available and not spec.reason:
            raise ValueError(f"{spec.name}: unavailable needs a reason")
        self._specs.append(spec)

    def ordered(self) -> list:
        return sorted(self._specs, key=lambda s: (s.priority, s.name))

    def available(self) -> list:
        return [s for s in self.ordered() if s.available]

    def unavailable(self) -> list:
        return [s for s in self.ordered() if not s.available]

    def for_type(self, vtype: str) -> list:
        return [s for s in self.available() if vtype in s.classes]

    def names(self) -> list:
        return [s.name for s in self.ordered()]

    def all_emitted(self) -> tuple:
        out = []
        for s in self.available():
            for name in s.emitted():
                if name not in out:
                    out.append(name)
        return tuple(out)


def default_registry() -> OperatorRegistry:
    """Mirror of the historical dispatch order (P2 parity requirement)."""
    reg = OperatorRegistry()
    reg.register(OperatorSpec(
        name='short_rip_reroute', method='short_rip_reroute',
        classes=('shorting_items',), priority=10))
    reg.register(OperatorSpec(
        name='widen_narrow_tracks', method='widen_narrow_tracks',
        classes=('track_width',), priority=20))
    reg.register(OperatorSpec(
        name='clearance_chain', method='_repair_clearance',
        classes=('clearance',), priority=30,
        emits=('width_cascade_surgery', 'policy_waiver_probe',
               'nudge_clearance')))
    reg.register(OperatorSpec(
        name='endpoint_attach', method='endpoint_attach',
        classes=('track_dangling',), priority=40))
    # A stub that draws nothing must not be dispatched as if it worked.
    reg.register(OperatorSpec(
        name='plane_bridge', method='plane_bridge',
        classes=('via_dangling',), priority=50, available=False,
        reason='stub: records a bridge but draws no geometry'))
    reg.register(OperatorSpec(
        name='topology_swap', method='topology_swap',
        classes=(), priority=55, available=False,
        reason='stub: records a swap but draws no geometry'))
    reg.register(OperatorSpec(
        name='connect_unconnected', method='connect_unconnected_geomir',
        classes=('unconnected',), priority=60,
        emits=('connect_unconnected',)))
    reg.register(OperatorSpec(
        name='maze_escape', method='maze_escape',
        classes=('unconnected',), priority=61))
    return reg


def all_operator_names() -> tuple:
    """Every operator name a repair stage can report."""
    names = [TEXT_TO_SILK]
    for n in default_registry().all_emitted():
        if n not in names:
            names.append(n)
    return tuple(names)
