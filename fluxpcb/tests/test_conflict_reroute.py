"""conflict_reroute — the pieces that need no board.

  * which ends of a connection get lifted, in which order;
  * the priced search: it names the copper that closes a corridor, never
    a pad, and says when the set is larger than allowed;
  * the two-layer planner: a wall on each layer is a path through two
    vias, the same wall on both is no path;
  * the board edge is an obstacle;
  * the raster the fine grid is built from agrees cell for cell with
    the distance test the exact verifier uses.
"""
import math
import os
import random
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from fluxpcb.kicadgen import geomir, microroute  # noqa: E402
from fluxpcb.verifiers import conflict_reroute as CR  # noqa: E402

RT = {'clearance': 0.2, 'clearance_smd': 0.2}
ME, OTHER = 5, 7
A, B = (0.0, 0.0), (8.0, 0.0)


def _track(x1, y1, x2, y2, hw=0.1, net=OTHER):
    return (net, (x1, y1), (x2, y2), hw)


def test_lifts_grow_both_ends_in_turn_smallest_first():
    assert CR.lifts(2, 5, 4) == [(0, 0), (0, 1), (1, 1), (1, 2)]
    assert CR.lifts(0, 1, 4) == [(0, 0)]          # a run of one track
    assert CR.lifts(0, 3, 4) == [(0, 0), (0, 1), (0, 2)]


def test_the_priced_search_names_the_copper_that_closes_the_corridor():
    wall = [_track(4.0, -3.0, 4.0, 3.0)]
    assert microroute.plan_local(A, B, 0.2, ME, wall, [], RT, margin=2.0) is None
    got = microroute.plan_local(A, B, 0.2, ME, wall, [], RT, margin=2.0,
                                cross={'cost': 10.0, 'max': 2})
    assert got['why'] == 'crosses' and len(got['crossed']) == 1
    assert got['crossed'][0][0] == OTHER and got['pts']
    # the same wall with a gap in it is a path, and nothing is crossed
    gap = [_track(4.0, 0.75, 4.0, 3.0), _track(4.0, -3.0, 4.0, -0.75)]
    assert microroute.plan_local(A, B, 0.2, ME, gap, [], RT, margin=2.0)
    got = microroute.plan_local(A, B, 0.2, ME, gap, [], RT, margin=2.0,
                                cross={'cost': 10.0, 'max': 2})
    assert got['why'] == 'clear' and got['crossed'] == []


def test_a_set_larger_than_allowed_is_reported_not_returned():
    walls = [_track(3.0, -3.0, 3.0, 3.0), _track(5.0, -3.0, 5.0, 3.0)]
    got = microroute.plan_local(A, B, 0.2, ME, walls, [], RT, margin=2.0,
                                cross={'cost': 10.0, 'max': 1})
    assert got['why'] == 'set_too_large' and got['pts'] is None
    assert len(got['crossed']) == 2
    got = microroute.plan_local(A, B, 0.2, ME, walls, [], RT, margin=2.0,
                                cross={'cost': 10.0, 'max': 2})
    assert got['why'] == 'crosses' and len(got['crossed']) == 2


def test_pads_are_never_crossed():
    pad = (OTHER, (4.0, 0.0, 0.2, 3.0, 0.0, False))
    got = microroute.plan_local(A, B, 0.2, ME, [], [pad], RT, margin=2.0,
                                cross={'cost': 10.0, 'max': 4})
    assert got['why'] == 'no_path' and got['crossed'] == []


def _verified(got, width, nc, segs, rects, via_r):
    """Every same-layer segment and every via of a plan, by the exact tests."""
    for p, q in zip(got['pts'], got['pts'][1:]):
        if p[2] != q[2]:
            assert geomir.via_ok(p[0], p[1], nc, segs, rects, via_r=via_r,
                                 rules=RT)[0]
        else:
            assert geomir.verify_polyline([p[:2], q[:2]], width, nc,
                                          segs[p[2]], rects[p[2]], RT)[0]


