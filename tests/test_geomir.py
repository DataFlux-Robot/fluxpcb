# -*- coding: utf-8 -*-
"""K23/M-round: the board map (geometry IR) — pure planning core,
map rendering, directed intervention contract."""
import json
import math
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
KGEN = REPO / "fluxpcb" / "kicadgen"
sys.path.insert(0, str(KGEN))
sys.path.insert(0, str(REPO))

import geomir                                    # noqa: E402


class TestCandidatePolylines:
    def test_shapes_and_endpoints(self):
        cands = geomir.candidate_polylines((0.0, 0.0), (10.0, 6.0))
        assert len(cands) >= 10
        for c in cands:
            assert tuple(c[0]) == (0.0, 0.0)
            assert tuple(c[-1]) == (10.0, 6.0)

    def test_l_and_z_and_staircase_present(self):
        cands = geomir.candidate_polylines((0, 0), (10, 6))
        l_shapes = [c for c in cands if len(c) == 3 and
                    (c[1][0] in (10, 0) or c[1][1] in (6, 0))]
        assert any(len(c) == 3 for c in cands)      # L or Z
        assert any(len(c) == 4 for c in cands)      # staircase


class TestVerifyPolyline:
    # geometry identical to commit_gate's rules (single truth via
    # routegrid's pure functions)

    def test_blocked_by_foreign_track(self):
        segs = [(2, (5, -3), (5, 3), 0.1)]          # wall at x=5
        ok, blocker = geomir.verify_polyline(
            [(0, 0), (10, 0)], 0.2, 1, segs, [])
        assert not ok
        assert blocker["type"] == "track" and blocker["net"] == 2
        assert blocker["short_by_mm"] > 0

    def test_clean_path_passes(self):
        segs = [(2, (5, 3), (5, 5), 0.1)]
        ok, blocker = geomir.verify_polyline(
            [(0, 0), (10, 0)], 0.2, 1, segs, [])
        assert ok and blocker is None

    def test_pad_obstacle(self):
        rects = [(2, (5.0, 0.0, 1.0, 1.0, 0.0))]
        ok, blocker = geomir.verify_polyline(
            [(0, 0), (10, 0)], 0.2, 1, [], rects)
        assert not ok and blocker["type"] == "pad"

    def test_own_net_ignored(self):
        segs = [(1, (5, -3), (5, 3), 0.1)]
        ok, _ = geomir.verify_polyline(
            [(0, 0), (10, 0)], 0.2, 1, segs, [])
        assert ok


class TestBestPaths:
    def test_verified_sorted_and_rejects_carry_blockers(self):
        # wall blocks the direct L's; an offset Z slips around it
        segs = [(2, (5, -3), (5, 3), 0.1)]
        ok, rej = geomir.best_paths((0, 0), (10, 0), 0.2, 1, segs, [])
        assert ok, "an offset candidate must find the gap"
        assert ok[0]["length_mm"] <= ok[-1]["length_mm"]
        for p in ok:
            assert p["pts"][0] == [0, 0] and p["pts"][-1] == [10, 0]
        assert rej and rej[0]["blocked_by"]["net"] == 2

    def test_hopeless_reports_blocker(self):
        # fully enclosed corridor
        segs = ([(2, (0, -3), (12, -3), 0.1),
                 (2, (0, 3), (12, 3), 0.1),
                 (2, (6, -3), (6, 3), 0.1)])
        ok, rej = geomir.best_paths((1, 0), (10, 0), 0.2, 1, segs, [])
        # offset paths must hop the wall x=6 within y∈(-3,3): all
        # candidates crossing x=6 are blocked -> honest rejects
        assert not ok
        assert rej and "short_by_mm" in rej[0]["blocked_by"]


class TestSummarizeAndRender:
    def _plan(self):
        ok, rej = geomir.best_paths((0, 0), (10, 0), 0.2, 1, [], [])
        islands = [{"pads": [[0, 0], [0, 1]], "anchor": [0, 0.5],
                    "bbox": [0, 0, 0, 1]},
                   {"pads": [[10, 0]], "anchor": [10, 0],
                    "bbox": [10, 0, 10, 0]}]
        summary = geomir.summarize("EN", islands, ok, rej)
        return {"net": "EN", "islands": islands, "paths": ok,
                "rejects": rej, "segs": [], "rects": [],
                "summary": summary}

    def test_summary_carries_coordinates(self):
        plan = self._plan()
        assert "EN" in plan["summary"]
        assert "岛1" in plan["summary"]
        assert "路径" in plan["summary"] or "被挡" in plan["summary"]

    def test_render_svg(self, tmp_path):
        plan = self._plan()
        out = geomir.render_plan(plan, tmp_path / "p.svg")
        svg = Path(out).read_text(encoding="utf-8")
        assert "<svg" in svg and plan["net"] in svg
        assert "polyline" in svg


class TestProtocol:
    def test_decision_contract_has_direct_link(self):
        from fluxpcb.kernel.decision import ACTION_CONTRACT, validate
        assert "direct_link" in ACTION_CONTRACT
        ok, fixit = validate({"action": "direct_link",
                              "params": {"net": "EN",
                                         "pts": [[0, 0, 0], [1, 0, 0]]}})
        assert ok
        ok2, fixit2 = validate({"action": "direct_link",
                                "params": {"net": "EN"}})
        assert not ok2 and "pts" in fixit2.get("message", "")

    def test_loop_has_direct_link_and_plans(self):
        src = (REPO / "fluxpcb" / "routing.py").read_text(encoding="utf-8")
        body = src.split("def _closed_loop")[1].split("\ndef ")[0]
        assert "geomir.py" in body and "plans" in body
        exec_body = src.split("def _loop_execute")[1].split("\ndef ")[0]
        assert "direct_link.py" in exec_body

    def test_jumper_flood_branch_reachable(self):
        """The q1/q2 NameError made the flood meeting-point dead code
        (midpoint guess forever). Anchors must precede the branch."""
        src = (KGEN / "route_jumper.py").read_text(encoding="utf-8")
        i_anchor = src.find("a1, a2 = anchor_of(isl_keep)")
        i_flood = src.find("c1 = _rd.anchor_cell(_dm, q1.x")
        i_mid = src.find("spot = find_spot((q1.x / mm + q2.x / mm) / 2")
        assert 0 < i_anchor < i_flood < i_mid

    def test_intel_plans_aware(self):
        src = (REPO / "fluxpcb" / "kernel" / "intel.py").read_text(
            encoding="utf-8")
        assert "plans: dict | None" in src
        assert '"direct_link"' in src
