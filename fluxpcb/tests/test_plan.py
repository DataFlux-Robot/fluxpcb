"""P1 tests: typed RoutingPlan + capacity claims (no pcbnew needed)."""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from fluxpcb.design_agent.capacity import ball_rings, channel_capacity  # noqa: E402
from fluxpcb.design_agent.plan import CapacityClaim, RoutingPlan  # noqa: E402


def _plan(**kw):
    base = dict(board_sha='b', rule_digest='r',
                capacity=CapacityClaim(verdict='sufficient', margin=1),
                net_order=['N1', 'N2'], pre_routed_nets=['N1'],
                claims=[{'id': 'capacity'}])
    base.update(kw)
    return RoutingPlan(**base)


def test_channel_capacity_h7_is_zero_and_pad_shrink_fixes_it():
    # H7: 0.8 pitch, 0.4 pad, 0.127 track, 0.15 clearance
    assert channel_capacity(0.8, 0.4, 0.127, 0.15) == 0
    # shrinking the pad to 0.35 buys exactly one trace per channel
    assert channel_capacity(0.8, 0.35, 0.127, 0.15) == 1


def test_ball_rings_counts_from_centroid():
    pos = [(x, y) for x in (0.0, 1.0, 2.0) for y in (0.0, 1.0, 2.0)]
    rings = ball_rings(pos, 1.0)
    assert rings == {0: 1, 1: 8}
    assert max(rings) + 1 == 2


def test_plan_digest_is_stable_and_content_addressed():
    a, b = _plan(), _plan()
    assert a.digest() == b.digest()
    c = _plan(net_order=['N2', 'N1'])
    assert c.digest() != a.digest()
    d = _plan(pre_routed_nets=[])
    assert d.digest() != a.digest()


def test_plan_validate_rejects_contract_breaks():
    assert _plan().validate() == []
    assert 'board_sha' in ' '.join(_plan(board_sha='').validate())
    infeasible = _plan(capacity=CapacityClaim(verdict='infeasible'))
    assert any('must not pre-route' in e for e in infeasible.validate())
    assert any('duplicates' in e for e in
               _plan(net_order=['N1', 'N1']).validate())
    assert any('net_order' in e for e in
               _plan(pre_routed_nets=['N9']).validate())


def test_unknown_capacity_routes_but_infeasible_does_not():
    assert _plan(capacity=CapacityClaim(verdict='unknown')).is_routable()
    assert not _plan(capacity=CapacityClaim(verdict='infeasible'),
                     pre_routed_nets=[]).is_routable()


def test_plan_json_roundtrip(tmp_path):
    p = _plan(escape_regions=[{'center': [1, 2], 'radius_mm': 3.0}],
              budgets={'maze_max_seconds': 30})
    path = str(tmp_path / 'plan.json')
    digest = p.to_json(path)
    q = RoutingPlan.from_json(path)
    assert q.digest() == digest
    assert q.escape_regions == p.escape_regions
    assert q.capacity.verdict == 'sufficient'
