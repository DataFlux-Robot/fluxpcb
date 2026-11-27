"""Open-net targeting for the proof-directed repair loop."""
from __future__ import annotations

from fluxpcb.proof import repair_loop
from test_proof_gate import _two_net_state


def test_open_nets_finds_the_unreached_net():
    st = _two_net_state(False)          # N connected, M open
    assert repair_loop.open_nets(st) == ['M']
    st = _two_net_state(True)
    assert repair_loop.open_nets(st) == []
