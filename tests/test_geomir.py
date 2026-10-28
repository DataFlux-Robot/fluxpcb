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


class TestT1ViaBarrel:
    """CyrilKafle methodology: each check proven on a synthetic bad
    case AND silent on a clean one. The 10 hop-repair shorts = via
    barrels checked only on the path's layer."""

    def test_via_blocked_by_other_layer_copper(self):
        # a foreign track on layer 1 near the via site -> barrel shorts
        segs = [[], [(2, (5.1, 0.0), (5.1, 1.0), 0.1)]]
        rects = [[], []]
        ok, bl = geomir.via_ok(5.0, 0.5, 1, segs, rects)
        assert not ok and bl["type"] == "via-vs-track"

    def test_via_clean(self):
        segs = [[], [(2, (8.0, 0.0), (8.0, 1.0), 0.1)]]
        ok, bl = geomir.via_ok(5.0, 0.5, 1, segs, [[], []])
        assert ok and bl is None

    def test_via_blocked_by_pad(self):
        rects = [[], [(2, (5.2, 0.5, 0.3, 0.3, 0.0))]]
        ok, bl = geomir.via_ok(5.0, 0.5, 1, [[], []], rects)
        assert not ok and bl["type"] == "via-vs-pad"

    def test_hop_repair_requires_via_ok(self):
        src = (KGEN / "geomir.py").read_text(encoding="utf-8")
        body = src.split("def _plan_paths_at_width")[1]
        assert "via_ok(p[0], p[1]" in body and "via_ok(q[0], q[1]" in body


class TestT4StrictGate:
    def test_strict_gate_in_loop(self):
        src = (REPO / "fluxpcb" / "routing.py").read_text(encoding="utf-8")
        body = src.split("def _closed_loop")[1].split("\ndef ")[0]
        assert "strict_rollback" in body
        assert "_qs_before" in body and "_qs_after" in body
        # restore-on-regression present
        assert "_sh4.copy2(_pre_snap, pcb_path)" in body

    def test_delta_shorts_recorded(self):
        src = (REPO / "fluxpcb" / "routing.py").read_text(encoding="utf-8")
        assert 'outcome["delta_shorts"]' in src


class TestT2RuleTable:
    """Corpus cures: parse status (kipilot), load probe (ByTaymur),
    write-then-readback (DaCapo7)."""

    def test_load_reads_pro(self, tmp_path):
        import json as _j
        from fluxpcb.kernel import rules
        pro = tmp_path / "b.kicad_pro"
        pro.write_text(_j.dumps({
            "board": {"design_settings": {"rules": {"min_clearance": 0.2}}},
            "net_settings": {"classes": [{"name": "Default",
                                          "clearance": 0.2,
                                          "track_width": 0.25,
                                          "via_diameter": 0.6,
                                          "via_drill": 0.3,
                                          "smd_to_smd_clearance": 0.05}]},
        }), encoding="utf-8")
        r = rules.load_rules(tmp_path / "b.kicad_pcb")
        assert r["clearance"] == 0.2
        assert r["clearance_smd"] == 0.05
        assert r["via_dia"] == 0.6
        assert r["parse"]["source"]["clearance"].startswith("kicad_pro")

    def test_probe_flags_fallback(self, tmp_path):
        from fluxpcb.kernel import rules
        r = rules.load_rules(tmp_path / "none.kicad_pcb")
        bad = rules.verify_loaded(r)
        assert bad, "fallback-sourced rules must surface in the probe"

    def test_readback_detects_mismatch(self, tmp_path):
        import json as _j
        from fluxpcb.kernel import rules
        pro = tmp_path / "b.kicad_pro"
        pro.write_text(_j.dumps({"a": {"b": 1}}), encoding="utf-8")
        assert rules.readback(pro, {"a.b": 1}) == []
        assert rules.readback(pro, {"a.b": 2})

    def test_geomir_verifier_takes_rules(self):
        src = (KGEN / "geomir.py").read_text(encoding="utf-8")
        assert "rules=None" in src and "_kr.load_rules" in src


