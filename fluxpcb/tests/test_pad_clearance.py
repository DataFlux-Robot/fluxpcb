"""Pad-local clearance in the one geometric verifier.

A pad may carry its own clearance override.  DRC judges against it; the
verifier that only knew the netclass value kept accepting positions DRC
rejects, so the nudge operator "verified" a fix the DRC then refused and
the whole repair batch was rolled back (0571: a 4.5 mm battery pad with
0.5 mm on it, a track 0.288 mm from its corner).
"""
import math
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
SQ2 = math.sqrt(2)


def _pad(x, y, hw, hh, local=None):
    rect = (x, y, hw, hh, 0.0, False) if local is None else (x, y, hw, hh, 0.0, False, local)
    return (2, rect)


def _line_near_corner(gap, width=0.25, corner=2.25):
    """A 45° track (x + y = c) whose EDGE is `gap` mm from the square
    pad's corner, long enough to pass it."""
    c = 2 * corner + SQ2 * (gap + width / 2)
    mid = c / 2
    return (mid - 0.6, mid + 0.6), (mid + 0.6, mid - 0.6)


def test_a_pad_override_is_the_rule_when_it_is_stricter():
    pad = _pad(0.0, 0.0, 2.25, 2.25, local=0.5)        # the 0571 battery pad
    a, b = _line_near_corner(0.288)                      # the 0571 gap
    ok, blk = geomir.verify_polyline([a, b], 0.25, NC, [], [pad], RT)
    assert not ok and blk['type'] == 'pad'
    assert abs(blk['short_by_mm'] - 0.212) < 0.003
    # the same geometry with no override is legal (0.288 > 0.2)
    plain = _pad(0.0, 0.0, 2.25, 2.25)
    assert geomir.verify_polyline([a, b], 0.25, NC, [], [plain], RT)[0]
    # 0.5 mm clear of the corner is legal again
    ok, _ = geomir.verify_polyline([*_line_near_corner(0.5)], 0.25, NC, [], [pad], RT)
    assert ok


def test_a_track_between_two_samples_is_judged_exactly():
    """The verifier used to sample the segment every 0.1 mm.  This track
    misses the need by 2 µm at a point no sample lands on (the nearest
    samples read 0.327 mm); it also reports the deepest point."""
    pad = _pad(0.0, 0.0, 1.0, 1.0)                        # corner at (1, 1)
    ok, blk = geomir.verify_polyline([(0.85, 1.323), (1.25, 1.323)],
                                     0.25, NC, [], [pad], RT)
    assert not ok and 0.002 - 1e-6 <= blk['short_by_mm'] <= 0.002 + 1e-6
    # and one that clears it by the same 2 µm is legal
    assert geomir.verify_polyline([(0.85, 1.327), (1.25, 1.327)],
                                  0.25, NC, [], [pad], RT)[0]


def test_segment_distance_rotated_round_and_inside():
    assert geomir.seg_pad_dist((0.0, 0.0), (0.05, 0.05),
                               (0.0, 0.0, 1.0, 1.0, 0.0, False)) == 0.0
    # a 2.0 x 0.5 pad turned 90 deg: its corner is at (0.25, 1.0)
    turned = (0.0, 0.0, 1.0, 0.25, math.pi / 2, False)
    assert abs(geomir.seg_pad_dist((0.5, 1.2), (1.5, 1.2), turned)
               - math.hypot(0.25, 0.2)) < 1e-9
    assert geomir.seg_pad_dist((0.0, 0.0), (1.2, 0.0), turned) == 0.0   # crosses
    circ = (0.0, 0.0, 0.5, 0.5, 0.0, True)
    assert abs(geomir.seg_pad_dist((0.0, 0.8), (3.0, 0.8), circ) - 0.3) < 1e-9


