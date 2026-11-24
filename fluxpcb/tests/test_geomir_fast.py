"""The exact verifier with its box prefilter: same verdicts, fewer measures.

verify_polyline and via_ok skip any track whose box is farther than the
need.  That is sound (a box is never farther than the segment in it);
what must not change is WHICH item is named and by how much.  The
reference here is the verifier without the prefilter: every pair
measured, in list order, first violation reported.
"""
import math
import os
import random
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from fluxpcb.kicadgen import geomir  # noqa: E402
from fluxpcb.kicadgen import routegrid as rg  # noqa: E402

CLEAR, SMD = 0.2, 0.2
RT = {'clearance': CLEAR, 'clearance_smd': SMD}


def _reference(pts, width, nc, segs, rects):
    own = width / 2
    for (p, q) in zip(pts, pts[1:]):
        for (net, s, e, hw) in segs:
            if net == nc:
                continue
            d = rg.seg_seg_dist(p, q, s, e)
            if d < CLEAR + hw + own:
                return False, ('track', net, round(CLEAR + hw + own - d, 3))
        for (net, rect) in rects:
            if net == nc:
                continue
            need = geomir.pad_need(rect, SMD) + own
            n = 2000                       # dense: the sample min is within
            best = float('inf')            # 1 µm of the true distance here
            for k in range(n + 1):
                t = k / n
                x = p[0] + (q[0] - p[0]) * t
                y = p[1] + (q[1] - p[1]) * t
                best = min(best, geomir.pt_pad_dist(x, y, rect))
            if best < need:
                return False, ('pad', net, round(need - best, 3))
    return True, None


def _scene(rng, n_segs=70, n_rects=18):
    segs, rects = [], []
    for _ in range(n_segs):
        x, y = rng.uniform(-2, 12), rng.uniform(-2, 12)
        segs.append((rng.choice([1, 2, 4, 9]),
                     (x, y), (x + rng.uniform(-3, 3), y + rng.uniform(-3, 3)),
                     rng.choice([0.1, 0.125, 0.15, 0.25])))
    for _ in range(n_rects):
        rects.append((rng.choice([1, 2, 4, 9]),
                      (rng.uniform(0, 10), rng.uniform(0, 8),
                       rng.uniform(0.15, 0.7), rng.uniform(0.15, 0.7),
                       rng.choice([0.0, 0.3, 1.1]), rng.choice([False, True]),
                       rng.choice([0.0, 0.0, 0.5]))))
    return segs, rects


def _as_reported(ok, blk):
    if ok:
        return None
    return (blk['type'], blk['net'], blk['short_by_mm'])


def test_the_prefilter_names_the_same_item_by_the_same_amount():
    rng = random.Random(5)
    segs, rects = _scene(rng)
    checked = rejects = 0
    for _ in range(1500):
        pts = [(rng.uniform(-2, 12), rng.uniform(-2, 12)) for _ in range(rng.randint(2, 4))]
        nc, width = rng.choice([1, 2, 4, 9, 30]), rng.choice([0.15, 0.2, 0.254, 0.4])
        got = geomir.verify_polyline(pts, width, nc, segs, rects, RT)
        want = _reference(pts, width, nc, segs, rects)
        checked += 1
        rejects += not want[0]
        if want[0]:
            assert got[0], (pts, width, nc)
            continue
        kind, net, short = want[1]
        assert not got[0] and got[1]['type'] == kind and got[1]['net'] == net, (pts, got, want)
        # a pad's reference depth comes from samples: within a micron
        assert abs(got[1]['short_by_mm'] - short) <= (0.001 if kind == 'pad' else 1e-9)
    assert rejects > 300                 # the scene does exercise both paths


def test_via_legality_is_the_same_verdict():
    rng = random.Random(9)
    segs, rects = _scene(rng)
    layers = ([segs[:40], segs[40:]], [rects[:10], rects[10:]])
    judged = 0
    for _ in range(1500):
        x, y = rng.uniform(-2, 12), rng.uniform(-2, 12)
        nc, via_r = rng.choice([1, 2, 4, 9, 30]), rng.choice([0.25, 0.3, 0.45])
        ok, blk = geomir.via_ok(x, y, nc, layers[0], layers[1], via_r=via_r, rules=RT)
        want = None
        for li in range(2):
            for (net, s, e, hw) in layers[0][li]:
                if net != nc and rg.pt_seg_dist(x, y, s[0], s[1], e[0], e[1]) < CLEAR + hw + via_r:
                    want = ('via-vs-track', net)
                    break
            if want:
                break
            for (net, rect) in layers[1][li]:
                if net != nc and geomir.pt_pad_dist(x, y, rect) < max(CLEAR, geomir.pad_need(rect, 0.0)) + via_r:
                    want = ('via-vs-pad', net)
                    break
            if want:
                break
        judged += want is not None
        if want is None:
            assert ok, (x, y, nc, via_r)
        else:
            assert not ok and (blk['type'], blk['net']) == want, (x, y, nc, via_r, blk, want)
    assert judged > 300