def test_two_layers_turn_two_walls_into_a_path_through_two_vias():
    segs = [[_track(4.0, -10.0, 4.0, 10.0)], [_track(6.0, -10.0, 6.0, 10.0)]]
    # no layer on its own carries the connection
    for li in (0, 1):
        assert microroute.plan_local(A, B, 0.2, ME, segs[li], [], RT,
                                     margin=2.0) is None
    got = microroute.plan_layers(A, B, 0, 0, 0.2, ME, segs, [[], []], RT, via_r=0.3,
                                 via_cost=4.0, margin=2.0)
    assert got is not None and got['vias'] == 2
    layers = [p[2] for p in got['pts']]
    assert layers[0] == layers[-1] == 0 and 1 in layers
    _verified(got, 0.2, ME, segs, [[], []], 0.3)
    # the wall on both layers at the same place is no path
    both = [[_track(4.0, -10.0, 4.0, 10.0)], [_track(4.0, -10.0, 4.0, 10.0)]]
    assert microroute.plan_layers(A, B, 0, 0, 0.2, ME, both, [[], []], RT,
                                  via_r=0.3, margin=2.0) is None
    # nothing in the way: a straight line, no via (a via costs more than
    # it could ever save here)
    got = microroute.plan_layers(A, B, 0, 0, 0.2, ME, [[], []], [[], []], RT,
                                 via_r=0.3, margin=2.0)
    assert got['vias'] == 0 and got['pts'] == [(0.0, 0.0, 0), (8.0, 0.0, 0)]


def test_the_board_edge_is_an_obstacle():
    # the wall leaves 0.5 mm to the top and bottom of a 4 mm board
    wall = [_track(4.0, 0.5, 4.0, 3.5)]
    edge = (0.0, 0.0, 8.0, 4.0)
    assert microroute.plan_local((1.0, 2.0), (7.0, 2.0), 0.2, ME, wall, [],
                                 RT, margin=2.0)
    assert microroute.plan_local((1.0, 2.0), (7.0, 2.0), 0.2, ME, wall, [],
                                 RT, margin=2.0, edge=edge,
                                 edge_keep=0.5) is None
    # a via's barrel is copper too
    assert microroute.plan_layers((1.0, 2.0), (7.0, 2.0), 0, 0, 0.2, ME,
                                  [wall, wall], [[], []], RT, via_r=0.3,
                                  margin=2.0, edge=edge, edge_keep=0.5) is None


def test_the_raster_is_the_distance_test_cell_for_cell():
    """The grid is painted obstacle by obstacle (a whole-window sweep
    cost 100x more).  It must say exactly what testing every cell
    against every obstacle says."""
    rng = random.Random(11)
    nc, width = 3, 0.25
    segs, rects = [], []
    for _ in range(14):
        x, y = rng.uniform(-1, 5), rng.uniform(-1, 5)
        segs.append((rng.choice([1, 2, 4]), (x, y),
                     (x + rng.uniform(-2, 2), y + rng.uniform(-2, 2)),
                     rng.choice([0.1, 0.125, 0.15])))
    for _ in range(8):
        rects.append((rng.choice([1, 2, 4]),
                      (rng.uniform(0, 4), rng.uniform(0, 3),
                       rng.uniform(0.2, 0.6), rng.uniform(0.2, 0.6),
                       rng.choice([0.0, 0.6]), False,
                       rng.choice([0.0, 0.0, 0.5]))))
    x0, y0, step, nx, ny = -0.5, -0.5, 0.05, 110, 80
    grid, owner, items = microroute._raster(nx, ny, x0, y0, step, width, nc,
                                            segs, rects, RT, True)
    own = width / 2
    for j in range(ny):
        for i in range(nx):
            x, y = x0 + i * step, y0 + j * step
            free = True
            for (net, s, e, hw) in segs:
                if net != nc and microroute.rg.pt_seg_dist(x, y, s[0], s[1], e[0], e[1]) < 0.2 + hw + own:
                    free = False
            for (net, rect) in rects:
                if net != nc and geomir.pt_pad_dist(x, y, rect) < geomir.pad_need(rect, 0.2) + own:
                    free = False
            assert bool(grid[j][i]) == free, (i, j)
            if not free and owner[j][i]:
                assert items[owner[j][i] - 1][0] != nc
    assert len(items) == len([g for g in segs if g[0] != nc])
