"""P2 tests: capability registry is the dispatch order, stubs are not run."""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from fluxpcb.verifiers.operator_spec import (OperatorRegistry,  # noqa: E402
                                            OperatorSpec, default_registry)
from fluxpcb.verifiers.operators import RepairOperators, Violation  # noqa: E402


def test_default_registry_order_and_availability():
    reg = default_registry()
    assert reg.names() == [
        'short_rip_reroute', 'widen_narrow_tracks', 'clearance_chain',
        'conflict_reroute', 'endpoint_attach', 'plane_bridge', 'topology_swap',
        'connect_unconnected', 'maze_escape']
    assert [s.name for s in reg.unavailable()] == [
        'plane_bridge', 'topology_swap']
    # A stub never claims a class, so dispatch cannot run it.
    assert reg.for_type('via_dangling') == []
    assert [s.name for s in reg.for_type('unconnected')] == [
        'connect_unconnected', 'maze_escape']


def test_every_available_method_exists_on_the_operator_library():
    ops = RepairOperators()
    for spec in default_registry().available():
        assert callable(getattr(ops, spec.method, None)), spec.method


def test_registry_rejects_duplicate_empty_and_silent_stub():
    reg = OperatorRegistry()
    reg.register(OperatorSpec('a', 'm', ('x',), 1))
    with pytest.raises(ValueError):
        reg.register(OperatorSpec('a', 'm', ('y',), 2))
    with pytest.raises(ValueError):
        reg.register(OperatorSpec('b', 'm', (), 3))
    with pytest.raises(ValueError):
        reg.register(OperatorSpec('c', 'm', ('x',), 4, available=False))


def test_applicable_requires_available_and_matching_class():
    reg = default_registry()
    spec = [s for s in reg.ordered() if s.name == 'widen_narrow_tracks'][0]
    assert spec.applicable(Violation(type='track_width'))
    assert not spec.applicable(Violation(type='clearance'))
    stub = [s for s in reg.ordered() if s.name == 'plane_bridge'][0]
    assert not stub.applicable(Violation(type='via_dangling'))
