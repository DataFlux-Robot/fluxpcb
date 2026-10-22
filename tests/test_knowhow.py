"""Tests for K1-K6 knowhow features (259-project mining round)."""
from __future__ import annotations

from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------- K1 classes
class TestRoutingClasses:
    def test_classification(self):
        from fluxpcb.routing_classes import classify_all
        cls = classify_all(["USB_DP", "3V3", "PWM_CH1", "SPI1_MOSI", "GND"])
        assert cls["USB_DP"] == "critical"
        assert cls["SPI1_MOSI"] == "critical"
        assert cls["3V3"] == "power"
        assert cls["GND"] == "power"
        assert cls["PWM_CH1"] == "normal"

    def test_spec_impedance_declares_critical(self):
        from fluxpcb.routing_classes import classify_net
        spec = {"interfaces": [{"name": "usb", "impedance_ohm": 90,
                                "connections": ["MYBUS_P", "MYBUS_N"]}]}
        assert classify_net("MYBUS_P", spec) == "critical"
        assert classify_net("MYBUS_P") == "normal"

    def test_routing_order_critical_first_power_last(self):
        from fluxpcb.routing_classes import classify_all, routing_order_pairs
        cls = classify_all(["3V3", "USB_DP", "LED"])
        pairs = ["3V3|1|1|0|2|2|0", "LED|3|3|0|4|4|0", "USB_DP|5|5|0|6|6|0"]
        ordered = routing_order_pairs(pairs, cls)
        assert ordered[0].startswith("USB_DP")
        assert ordered[-1].startswith("3V3")

    def test_widths(self):
        from fluxpcb.routing_classes import DEFAULT_WIDTH_MM, width_for
        cls = {"3V3": "power", "IO9": "normal", "USB_DP": "critical"}
        assert width_for("3V3", cls) == 0.4
        assert width_for("USB_DP", cls) == 0.15
        assert DEFAULT_WIDTH_MM["normal"] == 0.2

    def test_kicad_classes_structure(self):
        from fluxpcb.routing_classes import classify_all, kicad_net_classes
        cls = classify_all(["USB_DP", "3V3", "IO9"])
        kcs = kicad_net_classes(cls)
        names = [k["name"] for k in kcs]
        assert names == ["Power", "Critical", "Default"]
        power = kcs[0]
        assert power["track_width"] == 0.4
        assert "3V3" in power["netclass_patterns"]

    def test_apply_net_classes(self, tmp_path):
        import json
        from fluxpcb.routing_classes import apply_net_classes, classify_all
        pro = tmp_path / "b.kicad_pro"
        pro.write_text(json.dumps(
            {"board": {"design_settings": {}}}), encoding="utf-8")
        cls = classify_all(["USB_DP", "3V3"])
        assert apply_net_classes(pro, cls)
        data = json.loads(pro.read_text(encoding="utf-8"))
        assert len(data["net_settings"]["classes"]) == 3


# ---------------------------------------------------------------- K2 power
class TestPowerLayout:
    def test_ldo_cluster_offsets(self):
        from fluxpcb.kicadgen.power_layout import ldo_cluster
        c = ldo_cluster((10, 10))
        assert c["C_in"][0] < 10 < c["C_out"][0]      # flanking

    def test_loop_area(self):
        from fluxpcb.kicadgen.power_layout import loop_area_mm2, loop_ok
        a = (10, 10)
        assert loop_area_mm2((8, 10), a, (12, 10)) == 0.0
        assert not loop_ok((0, 0), (50, 0), (0, 50), budget_mm2=10)


# ---------------------------------------------------------------- K3 SA
class TestSARefine:
    def test_refine_never_worsens(self):
        from fluxpcb.kicadgen.px4fmu import W, H, footprints, placement
        from fluxpcb.placement import cost, refine
        fps = footprints()
        new, c0, c1 = refine(placement(), fps, W, H, iterations=300)
        assert c1 <= c0
        assert set(new) == set(placement())

    def test_refine_deterministic(self):
        from fluxpcb.kicadgen.px4fmu import W, H, footprints, placement
        from fluxpcb.placement import refine
        fps = footprints()
        _, _, a = refine(placement(), fps, W, H, iterations=100, seed=7)
        _, _, b = refine(placement(), fps, W, H, iterations=100, seed=7)
        assert a == b


