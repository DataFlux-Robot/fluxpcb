"""whynot: the numerical answers must be right on known geometry."""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from fluxpcb.kicadgen.whynot import (near_miss_profile,  # noqa: E402
                                     search_budget,
                                     seg_clearance_profile, via_profile,
                                     via_sites)


def _segs():
    # a foreign vertical track from (10,0)-(10,10), half-width 0.1
    return [(7, (10.0, 0.0), (10.0, 10.0), 0.1)]


def _rects():
    # a foreign pad centred at (0,0), half-extents 0.5 x 0.5
    return [(9, (0.0, 0.0, 0.5, 0.5, 0.0))]


def _near_track():
    # a foreign track (9.5,4)-(9.5,6), half-width 0.1: a horizontal path
    # ending at (9,5) has centerline distance 0.5 from it
    return [(7, (9.5, 4.0), (9.5, 6.0), 0.1)]


def test_segment_profile_against_track():
    # own track (0,5)-(9,5) width 0.2: centerline distance 0.5, needed =
    # 0.2 + 0.1 + 0.1 = 0.4 -> margin 0.1
    r = seg_clearance_profile((0.0, 5.0), (9.0, 5.0), 0.2, 5,
                              _near_track(), [], 0.2)
    assert r['feasible'] and r['margin_mm'] == 0.1
    assert r['blocker']['kind'] == 'track' and r['blocker']['net'] == 7
    # w_max: needed grows by 1 mm per 2 mm of width -> 0.2 + 0.2 = 0.4
    assert r['w_max_mm'] == 0.4


def test_segment_profile_near_miss_and_w_max():
    # width 0.25: needed = 0.2+0.1+0.125 = 0.425, margin = 0.075
    r = seg_clearance_profile((0.0, 5.0), (9.0, 5.0), 0.25, 5,
                              _near_track(), [], 0.2)
    assert r['margin_mm'] == 0.075 and r['w_max_mm'] == 0.4
    # width 0.45: needed = 0.525 -> miss by 0.025
    r2 = seg_clearance_profile((0.0, 5.0), (9.0, 5.0), 0.45, 5,
                               _near_track(), [], 0.2)
    assert not r2['feasible']
    assert r2['blocker']['short_by_mm'] == 0.025
    assert r2['blocker']['move_by_mm'] == 0.025


def test_segment_profile_against_pad_uses_pad_clearance():
    # own track from (1,0)-(4,0) width 0.2, pad edge at x=0.5 -> d=0.5
    # needed = pad_clearance(0.15) + 0.1 = 0.25 -> margin 0.25
    r = seg_clearance_profile((1.0, 0.0), (4.0, 0.0), 0.2, 5,
                              [], _rects(), 0.2, pad_clearance=0.15)
    assert r['margin_mm'] == 0.25 and r['blocker']['kind'] == 'pad'


def test_via_profile_binding_layer_and_geometry():
    # layer 0: free.  layer 1: a track passing within 0.2 of the site.
    segs = [[], [(7, (0.0, 0.2), (5.0, 0.2), 0.05)]]
    rects = [[], []]
    r = via_profile(0.0, 0.0, 5, segs, rects, via_r=0.25, clearance=0.2)
    # d=0.2, needed = 0.2 + 0.05 + 0.25 = 0.5 -> margin -0.3
    assert r['layer'] == 1 and r['margin_mm'] == -0.3
    assert not r['feasible']
    # clearing the track by 0.3 makes it legal exactly
    segs[1] = [(7, (0.0, 0.5), (5.0, 0.5), 0.05)]
    r2 = via_profile(0.0, 0.0, 5, segs, rects, via_r=0.25, clearance=0.2)
    assert r2['feasible'] and r2['margin_mm'] == 0.0


def test_via_sites_sealed_versus_open_anchor():
    # a foreign track THROUGH the anchor (0,-5)-(0,5), half-width 0.05.
    # needed = clearance + 0.05 + via_r; a site at distance |cx| from the
    # centerline fails when |cx| < needed.
    segs = [[(7, (0.0, -5.0), (0.0, 5.0), 0.05)] for _ in range(2)]
    rects = [[], []]
    r = via_sites(0.0, 0.0, 5, [0, 1], segs, rects,
                  radii=(0.3, 1.0), angles=12, via_r=0.25, clearance=0.2)
    # needed 0.5: the ring at r=1.0 angle 0 sits 1.0 away -> legal
    assert r['sealed'] is False
    assert any(l['legal'] >= 1 for l in r['layers'])
    # needed 1.05 > every ring distance <= 1.0 -> sealed everywhere
    r2 = via_sites(0.0, 0.0, 5, [0, 1], segs, rects,
                   radii=(0.3, 1.0), angles=12, via_r=0.5, clearance=0.5)
    assert r2['sealed'] is True
    assert all(l['legal'] == 0 for l in r2['layers'])


def test_near_miss_profile_distinguishes_wall_from_coarse_search():
    wall = near_miss_profile([{'step': s, 'margin_mm': -0.05} for s in
                              (0.02, 0.05, 0.1, 0.2, 0.3)])
    assert wall['verdict'] == 'walled' and wall['best_margin_mm'] == -0.05
    coarse = near_miss_profile([{'step': 0.02, 'margin_mm': -0.5},
                                {'step': 0.05, 'margin_mm': -0.3},
                                {'step': 0.1, 'margin_mm': -0.02}])
    assert coarse['verdict'] == 'search_too_coarse'
    assert coarse['improving'] and coarse['best_margin_mm'] == -0.02


def test_search_budget_verdicts():
    assert search_budget(120000, 120000, None)['verdict'] == 'budget_exhausted'
    assert search_budget(120000, 120000, [] )['exhausted'] is True
    assert search_budget(4000, 120000, None)['verdict'] == 'geometry'
    assert search_budget(4000, 120000, ['a'])['verdict'] == 'found'