def test_an_override_below_the_rule_does_not_loosen_the_rule():
    # The verifier holds ONE track clearance for the whole board, so the
    # need is the stricter of the rule and the pad's own value.
    pad = _pad(0.0, 0.0, 1.0, 1.0, local=0.1)
    assert geomir.pad_need(pad[1], 0.2) == 0.2
    assert geomir.pad_need(pad[1], 0.05) == 0.1
    assert geomir.pad_need((0, 0, 1, 1, 0.0, False), 0.2) == 0.2       # no 7th field
    assert geomir.pad_need((0, 0, 1, 1, 0.0, False, None), 0.2) == 0.2


def test_via_legality_sees_the_override():
    pad = _pad(0.0, 0.0, 2.25, 2.25, local=0.5)
    # a via of radius 0.3 with its centre 0.7 mm outside the pad's corner:
    # 0.7 >= 0.2 + 0.3 (legal by the rule) but < 0.5 + 0.3 (not by the pad)
    k = 2.25 + 0.7 / SQ2
    ok, blk = geomir.via_ok(k, k, NC, [[]], [[pad]], via_r=0.3, rules=RT)
    assert not ok and blk['type'] == 'via-vs-pad'
    assert geomir.via_ok(k, k, NC, [[]], [[_pad(0.0, 0.0, 2.25, 2.25)]], via_r=0.3, rules=RT)[0]
    k2 = 2.25 + 0.9 / SQ2
    assert geomir.via_ok(k2, k2, NC, [[]], [[pad]], via_r=0.3, rules=RT)[0]


def test_the_fine_grid_respects_the_override():
    pad = _pad(0.0, 0.0, 1.0, 0.25, local=0.6)        # 2.0 x 0.5 mm, 0.6 mm on it
    a, b = (-2.3, 0.0), (2.3, 0.0)                     # anchors clear of the override zone
    got = microroute.plan_local(a, b, 0.2, NC, [], [pad], RT, margin=1.3)
    assert got is not None
    for p, q in zip(got['pts'], got['pts'][1:]):
        assert geomir.verify_polyline([p, q], 0.2, NC, [], [pad], RT)[0]
    assert max(abs(p[1]) for p in got['pts']) >= 0.25 + 0.6 + 0.1 - 1e-6
    # without the override the detour is 0.4 mm closer
    got2 = microroute.plan_local(a, b, 0.2, NC, [], [_pad(0.0, 0.0, 1.0, 0.25)], RT, margin=1.3)
    assert got2 is not None
    assert max(abs(p[1]) for p in got2['pts']) <= max(abs(p[1]) for p in got['pts']) - 0.35


def test_an_override_pad_just_outside_the_window_is_still_an_obstacle():
    """The window keeps only copper that reaches it.  By the rule this pad
    is 0.4 mm outside it (not kept); by its own override it reaches a
    further 1.0 mm in and closes the only corridor."""
    pad = _pad(2.7, 0.0, 0.3, 1.0, local=1.2)         # edge at x = 2.4
    a, b = (1.6, -2.2), (1.6, 2.2)
    assert microroute.plan_local(a, b, 0.2, NC, [], [pad], RT, margin=0.4) is None
    plain = _pad(2.7, 0.0, 0.3, 1.0)
    got = microroute.plan_local(a, b, 0.2, NC, [], [plain], RT, margin=0.4)
    assert got is not None and len(got['pts']) == 2      # straight through


def test_whynot_profile_names_the_pad_and_its_need():
    from fluxpcb.kicadgen import whynot
    pad = _pad(0.0, 0.0, 2.25, 2.25, local=0.5)
    a, b = _line_near_corner(0.288)
    prof = whynot.seg_clearance_profile(a, b, 0.25, NC, [], [pad], 0.2, pad_clearance=0.2)
    assert not prof['feasible']
    blk = prof['blocker']
    assert blk['kind'] == 'pad'
    assert abs(blk['needed_mm'] - 0.625) < 0.003        # 0.5 + half the width
    assert abs(blk['move_by_mm'] - 0.212) < 0.003
