# -*- coding: utf-8 -*-
"""K14 fluxrouter tests: shared grid core + PathFinder core contracts."""
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


class TestRouteGrid:
    def test_module_exists_and_core_api(self):
        src = (REPO / "fluxpcb" / "kicadgen" / "routegrid.py").read_text(
            encoding="utf-8")
        for token in ("class GridModel", "def fixed_free", "def via_cell_ok",
                      "def clear_exact", "def via_exact_ok",
                      "def pair_connected", "def add_track", "def add_via",
                      "overuse_cells", "route_occupy"):
            assert token in src, token

    def test_honest_margins_baked(self):
        src = (REPO / "fluxpcb" / "kicadgen" / "routegrid.py").read_text(
            encoding="utf-8")
        # K7 lesson: extent + clearance + track half
        assert "ext + 0.30" in src
        # K12: dog-bone lattice via radii
        assert "VIA_ANNULUS + 0.25 + ext" in src


class TestFluxRouter:
    def test_pathfinder_core(self):
        src = (REPO / "fluxpcb" / "kicadgen" / "fluxrouter.py").read_text(
            encoding="utf-8")
        # negotiated cost: (1 + h) * (1 + pfac * foreign)
        assert "cost_l[i2] * (PFM" in src and "PFAC" in src
        # history feedback on contested cells
        assert "H_INC" in src
        # violation feedback: route AROUND physical fails
        assert "5.0" in src
        # orphaned partial-item cleanup on violation
        assert "board.Remove(i)" in src
        # island verification per chain (the honest gate)
        assert "pair_connected" in src
        # pad-anchored goals
        assert "anchor_pad" in src
        # partial rip-up (Zha & Li)
        assert "conflicted" in src and "PARTIAL RIP-UP" in src
        # adaptive step (MegaRoute)
        assert "moves + ((2, 0), (0, 2))" in src

    def test_json_contract_superset(self):
        src = (REPO / "fluxpcb" / "kicadgen" / "fluxrouter.py").read_text(
            encoding="utf-8")
        for field in ('"router": "pathfinder"', '"final"', '"time_s"'):
            assert field in src

    def test_wired_into_auto_route(self):
        src = (REPO / "fluxpcb" / "routing.py").read_text(encoding="utf-8")
        assert "_run_fluxrouter" in src
        assert 'FLUXNT_ROUTER") == "pathfinder"' in src


class TestBaseline:
    def test_baseline_recorded(self):
        import json
        d = json.loads((REPO / "benchmarks" / "baseline_20261024.json")
                       .read_text(encoding="utf-8"))
        assert d["boards"]["h743-fc"]["opens"] == 49
        assert d["boards"]["esp32s31-dev"]["opens"] == 1