# ---------------------------------------------------------------- K4 quality
class TestRoutingQuality:
    def test_score_clean_board(self, tmp_path):
        from fluxpcb.routing_quality import score_board
        pcb = tmp_path / "b.kicad_pcb"
        pcb.write_text('(segment (start 1 1) (end 2 2) (width 0.4)\n'
                       '(layer "F.Cu") (net "3V3"))\n', encoding="utf-8")
        s = score_board(pcb, {"unconnected": 0, "shorting": 0})
        assert s["score"] > 80
        assert s["net_class_counts"]["power"] >= 1

    def test_score_shorts_penalized(self, tmp_path):
        from fluxpcb.routing_quality import score_board
        pcb = tmp_path / "b.kicad_pcb"
        pcb.write_text("", encoding="utf-8")
        s = score_board(pcb, {"unconnected": 10, "shorting": 3})
        assert s["score"] <= 45
        assert any("短路" in f["issue"] for f in s["findings"])

    def test_structured_feedback(self):
        from fluxpcb.routing_quality import structured_feedback
        txt = ("[unconnected_items]: x\n desc\n  @(1.0 mm, 2.0 mm): Pad [USB_DP] of U1\n"
               "[shorting_items]: y\n desc\n  @(1 mm, 1 mm): A [3V3]\n  @(2 mm, 2 mm): B [GND]")
        fb = structured_feedback(txt, {"USB_DP": "critical"})
        assert any("critical" in x for x in fb)
        assert any("short" in x for x in fb)


# ---------------------------------------------------------------- K5 ses
class TestSesParser:
    def test_parse_and_inject(self, tmp_path):
        from fluxpcb.ses2kicad import inject_ses, parse_ses
        ses = tmp_path / "r.ses"
        ses.write_text(
            "(network_out\n"
            " (net USB_DP\n"
            "  (wire (path (layer top_copper) (100000 100000) (200000 100000))))\n"
            "  (via (150000 100000)))\n", encoding="utf-8")
        d = parse_ses(ses)
        assert "USB_DP" in d["nets"]
        assert len(d["vias"]) == 1
        pcb = tmp_path / "b.kicad_pcb"
        pcb.write_text("(kicad_pcb ...stuff... )", encoding="utf-8")
        stats = inject_ses(ses, pcb)
        assert stats["segments"] >= 1
        out = pcb.read_text(encoding="utf-8")
        assert '(net "USB_DP")' in out


# ---------------------------------------------------------------- K6 prompts
class TestPrompts:
    def test_prompt_files_exist_and_versioned(self):
        for name in ("requirements.md", "placement.md", "route_repair.md"):
            f = REPO / "prompts" / name
            assert f.exists(), name
            txt = f.read_text(encoding="utf-8")
            assert "禁止" in txt or "禁止项" in txt
            assert len(txt) > 200

    def test_requirements_has_fewshot_and_schema(self):
        txt = (REPO / "prompts" / "requirements.md").read_text(encoding="utf-8")
        assert "covered_by" in txt          # schema element
        assert "few-shot" in txt.lower() or "Few-shot" in txt
        assert "unsupported" in txt         # honesty boundary

    def test_placement_forbids_coordinates(self):
        txt = (REPO / "prompts" / "placement.md").read_text(encoding="utf-8")
        assert "不是坐标" in txt
        assert "px4-fmu" in txt             # board-family rules

    def test_route_repair_whitelist_only(self):
        txt = (REPO / "prompts" / "route_repair.md").read_text(encoding="utf-8")
        assert "component_nudges" in txt
        assert "走线坐标" in txt             # forbids coordinate tracks