def _same_verdict(a, b):
    return a[0] == b[0] and a[1] == b[1]


def test_the_index_hands_back_what_the_full_scan_judges():
    """verify_polyline and via_ok with a CopperIndex against the same
    calls without one, under three rule tables (a pad is filed as far as
    ITS rules ask, so the strict table is the one to watch)."""
    tables = [{'clearance': 0.2, 'clearance_smd': 0.2},
              {'clearance': 0.254, 'clearance_smd': 0.0375},
              {'clearance': 0.15, 'clearance_smd': 0.15}]
    for rules in tables:
        rng = random.Random(len(rules['clearance_smd'].__str__()) + 3)
        segs, rects = _scene(rng, n_segs=400, n_rects=90)
        layers_s, layers_r = [segs[:250], segs[250:]], [rects[:55], rects[55:]]
        index = geomir.CopperIndex(layers_s, layers_r, rules)
        for _ in range(3000):
            pts = [(rng.uniform(-2, 12), rng.uniform(-2, 12)) for _ in range(rng.randint(2, 4))]
            li = rng.randrange(2)
            nc, width = rng.choice([1, 2, 4, 9, 30]), rng.choice([0.15, 0.254, 0.4])
            full = geomir.verify_polyline(pts, width, nc, layers_s[li], layers_r[li], rules)
            near = geomir.verify_polyline(pts, width, nc, layers_s[li], layers_r[li], rules,
                                          index, li)
            assert _same_verdict(full, near), (rules, pts, full, near)
            x, y = rng.uniform(-2, 12), rng.uniform(-2, 12)
            via_r = rng.choice([0.25, 0.3, 0.45])
            full = geomir.via_ok(x, y, nc, layers_s, layers_r, via_r=via_r, rules=rules)
            near = geomir.via_ok(x, y, nc, layers_s, layers_r, via_r=via_r, rules=rules,
                                 index=index)
            assert full == near, (rules, x, y, full, near)


def test_plan_paths_with_the_index_is_plan_paths_with_a_full_scan(monkeypatch):
    """The planner's whole output — what it verified, what it rejected
    and why — with the cell index against the same with an index that
    returns everything."""
    rng = random.Random(21)
    segs, rects = _scene(rng, n_segs=260, n_rects=60)
    layers_s, layers_r = [segs[:150], segs[150:]], [rects[:40], rects[40:]]

    class FullScan:
        """The index as "everything is near": the planner then measures
        every item, which is what it did before the index existed."""

        def __init__(self, s_, r_, rules=None):
            self.s, self.r = s_, r_

        def tracks(self, x0, y0, x1, y1, reach, li):
            return self.s[li]

        def pads(self, x0, y0, x1, y1, reach, li):
            return self.r[li]

    for (a, b) in (((0.5, 0.5), (9.0, 7.0)), ((1.0, 1.0), (1.4, 1.3)),
                   ((-1.0, 4.0), (11.0, 4.2)), ((3.0, 3.0), (3.0, 8.0))):
        for nc in (1, 2, 30):
            for rules in ({'clearance': 0.2, 'clearance_smd': 0.2}, None):
                monkeypatch.setattr(geomir, 'CopperIndex', geomir.CopperIndex)
                with_index = geomir.plan_paths(a, b, 0.2, nc, layers_s, layers_r,
                                               bounds=(0, 0, 10, 8),
                                               width_ladder=(0.2, 0.15), rules=rules)
                monkeypatch.setattr(geomir, 'CopperIndex', FullScan)
                full = geomir.plan_paths(a, b, 0.2, nc, layers_s, layers_r,
                                         bounds=(0, 0, 10, 8),
                                         width_ladder=(0.2, 0.15), rules=rules)
                assert with_index == full, (a, b, nc, rules)
