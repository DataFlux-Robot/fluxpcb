# -*- coding: utf-8 -*-
"""K13 corpus-backflow tests: diagnosis / one-liner / family gate / fab."""
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent


class TestRouteDiag:
    def test_diag_module_exists_and_contract(self):
        src = (REPO / "fluxpcb" / "kicadgen" / "route_diag.py").read_text(
            encoding="utf-8")
        for token in ("def flood", "def channel_width", "def open_pairs",
                      "BIDIRECTIONAL", "verdict"):
            assert token in src, token
        # verdicts: routable / tight / jumper (feeds the 0R closer)
        assert '"jumper"' in src


class TestJumper:
    def test_jumper_module_exists(self):
        src = (REPO / "fluxpcb" / "kicadgen" / "route_jumper.py").read_text(
            encoding="utf-8")
        assert "0R" in src and "split_as" in src
        # net split contract: NET -> NET#Jn (netlist gate merges via 0R)
        assert '"#J"' in src or "#J" in src


class TestOneLiner:
    def test_fc_full_8pwm(self):
        from fluxpcb.one_liner import synthesize
        r = synthesize("做一个带8路PWM的H743飞控，双CAN，TF卡，2路ADC")
        assert r["ok"] and r["family"] == "fc-full"
        assert r["slots"]["pwm"] == 8 and r["slots"]["adc"] == 2
        assert r["slots"]["tf"] is True

    def test_px4_n6(self):
        from fluxpcb.one_liner import synthesize
        r = synthesize("PX4飞控用N6芯片")
        assert r["ok"] and r["family"] == "px4-fmu"
        assert r["slots"]["mcu"] == "n6"

    def test_esp32(self):
        from fluxpcb.one_liner import synthesize
        r = synthesize("做一个ESP32开发板带WiFi")
        assert r["ok"] and r["family"] == "esp32s31-devboard"

    def test_ambiguous_asks(self):
        from fluxpcb.one_liner import synthesize
        r = synthesize("我要一块板子")
        assert not r["ok"] and r["clarification_needed"]

    def test_px4_without_mcu_asks(self):
        from fluxpcb.one_liner import synthesize
        r = synthesize("做个PX4飞控")
        assert not r["ok"]
        assert any("mcu" in q for q in r["clarification_needed"])


class TestFamilyGate:
    def test_unknown_family_fails(self, tmp_path):
        import sys
        sys.path.insert(0, str(REPO))
        from fluxpcb.gates import run_gate
        from fluxpcb.models import GateStatus
        spec = {"project": "x",
                "requirements": [{"id": "FR-001", "text": "t"}],
                "power": {"input": "USB-C 5V", "rails": ["3V3"]},
                "interfaces": [],
                "constraints": {"family": "bogus-family",
                                "size_mm": [50, 50], "layers": 6}}
        (tmp_path / "spec.yaml").write_text(yaml.safe_dump(spec),
                                            encoding="utf-8")
        res = run_gate("spec_schema", tmp_path, [])
        assert res.status == GateStatus.FAIL
        assert any("family" in f["issue"] for f in res.findings)


class TestFabExport:
    def test_export_fab_wired_into_engine(self):
        src = (REPO / "fluxpcb" / "engine.py").read_text(encoding="utf-8")
        assert "_export_fab" in src and "report.released" in src
        # gerbers + drill + positions all exported
        assert "gerbers" in src and "drill" in src and "positions.csv" in src


class TestCongestion:
    def test_congestion_module(self):
        src = (REPO / "fluxpcb" / "kicadgen" / "congestion.py").read_text(
            encoding="utf-8")
        assert "escape_ring" in src or "逃逸环" in src
        assert "RING_MARGIN" in src
