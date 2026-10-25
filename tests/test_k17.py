# -*- coding: utf-8 -*-
"""K17 W-round tests: LLM driver (决策层) — menu validation, fallback
policy, policy-file mode, executors, auto_route wiring."""
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from fluxpcb.routing_llm import Driver, MENU, _validate, _fallback_decision


class TestMenuValidation:
    """A hallucinating model can at worst behave like the legacy loop."""

    def test_valid_actions_pass(self):
        assert _validate({"action": "reroute_nets",
                          "nets": ["A", "B"]})["action"] == "reroute_nets"
        d = _validate({"action": "move_footprint", "ref": "U9",
                       "dx_mm": 1.5, "dy_mm": -0.8, "reason": "sealed"})
        assert d and d["ref"] == "U9" and d["dx_mm"] == 1.5
        assert _validate({"action": "finish", "reason": "done"}) is not None
        assert _validate({"action": "jumper", "nets": ["3V3"]}) is not None

    def test_invalid_rejected(self):
        assert _validate({"action": "teleport_board"}) is None
        assert _validate({"action": "move_footprint", "ref": "U1",
                          "dx_mm": 99, "dy_mm": 0}) is None      # >10mm
        assert _validate({"action": "reroute_nets"}) is None     # no nets
        assert _validate({"action": "swap_pins", "ref": "U1",
                          "pad_a": "A1"}) is None                # missing b
        assert _validate("not a dict") is None
        assert _validate(None) is None

    def test_every_menu_action_validatable(self):
        for act, params in MENU.items():
            d = {"action": act, "reason": "t"}
            d.update(params.__class__ and {
                "nets": ["N"], "max_ripup": 5, "iters": 3, "timeout_s": 900,
                "ref": "U1", "dx_mm": 0.5, "dy_mm": 0.5,
                "pad_a": "A1", "pad_b": "B2"})
            assert _validate(d) is not None, act


class TestFallbackPolicy:
    def test_finish_when_clean(self):
        d = _fallback_decision({"opens": 0, "shorts": 0}, [])
        assert d["action"] == "finish"

    def test_surgical_first_for_small_residue(self):
        d = _fallback_decision(
            {"opens": 5, "shorts": 0,
             "fragmented": {"3V3": 2, "EN": 1}}, [])
        assert d["action"] == "reroute_nets"
        assert set(d["nets"]) == {"3V3", "EN"}

    def test_global_pass_for_large_residue(self):
        d = _fallback_decision({"opens": 40, "shorts": 0, "fragmented": {}},
                               [])
        assert d["action"] == "rerun_session"

    def test_escalate_after_stagnation(self):
        hist = [{"action": "rerun_session", "delta_opens": 0},
                {"action": "rerun_session", "delta_opens": 1}]
        d = _fallback_decision({"opens": 20, "shorts": 0}, hist)
        assert d["action"] == "escalate"


class TestPolicyFileMode:
    def test_policy_queue_consumed_in_order(self, tmp_path, monkeypatch):
        pf = tmp_path / "policy.json"
        pf.write_text(json.dumps([
            {"action": "reroute_nets", "nets": ["EN"]},
            {"action": "finish", "reason": "done"}]), encoding="utf-8")
        monkeypatch.setenv("FLUXNT_LLM_POLICY", str(pf))
        drv = Driver(log=lambda s: None)
        assert drv.mode == "policy"
        d1 = drv.decide({"opens": 3, "fragmented": {"EN": 1}})
        d2 = drv.decide({"opens": 0})
        assert d1["action"] == "reroute_nets"
        assert d2["action"] == "finish"
        # queue empty -> falls back
        d3 = drv.decide({"opens": 0, "shorts": 0})
        assert d3["action"] == "finish" and d3["source"] != "policy"

    def test_invalid_policy_entry_falls_back(self, tmp_path, monkeypatch):
        pf = tmp_path / "p.json"
        pf.write_text(json.dumps([{"action": "bogus"}]), encoding="utf-8")
        monkeypatch.setenv("FLUXNT_LLM_POLICY", str(pf))
        drv = Driver(log=lambda s: None)
        d = drv.decide({"opens": 40, "fragmented": {}})
        assert d["action"] == "rerun_session"


class TestDriverAudit:
    def test_history_feedback(self, tmp_path, monkeypatch):
        monkeypatch.delenv("FLUXNT_LLM_POLICY", raising=False)
        monkeypatch.delenv("FLUXNT_LLM_PROVIDER", raising=False)
        drv = Driver(log=lambda s: None)
        assert drv.mode == "fallback"
        drv.decide({"opens": 40, "shorts": 0, "fragmented": {}})
        drv.record_outcome(40, 0, 32, 0)
        assert drv.history[-1]["delta_opens"] == -8


class TestWiring:
    def test_auto_route_driver_gate(self):
        src = (REPO / "fluxpcb" / "routing.py").read_text(encoding="utf-8")
        assert 'FLUXNT_LLM_DRIVER") == "1"' in src
        assert "_driver_loop(pcb_path" in src
        assert '"reroute_nets"' in src
        assert "driver_decisions.json" in src

    def test_llm_baseurl_supported(self):
        src = (REPO / "fluxpcb" / "llm.py").read_text(encoding="utf-8")
        assert "FLUXNT_LLM_BASEURL" in src
        assert "{self.base_url}/chat/completions" in src

    def test_executors_exist(self):
        for f, tokens in {
            "rip_nets.py": ["sys.argv[2].split(\",\")", "b.Save"],
            "fp_move.py": ["SetPosition", "GetNetCode() in nets"],
            "pin_swap.py": ["padA.SetNet(nb)", "padB.SetNet(na)"],
        }.items():
            src = (REPO / "fluxpcb" / "kicadgen" / f).read_text(
                encoding="utf-8")
            for t in tokens:
                assert t in src, (f, t)

    def test_jumper_net_filter(self):
        src = (REPO / "fluxpcb" / "kicadgen" / "route_jumper.py").read_text(
            encoding="utf-8")
        assert "ONLY_NETS" in src and "sys.argv[3]" in src
