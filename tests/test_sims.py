"""Unit tests for the simulation suite (dcsolve / cavity / pdn_ac /
returnpath / em_adapters) — the seven-domain physics layer."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
MINI = REPO / "projects" / "esp32s31-mini"


# ---------------------------------------------------------------- dcsolve
class TestDCSolve:
    def test_parse_copper_counts(self):
        from fluxpcb.tools.dcsolve import parse_copper
        items = parse_copper(MINI / "pcb" / "board" / "esp32s31-mini.kicad_pcb")
        kinds = {}
        for it in items:
            kinds[it[0]] = kinds.get(it[0], 0) + 1
        assert kinds.get("pad", 0) == 84
        assert kinds.get("seg", 0) >= 10
        assert kinds.get("via", 0) >= 1
        # every item carries a net name
        assert all(it[5] is not None for it in items)

    def test_solve_rail_metrics(self):
        from fluxpcb.tools.dcsolve import solve_rail
        r = solve_rail(MINI / "pcb" / "board" / "esp32s31-mini.kicad_pcb",
                       "3V3", 3.3, i_total_a=0.5)
        assert r["status"] == "ok"
        assert 0.0 <= r["drop_pct"] < 3.0
        assert r["j_max_a_mm2"] > 0.0          # some current flows
        assert r["crowding_ok"] is True
        assert len(r["field_v"]) > 10          # downsampled field present

    def test_sheet_resistance_scale(self):
        import numpy as np
        from fluxpcb.tools.dcsolve import SIGMA_CU
        # 1oz foil sheet R = 1/(sigma*t) ~ 0.49 mOhm/square
        r_sheet = 1.0 / (SIGMA_CU * 35e-6)
        assert 4e-4 < r_sheet < 6e-4


# ---------------------------------------------------------------- cavity
class TestCavity:
    def test_first_mode_formula(self):
        import math
        from fluxpcb.tools.cavity import C0
        # 52x30mm, er=4.4: f10 = c/(2*sqrt(er)*L)
        L, W, er = 0.052, 0.030, 4.4
        f10 = C0 / (2 * math.sqrt(er)) * (1 / L)
        assert abs(f10 / 1e6 - 1374.2) < 5     # matches gate evidence
        f01 = C0 / (2 * math.sqrt(er)) * (1 / W)
        assert f01 > f10                        # shorter dimension, higher mode

    def test_run_writes_evidence(self, tmp_path):
        import yaml
        from fluxpcb.tools import cavity
        (tmp_path / "stackup.yaml").write_text(
            "layers: 2\ner: 4.4\n", encoding="utf-8")
        pcb_dir = tmp_path / "pcb" / "board"
        pcb_dir.mkdir(parents=True)
        shutil_copy = MINI / "pcb" / "board" / "esp32s31-mini.kicad_pcb"
        import shutil
        shutil.copy(shutil_copy, pcb_dir / "b.kicad_pcb")
        out = cavity.run(tmp_path, {})
        assert out["status"] == "ok"
        assert out["first_resonance_mhz"] > 1000
        assert (tmp_path / "sim" / "plane_cavity.json").exists()


# ---------------------------------------------------------------- returnpath
class TestReturnPath:
    def test_concentration_values(self):
        from fluxpcb.tools.returnpath import concentration_within
        assert abs(concentration_within(1) - 0.5) < 0.01   # +-1h: ~50%
        assert abs(concentration_within(3) - 0.795) < 0.01  # +-3h: ~79.5%
        assert concentration_within(10) > 0.93
        assert concentration_within(1000) > 0.999   # asymptotically 1

    def test_run_pass(self, tmp_path):
        from fluxpcb.tools import returnpath
        spec = {"interfaces": [{"name": "usb", "high_speed": True,
                                "h_mm": 0.2, "style": "microstrip"}]}
        out = returnpath.run(tmp_path, spec)
        assert out["pass"] is True
        assert out["worst_concentration_pct"] >= 70.0


# ---------------------------------------------------------------- pdn_ac
class TestPdnAC:
    def test_deck_structure(self):
        from fluxpcb.tools.pdn_ac import _deck
        caps = [{"c_f": 100, "esr": 0.05, "esl_nh": 1.0}] * 4
        deck = _deck(caps, 2.0, 36.5, 0.02, 20)
        assert "I1   0 out AC 1" in deck          # 1A injection
        assert deck.count("Rc") == 4              # series ESR per cap
        assert deck.count("Lc") == 4              # series ESL per cap
        assert "ac dec" in deck                   # AC sweep
        assert "100u" not in deck and "1.000000e-04" in deck  # 100uF in F

    def test_cap_units_farads(self):
        from fluxpcb.tools.pdn_ac import _deck
        deck = _deck([{"c_f": 100, "esr": 0.05, "esl_nh": 1.0}], 2.0, 36.5,
                     0.02, 20)
        # 100 (uF) must appear as 1e-4 farads, never 1e-10
        assert "1.000000e-04" in deck
        assert "1.000000e-10" not in deck


# ---------------------------------------------------------------- em adapters
class TestEmAdapters:
    def test_probe_shape(self):
        from fluxpcb.tools import em_adapters
        avail = em_adapters.probe()
        assert isinstance(avail, dict)
        assert "openems" in avail and "openfoam" in avail

    def test_generate_cases(self, tmp_path):
        from fluxpcb.tools import em_adapters
        cases = em_adapters.generate_cases(tmp_path)
        assert len(cases) == 4
        d = tmp_path / "sim" / "em_cases"
        for c in ("return_current.py", "resonance_sweep.py",
                  "farfield.py", "meep_farfield.py"):
            assert (d / c).exists()
            assert "import" in (d / c).read_text(encoding="utf-8")[:400]

    def test_thermal_case_skeleton(self, tmp_path):
        from fluxpcb.tools import em_adapters
        rel = em_adapters.generate_thermal_case(tmp_path)
        assert (tmp_path / rel / "README.md").exists()
        assert "chtMultiRegionFoam" in \
            (tmp_path / rel / "README.md").read_text(encoding="utf-8")