class TestPlaceOpt:
    """Stream ①: margin-aware moves (DaCapo7 check-space-first) +
    attribution bias (elcruzo GNN-lite)."""

    def _mk_board(self, tmp_path):
        """Synthetic board: 3 fp clusters — A free zone, B crowded."""
        import subprocess
        code = (
            "import pcbnew\n"
            "b = pcbnew.BOARD()\n"
            "import sys\n"
            "for ref, x, y, w, h in [('R1', 10, 10, 2, 2), "
            "('R2', 20, 10, 2, 2), ('R3', 20.6, 10, 2, 2), "
            "('C1', 30, 10, 2, 2)]:\n"
            "    fp = pcbnew.FOOTPRINT(b)\n"
            "    fp.SetReference(ref)\n"
            "    fp.SetPosition(pcbnew.VECTOR2I(int(x*1e6), int(y*1e6)))\n"
            "    fp.SetValue(ref)\n"
            "    b.Add(fp)\n"
            "edge = pcbnew.PCB_SHAPE(b)\n"
            "edge.SetShape(pcbnew.SHAPE_T_RECT)\n"
            "edge.SetStart(pcbnew.VECTOR2I(0, 0))\n"
            "edge.SetEnd(pcbnew.VECTOR2I(50000000, 20000000))\n"
            "edge.SetLayer(pcbnew.Edge_Cuts)\n"
            "b.Add(edge)\n"
            f"pcbnew.SaveBoard(r'{tmp_path}/t.kicad_pcb', b)\n"
            "print('OK')\n")
        p = subprocess.run([sys.executable, "-c", code],
                           capture_output=True, text=True, timeout=60)
        assert "OK" in p.stdout, p.stderr[-200:]
        return tmp_path / "t.kicad_pcb"

    def test_margin_rejects_overlap(self, tmp_path):
        import subprocess
        pcb = self._mk_board(tmp_path)
        code = (
            "import sys, json\n"
            "sys.path.insert(0, r'"
            + str(KGEN).replace("\\", "/") + "')\n"
            "import pcbnew, place_opt\n"
            f"b = pcbnew.LoadBoard(r'{str(pcb).replace(chr(92), '/')}')\n"
            "print(json.dumps({'r': place_opt.margin_ok(b, 'R3', -0.4, 0)}))\n")
        p = subprocess.run([sys.executable, "-c", code],
                           capture_output=True, text=True, timeout=60,
                           encoding="utf-8", errors="replace")
        d = json.loads([l for l in p.stdout.splitlines()
                        if l.strip().startswith("{")][-1])
        assert not d["r"]["ok"], d
        assert d["r"]["reason"] in ("footprint-overlap", "board-edge")

    def test_margin_accepts_free(self, tmp_path):
        import subprocess
        pcb = self._mk_board(tmp_path)
        code = (
            "import sys, json\n"
            "sys.path.insert(0, r'"
            + str(KGEN).replace("\\", "/") + "')\n"
            "import pcbnew, place_opt\n"
            f"b = pcbnew.LoadBoard(r'{str(pcb).replace(chr(92), '/')}')\n"
            "print(json.dumps({'r': place_opt.margin_ok(b, 'C1', -5, 0)}))\n")
        p = subprocess.run([sys.executable, "-c", code],
                           capture_output=True, text=True, timeout=60,
                           encoding="utf-8", errors="replace")
        d = json.loads([l for l in p.stdout.splitlines()
                        if l.strip().startswith("{")][-1])
        assert d["r"]["ok"], d

    def test_wiring(self):
        from fluxpcb.kernel.decision import ACTION_CONTRACT
        assert "optimize_placement" in ACTION_CONTRACT
        src = (REPO / "fluxpcb" / "kernel" / "intel.py").read_text(
            encoding="utf-8")
        assert "optimize_placement" in src
        rsrc = (REPO / "fluxpcb" / "routing.py").read_text(encoding="utf-8")
        assert "place_opt.py" in rsrc
