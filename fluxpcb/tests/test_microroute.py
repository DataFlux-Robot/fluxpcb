"""Local fine-grid re-plan: a corridor tens of microns wide."""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
KG = os.path.join(ROOT, 'fluxpcb', 'kicadgen')
if KG not in sys.path:
    sys.path.insert(0, KG)

from fluxpcb.kicadgen import geomir, microroute  # noqa: E402

RT = {'clearance': 0.2, 'clearance_smd': 0.2}
NC = 5


def _via(x, y, r=0.3, net=2):
    return (net, (x, y), (x, y), r)


def _band_left(d):
    """x of the left edge of the feasible band between two r=0.3 vias
    0.2 mm apart (centre distance d) for a 0.2 mm track at 0.2 mm."""
    return 0.3 + 0.2 + 0.1


def test_threads_a_corridor_67_microns_wide():
    d = 1.2668                      # the 0054 geometry: /SDA via to GND via
    segs = [_via(0.0, 0.0), _via(d, 0.0)]
    a, b = (0.33, 1.2), (0.50, -1.2)
    got = microroute.plan_local(a, b, 0.2, NC, segs, [], RT, margin=0.3)
    assert got is not None
    pts = got['pts']
    assert pts[0] == a and pts[-1] == b
    # every point of the path clears both vias by exactly the rule
    for p, q in zip(pts, pts[1:]):
        ok, blk = geomir.verify_polyline([p, q], 0.2, NC, segs, [], RT)
        assert ok, blk
    # it really went between them: some point near y=0 lies in the band
    near = [p for p in pts if abs(p[1]) < 0.3]
    assert near, pts
    assert all(_band_left(d) - 1e-6 <= p[0] <= d - _band_left(d) + 1e-6 for p in near)
    # and it is a handful of segments, not a staircase
    assert len(pts) <= 8, pts


def test_no_corridor_no_path():
    segs = [_via(0.0, 0.0), _via(1.19, 0.0)]       # band is negative
    assert microroute.plan_local((0.33, 1.2), (0.5, -1.2), 0.2, NC, segs, [], RT,
                                 margin=0.3) is None


def test_goes_around_a_pad_it_cannot_cross():
    pad = (2, (0.0, 0.0, 0.5, 0.25, 0.0, False))    # 1.0 x 0.5 mm, axis-aligned
    a, b = (-1.2, 0.0), (1.2, 0.0)
    got = microroute.plan_local(a, b, 0.2, NC, [], [pad], RT, margin=1.0)
    assert got is not None
    for p, q in zip(got['pts'], got['pts'][1:]):
        assert geomir.verify_polyline([p, q], 0.2, NC, [], [pad], RT)[0]
    assert any(abs(p[1]) >= 0.25 + 0.2 + 0.1 - 1e-6 for p in got['pts'])


def test_a_window_too_small_for_the_detour_is_no_path_not_a_wrong_one():
    pad = (2, (0.0, 0.0, 0.5, 0.25, 0.0, False))
    got = microroute.plan_local((-1.2, 0.0), (1.2, 0.0), 0.2, NC, [], [pad], RT,
                                margin=0.3)         # the detour needs |y| >= 0.55
    assert got is None


def test_same_net_copper_is_not_an_obstacle():
    own_via = [_via(0.0, 0.0, 0.3, net=NC)]
    straight = microroute.plan_local((-0.8, 0.0), (0.8, 0.0), 0.2, NC, own_via, [],
                                     RT, margin=0.5)
    assert straight['pts'] == [(-0.8, 0.0), (0.8, 0.0)]
    wall = (2, (0.0, -0.3), (0.0, 0.3), 0.05)       # a foreign track across the way
    got = microroute.plan_local((-0.8, 0.0), (0.8, 0.0), 0.2, NC, own_via + [wall],
                                [], RT, margin=1.0)
    assert got is not None and len(got['pts']) > 2
    assert max(abs(p[1]) for p in got['pts']) >= 0.3 + 0.2 + 0.1 + 0.05 - 1e-6


def test_threads_where_the_coarse_planner_only_detours():
    # With open space around, geomir goes AROUND the two vias (a 4.7 mm
    # detour); the fine grid threads the 67 µm corridor between them.
    # On 0054 the space around is taken, so the detour is not there to
    # be had — which is what this module is for.
    segs = [_via(0.0, 0.0), _via(1.2668, 0.0)]
    a, b = (0.33, 1.2), (0.50, -1.2)
    plans, _ = geomir.plan_paths(
        a, b, 0.2, NC, [segs], [[]], bounds=(-1, -2, 2, 2),
        width_ladder=(0.2,), rules=RT, planes_by_layer=None)
    fine = microroute.plan_local(a, b, 0.2, NC, segs, [], RT, margin=0.3)
    assert fine is not None
    fine_len = sum(geomir.math.dist(p, q)
                    for p, q in zip(fine['pts'], fine['pts'][1:]))
    if plans:
        assert min(pl['length_mm'] for pl in plans) > fine_len + 1.0


def test_walls_take_away_the_detour_but_not_the_corridor():
    segs = [_via(0.0, 0.0), _via(1.2668, 0.0),
            (2, (-0.9, -2.5), (-0.9, 2.5), 0.1),      # a wall left ...
            (2, (2.2, -2.5), (2.2, 2.5), 0.1),        # ... and a wall right
            (2, (-0.9, 1.6), (2.2, 1.6), 0.1),        # ... above ...
            (2, (-0.9, -1.6), (2.2, -1.6), 0.1)]      # ... and below
    a, b = (0.33, 1.2), (0.50, -1.2)
    plans, _ = geomir.plan_paths(
        a, b, 0.2, NC, [segs], [[]], bounds=(-1, -2, 3, 2),
        width_ladder=(0.2,), rules=RT, planes_by_layer=None)
    same = [pl for pl in plans if int(pl['pts'][0][2]) == 0
            and int(pl['pts'][-1][2]) == 0]
    fine = microroute.plan_local(a, b, 0.2, NC, segs, [], RT, margin=0.4)
    assert fine is not None
    for p, q in zip(fine['pts'], fine['pts'][1:]):
        assert geomir.verify_polyline([p, q], 0.2, NC, segs, [], RT)[0]
    # whatever geomir could or could not do, the fine path is the short one
    assert sum(geomir.math.dist(p, q) for p, q in zip(fine['pts'], fine['pts'][1:])) < 2.6 or not same


def test_grid_never_trusts_itself_over_the_verifier():
    # a step far too coarse for the corridor: no cell centre falls in the
    # band, so there is no path to be had (not a wrong one)
    segs = [_via(0.0, 0.0), _via(1.2668, 0.0)]
    got = microroute.plan_local((0.33, 1.2), (0.5, -1.2), 0.2, NC, segs, [], RT,
                                step=0.25, margin=0.3)
    assert got is None or all(
        geomir.verify_polyline([p, q], 0.2, NC, segs, [], RT)[0]
        for p, q in zip(got['pts'], got['pts'][1:]))
