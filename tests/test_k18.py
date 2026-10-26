# -*- coding: utf-8 -*-
"""K18/R-round tests: diff pairs, DSN class rules, preroute whitelist,
handoff list, placement-margin driver rule."""
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from fluxpcb.routing_classes import classify_all, detect_pairs, classify_net


class TestR1DiffPairs:
    def test_can_pairs_now_critical(self):
        """fc shipped CAN1_H/L as 'normal' — CAN\d*_[HL] + pairing fix."""
        cls = classify_all(["CAN1_H", "CAN1_L", "CAN2_H", "CAN2_L"])
        assert all(cls[n] == "critical" for n in cls)

    def test_clock_suffix(self):
        assert classify_net("SDMMC1_CK") == "critical"

    def test_pair_detection(self):
        pairs = detect_pairs(["CAN1_H", "CAN1_L", "USB_DP", "USB_DM",
                              "PWM_CH2", "3V3"])
        assert ["CAN1_H", "CAN1_L"] in pairs
        assert ["USB_DP", "USB_DM"] in pairs
        assert len(pairs) == 2

    def test_pairs_written_to_cls_file(self, tmp_path, monkeypatch):
        # classify_all upgrades pair members even when regex misses both
        cls = classify_all(["LINDATA+", "LINDATA-"])
        assert cls["LINDATA+"] == "critical"
        assert ["LINDATA+", "LINDATA-"] in detect_pairs(
            ["LINDATA+", "LINDATA-"])

    def test_spec_has_diff_interfaces(self):
        import yaml
        spec = yaml.safe_load(
            (REPO / "projects" / "h743-fc" / "spec.yaml").read_text(
                encoding="utf-8"))
        diffs = [i for i in spec.get("interfaces", [])
                 if i.get("differential")]
        names = {i["name"] for i in diffs}
        assert {"usb", "can1", "can2"} <= names


class TestR2ClassRules:
    def _inject(self, tmp_path):
        from fluxpcb.routing import dsn_inject_class_rules
        p = tmp_path / "t.dsn"
        p.write_text(
            "(pcb\n  (network\n    (net A (pins u1-1))\n"
            "    (net B (pins u1-2))\n    (net C (pins u1-3))\n"
            "    (net D (pins u1-4))\n"
            "    (class kicad_default A B C D\n"
            "      (rule (width 200) (clearance 150))\n    )\n  )\n"
            "  (wiring\n    (wire x A)\n  )\n)\n", encoding="utf-8")
        n = dsn_inject_class_rules(
            p, {"A": "critical", "B": "power", "C": "normal",
                "D": "normal"},
            [["A", "C"]])
        return n, p.read_text(encoding="utf-8")

    def test_classes_inside_network(self, tmp_path):
        n, txt = self._inject(tmp_path)
        assert n >= 1
        i_pair, i_wir = txt.find('(class "PAIR_0"'), txt.find("(wiring")
        assert 0 < i_pair < i_wir

    def test_claimed_nets_leave_default(self, tmp_path):
        # pair members and classed nets leave kicad_default; plain
        # normal nets (D) keep their default membership
        _, txt = self._inject(tmp_path)
        import re
        blk = re.search(r"\(class\s+kicad_default[\s\S]*?\n\s*\)", txt)
        head = blk.group(0).split("(rule")[0]
        assert blk
        assert " A" not in head and " C" not in head
        assert "D" in head

    def test_pair_coupling_gap(self, tmp_path):
        _, txt = self._inject(tmp_path)
        assert '(class_class (classes "PAIR_0" "PAIR_0")' in txt
        assert "(clearance 250)" in txt

    def test_no_specctra_type_qualifiers(self, tmp_path):
        _, txt = self._inject(tmp_path)
        assert "smd_smd" not in txt


class TestR3PrerouteWhitelist:
    def test_session_whitelist_mode(self):
        src = (REPO / "fluxpcb" / "kicadgen" / "route_session.py").read_text(
            encoding="utf-8")
        assert "ONLY_NETS" in src and "sys.argv[4]" in src
        # fanout phases skipped in whitelist mode
        assert "if ONLY_NETS is None:" in src

    def test_v3_chain_has_preroute(self):
        src = (REPO / "fluxpcb" / "routing.py").read_text(encoding="utf-8")
        assert "preroute-critical" in src
        assert 'nets=_pre' in src

    def test_driver_menu_preroute(self):
        src = (REPO / "fluxpcb" / "routing_llm.py").read_text(
            encoding="utf-8")
        assert '"preroute_critical"' in src


class TestR4Handoff:
    def test_handoff_in_report(self):
        src = (REPO / "fluxpcb" / "routing.py").read_text(encoding="utf-8")
        assert '"handoff"' in src and "automation_pct" in src
        assert "jumpers_placed" in src
        assert "handoff-jumpers" in src


class TestR5PlacementMargin:
    def test_adjust_placement_action(self):
        src = (REPO / "fluxpcb" / "routing.py").read_text(encoding="utf-8")
        assert '"adjust_placement"' in src

    def test_fallback_hot_blocker_rule(self):
        from fluxpcb.routing_llm import _fallback_decision
        tel = {"opens": 12, "shorts": 0, "fragmented": {},
               "diag": [{"net": "N1", "blockers": ["U9", "U2"]},
                        {"net": "N2", "blockers": ["U9"]}]}
        hist = [{"action": "rerun_session", "delta_opens": 0},
                {"action": "rerun_session", "delta_opens": 1}]
        d = _fallback_decision(tel, hist)
        assert d["action"] == "adjust_placement"
        assert any(m["ref"] == "U9" for m in d["moves"])

    def test_escalate_when_no_hot_blocker(self):
        from fluxpcb.routing_llm import _fallback_decision
        d = _fallback_decision({"opens": 12, "shorts": 0, "fragmented": {},
                                "diag": []},
                               [{"delta_opens": 0}, {"delta_opens": 1}])
        assert d["action"] == "escalate"
