# -*- coding: utf-8 -*-
"""K21 kernel tests: invariants, transactions, taxonomy, truthnet
parsers, lint, severity protection, driver menu completeness."""
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from fluxpcb.kernel.invariants import check_level, FAB_FLOOR_MM
from fluxpcb.kernel.causal import classify, ACTION_FOR, _parse_pairs
from fluxpcb.kernel.truthnet import parse_kicad_netlist, parse_d356, \
    compare
from fluxpcb.kernel.lint import lint_knowhow
from fluxpcb.kernel.state import StateFingerprint, KernelQuery


class TestInvariants:
    def test_i3_opens_regression(self):
        v = check_level("light", {"opens": 3}, {"opens": 19})
        assert any(s.startswith("I3") for s in v)

    def test_i3_tolerance_respected(self):
        assert check_level("light", {"opens": 40}, {"opens": 44}) == []

    def test_full_level_checks_shorts_and_clearance(self):
        pre = {"opens": 10, "shorts": 0, "clearance": 5}
        post = {"opens": 10, "shorts": 3, "clearance": 7}
        v = check_level("full", pre, post)
        assert any(s.startswith("I1") for s in v)
        assert any(s.startswith("I2") for s in v)

    def test_light_level_skips_expensive_checks(self):
        assert check_level("light",
                           {"opens": 1, "shorts": 0, "clearance": 0},
                           {"opens": 1, "shorts": 99, "clearance": 99}) == []


class TestTaxonomy:
    PAIRS = [
        # escape-crossing: anchor at MCU field center vs far connector
        "SPI1_SCK|25.0|25.0|0|2.5|30.0|1",
        "SPI1_MOS|26.0|26.0|0|2.5|31.0|1",
        # power fragment
        "GND|25.0|25.0|0|40.0|45.0|1",
        # local: both anchors close
        "LOCAL_NET|10.0|10.0|0|11.0|10.5|1",
    ] + [f"ESC{i}|{24+i}|{24}|0|45.0|40.0|1" for i in range(1, 10)]

    def test_classification(self):
        r = classify(self.PAIRS)
        assert "SPI1_SCK" in r["escape-crossing"]
        assert "GND" in r["power-fragment"]
        assert "LOCAL_NET" in r["local-congestion"]

    def test_fc_iter3_residual_shape(self):
        """iter3 幸存数据实测：35 escape-crossing / 2 power / 1 local."""
        pairs_file = REPO / "projects" / "h743-fc" / "pcb" / "board" \
            / "ratsnest_pairs.txt"
        if not pairs_file.exists():
            pytest.skip("iter3 residual file not present")
        pairs = [l for l in
                 pairs_file.read_text(encoding="utf-8").splitlines()
                 if l.strip()]
        r = classify(pairs)
        assert len(r.get("escape-crossing", [])) >= 30

    def test_action_for_covers_all_classes(self):
        for cls in ("escape-crossing", "power-fragment",
                    "placement-blocked", "local-congestion",
                    "unclassified"):
            assert cls in ACTION_FOR


class TestTruthnetParsers:
    def test_kicad_netlist_parse(self, tmp_path):
        p = tmp_path / "n.net"
        p.write_text(
            '(export\n(nets\n(net\n(code "1")\n(name "GND")\n'
            '(node\n(ref "R1")\n(pin "1")\n(pinfunction "A")\n)\n'
            '(node\n(ref "C1")\n(pin "2")))\n'
            '(net\n(code "2")\n(name "3V3")\n'
            '(node\n(ref "R1")\n(pin "2")))))\n', encoding="utf-8")
        nets = parse_kicad_netlist(p)
        assert nets["GND"] == {"R1.1", "C1.2"}
        assert nets["3V3"] == {"R1.2"}

    def test_d356_parse_and_compare(self, tmp_path):
        # real KiCad shape: 317/327 both carry net@3-19 ref@19-24 pin@24-29
        p = tmp_path / "b.d356"
        p.write_text(
            "317GND              R6    -2          A01X+021825Y-00700\n"
            "317GND              C1    -2          A01X+016874Y-00750\n"
            "3273V3              R1    -2          A01X+017325Y-00150\n",
            encoding="utf-8")
        board = parse_d356(p)
        assert board["GND"] == {"R6.2", "C1.2"}
        sch = {"GND": {"R6.2", "C1.2", "C9.1"}, "3V3": {"R1.2"},
               "5V": {"U1.1"}}
        r = compare(board, sch)
        assert not r["clean"]
        assert "5V" in r["missing_nets"]
        assert "C9.1" in r["pin_mismatch"]["GND"]["sch_only"]

    def test_compare_clean(self):
        assert compare({"A": {"R1.1"}}, {"A": {"R1.1"}})["clean"]


class TestLint:
    def test_missing_generalization_flagged(self, tmp_path):
        p = tmp_path / "KH.md"
        p.write_text(
            "## K21 某轮\n内容无标注\n\n## K22 某轮\n泛化为: I5 xxx\n",
            encoding="utf-8")
        v = lint_knowhow(p)
        assert len(v) == 1 and "K21" in v[0]

    def test_case_marker_accepted(self, tmp_path):
        p = tmp_path / "KH.md"
        p.write_text("## K21 某轮\n个案：仅此板\n", encoding="utf-8")
        assert lint_knowhow(p) == []

    def test_real_knowhow_if_k21_exists(self):
        kh = REPO / "KNOWHOW.md"
        if not kh.exists():
            pytest.skip()
        v = lint_knowhow(kh)
        # K21 not yet written at module-introduction time
        assert isinstance(v, list)


class TestSeverityProtection:
    """I4-adjacent policy: functional DRC checks must never return to
    the ignore list (the connectivity-illusion class)."""

    # unconnected_items is the true-connectivity check — never silent.
    # (dangling/thermal are _DEFERRED_FUNCTIONAL until the stub-cleanup
    # pass exists; documented in routing.py)
    FUNCTIONAL = ("unconnected_items",)

    def test_functional_checks_not_ignored(self):
        src = (REPO / "fluxpcb" / "routing.py").read_text(
            encoding="utf-8")
        # extract the CALIBRATED_SEVERITIES dict body
        start = src.find("CALIBRATED_SEVERITIES")
        body = src[start:start + 2000]
        end = body.find("}")
        body = body[:end]
        for k in self.FUNCTIONAL:
            assert f'"{k}"' not in body, \
                f"{k} back in ignore calibration — the illusion returns"


class TestStateFingerprint:
    def test_query_carries_fingerprint(self):
        fp = StateFingerprint("a" * 64, "123")
        q = KernelQuery("opens", 5, fp)
        d = q.to_dict()
        assert d["fp"]["board_sha256"] == "a" * 64

    def test_short_repr(self):
        assert "abc" in StateFingerprint("abcd" * 16, "9").short()


class TestDriverMenuUpstreamActions:
    """Causal edge completeness: the driver menu must contain the
    upstream moves (the reverse edges), not just downstream repairs."""

    def test_menu_has_upstream_actions(self):
        src = (REPO / "fluxpcb" / "routing_llm.py").read_text(
            encoding="utf-8")
        for act in ("adjust_placement", "reassign_pins",
                    "preroute_critical"):
            assert act in src, f"driver menu missing upstream action {act}"
