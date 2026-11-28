# -*- coding: utf-8 -*-
"""Regression tests for 6-layer PathFinder, H743 spatial pinout, pcb.py fixes,
and DeepSeek V4 Pro MAX reasoning / SSE streaming support."""
from __future__ import annotations

import io
import json
import math
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]


# ----------------------------------------------------------- ExactBoard nm checks
class TestPathFinderExactGeometry:
    def _import_pf(self):
        import sys
        kg = str(REPO / "fluxpcb" / "kicadgen")
        if kg not in sys.path:
            sys.path.insert(0, kg)
        import route_pathfinder as rp
        return rp

    def test_bga_08mm_infeasibility_at_015mm_and_vippo_legality(self):
        rp = self._import_pf()
        outline = [(0, 0), (50_000_000, 0), (50_000_000, 50_000_000), (0, 50_000_000)]
        # 2x2 BGA cell at 0.80mm pitch, 0.40mm pad, 0.150mm clearance, 0.127mm track
        eb = rp.ExactBoard(n_layers=6, clear_nm=150_000, hole_nm=250_000,
                           edge_nm=300_000, outline=outline)
        cx, cy = 20_000_000, 20_000_000
        pitch = 800_000
        # 4 neighbor balls with distinct nets
        eb.add_item(1, (0,), (1, cx, cy, 200_000))
        eb.add_item(2, (0,), (1, cx + pitch, cy, 200_000))
        eb.add_item(3, (0,), (1, cx, cy + pitch, 200_000))
        eb.add_item(4, (0,), (1, cx + pitch, cy + pitch, 200_000))

        # Track between balls at 0.15mm clearance: gap = (0.8 - 0.4 - 0.127)/2 = 0.1365mm < 0.15mm -> REFUSED
        ok_ch, _ = eb.seg_clear(cx + pitch // 2, cy - 1_000_000, cx + pitch // 2, cy + 1_000_000,
                                127_000, 0, 5)
        assert not ok_ch

        # Dogbone 0.50/0.25mm via at 4-ball center: gap = 0.5657 - 0.20 - 0.25 = 0.1157mm < 0.15mm -> REFUSED
        ok_dog, _ = eb.via_clear(cx + pitch // 2, cy + pitch // 2, 250_000, 250_000, 1)
        assert not ok_dog

        # VIPPO 0.50/0.25mm via on ball 1 center: gap to ball 2 = 0.80 - 0.25 - 0.20 = 0.35mm >= 0.15mm -> LEGAL
        ok_vippo, _ = eb.via_clear(cx, cy, 250_000, 250_000, 1)
        assert ok_vippo

    def test_jlc6l_dogbone_legal_at_0127mm(self):
        rp = self._import_pf()
        outline = [(0, 0), (50_000_000, 0), (50_000_000, 50_000_000), (0, 50_000_000)]
        eb = rp.ExactBoard(n_layers=6, clear_nm=127_000, hole_nm=250_000,
                           edge_nm=300_000, outline=outline)
        cx, cy, pitch = 20_000_000, 20_000_000, 800_000
        eb.add_item(1, (0,), (1, cx, cy, 200_000))
        eb.add_item(2, (0,), (1, cx + pitch, cy, 200_000))
        eb.add_item(3, (0,), (1, cx, cy + pitch, 200_000))
        eb.add_item(4, (0,), (1, cx + pitch, cy + pitch, 200_000))

        # Track between 0.40mm balls: gap = 0.1365mm >= 0.127mm -> LEGAL
        ok_ch, _ = eb.seg_clear(cx + pitch // 2, cy - 1_000_000, cx + pitch // 2, cy + 1_000_000,
                                127_000, 0, 5)
        assert ok_ch

        # Dogbone 0.40/0.20mm via at 4-ball center: gap = 0.1657mm >= 0.127mm -> LEGAL
        ok_dog, _ = eb.via_clear(cx + pitch // 2, cy + pitch // 2, 200_000, 200_000, 1)
        assert ok_dog

    def test_owner_rollback_removes_items_cleanly(self):
        rp = self._import_pf()
        outline = [(0, 0), (50_000_000, 0), (50_000_000, 50_000_000), (0, 50_000_000)]
        eb = rp.ExactBoard(n_layers=6, clear_nm=150_000, hole_nm=250_000,
                           edge_nm=300_000, outline=outline)
        eb.add_item(1, (0,), (0, 10_000_000, 10_000_000, 20_000_000, 10_000_000, 150_000), owner="net:A")
        ok_blocked, blocker = eb.seg_clear(15_000_000, 5_000_000, 15_000_000, 15_000_000, 150_000, 0, 2)
        assert not ok_blocked and blocker == "net:A"
        eb.remove_owner("net:A")
        ok_free, _ = eb.seg_clear(15_000_000, 5_000_000, 15_000_000, 15_000_000, 150_000, 0, 2)
        assert ok_free


# ----------------------------------------------------------- Router tactic wiring
class TestPathFinderRouterIntegration:
    def test_tactics_registered_and_order_compatible(self):
        from fluxpcb.proof import repair_loop, router
        assert repair_loop.DEFAULT_TACTICS[:2] == ('freerouting', 'embedded')
        assert 'pathfinder_keep' in repair_loop.DEFAULT_TACTICS
        assert 'pathfinder' in repair_loop.DEFAULT_TACTICS
        assert 'pathfinder_keep' in router._KEEP_TACTICS
        assert router._tactic_base('pathfinder_keep') == 'pathfinder'
        assert router._tactic_base('pathfinder_full') == 'pathfinder'
        assert router._tactic_base('pathfinder') == 'pathfinder'

    def test_step_audit_accepts_process_and_robust_kwargs(self):
        import inspect
        from fluxpcb.proof import step
        sig = inspect.signature(step.audit)
        assert 'process' in sig.parameters
        assert 'robust' in sig.parameters


# ----------------------------------------------------------- Spatial MCMF pinout
class TestH7SpatialPinout:
    def test_spatial_allocator_improves_distance_and_outer_ring(self):
        from fluxpcb.kicadgen import fcfull, mcu_db
        pins = mcu_db.load_st_xml(mcu_db.MCU_CONFIG["h743"]["xml"])
        pl = fcfull.placement()
        ux, uy, _ = pl["U1"]
        bxy = mcu_db.ball_positions("h743", pins, cx=ux, cy=uy, pitch=0.8)
        dest = fcfull._signal_destinations(pl)

        bn_alpha = fcfull.mcu_ball_nets(spatial=False)
        bn_spat = fcfull.mcu_ball_nets(spatial=True)

        assert set(bn_alpha.values()) == set(bn_spat.values())
        assert len(bn_spat) == len(set(bn_spat.keys()))

        rows, c0, c1 = mcu_db.grid(pins)
        ri = {r: i for i, r in enumerate(rows)}
        nr, nc = len(rows), c1 - c0 + 1

        def score(bn):
            sigs = {b: n for b, n in bn.items()
                    if n not in ("GND", "3V3", "VCAP", "NRST", "BOOT0")}
            r0 = sum(1 for b in sigs
                     if min(ri[b[0]], int(b[1:]) - c0, nr - 1 - ri[b[0]],
                            nc - 1 - (int(b[1:]) - c0)) == 0)
            dist = sum(math.hypot(bxy[b][0] - dest[n][0], bxy[b][1] - dest[n][1])
                       for b, n in sigs.items() if n in dest)
            return r0, dist

        r0_old, d_old = score(bn_alpha)
        r0_new, d_new = score(bn_spat)
        assert r0_new > r0_old, f"expected more ring-0 signals: {r0_new} <= {r0_old}"
        assert d_new < d_old * 0.92, f"expected >=8% wirelength reduction: {d_new} vs {d_old}"


# ----------------------------------------------------------- pcb.py generator fixes
class TestPcbGeneratorFixes:
    def test_duplicate_fp_regenerates_unique_uuids(self):
        from fluxpcb.kicadgen.fcfull import LocalFp
        from fluxpcb.kicadgen.pcb import Board, render
        fp_path = REPO / "reference" / "fculib" / "BMP388.kicad_mod"
        b = Board("dup_test", 50.0, 50.0, ["GND", "3V3", "I2C1_SCL"], n_layers=6)
        b.place(LocalFp(fp_path, "U4", "BMP388", 10.0, 10.0, 0.0, {"1": "3V3", "3": "GND"}))
        b.place(LocalFp(fp_path, "U5", "BMP388", 20.0, 10.0, 0.0, {"1": "3V3", "3": "GND"}))
        txt = b.to_text()
        import re
        uuids = re.findall(r'\(uuid "([^"]+)"\)', txt)
        assert len(uuids) == len(set(uuids)), "duplicate UUIDs found across U4/U5!"

    def test_legacy_fp_text_reference_and_value_replaced(self):
        from fluxpcb.kicadgen.fcfull import LocalFp
        from fluxpcb.kicadgen.pcb import Board, parse
        fp_path = REPO / "reference" / "fculib" / "ICM-42688-P.kicad_mod"
        b = Board("icm_test", 50.0, 50.0, ["GND", "3V3"], n_layers=6)
        b.place(LocalFp(fp_path, "U3", "ICM-45686", 15.0, 28.0, 0.0, {"5": "3V3", "6": "GND"}))
        txt = b.to_text()
        assert '"REF**"' not in txt
        assert '"U3"' in txt and '"ICM-45686"' in txt
        # Verify parsed tree has fp_text reference -> "U3"
        tree = parse(txt)
        fp_node = next(v for k, v in tree if k == "n" and v and v[0] == ("a", "footprint"))
        refs = [v[2][1] for k, v in fp_node if k == "n" and len(v) >= 3
                and v[0] == ("a", "fp_text") and v[1] == ("a", "reference")]
        assert refs == ["U3"]

    def test_rotated_footprint_rotates_courtyard_with_pads(self):
        from fluxpcb.kicadgen.fcfull import LocalFp
        from fluxpcb.kicadgen.pcb import Board
        fp_path = REPO / "reference" / "fculib" / "ICM-42688-P.kicad_mod"
        b = Board("rot_test", 50.0, 50.0, ["GND"], n_layers=6)
        fp = LocalFp(fp_path, "U3", "ICM-45686", 25.0, 25.0, 90.0, {"6": "GND"})
        b.place(fp)
        txt = b.to_text()
        # Original ICM-42688-P F.CrtYd spans x=+-1.7526, y=+-1.4986.
        # After rot=90 ((x,y)->(y,-x)), courtyard must span x=+-1.4986, y=+-1.7526!
        assert "-1.4986 -1.7526" in txt or "1.4986 -1.7526" in txt

    def test_zone_polygon_insets_inside_board_and_stitch_vias_use_int_net(self):
        from fluxpcb.kicadgen.pcb import Board
        b = Board("zone_test", 50.0, 50.0, ["GND", "3V3", "5V"], n_layers=6)
        z = b._zone("GND", "In1.Cu")
        assert "(xy 0.35 0.35)" in z and "(xy 49.65 49.65)" in z
        assert "-0.2" not in z
        vias = b.stitch_vias([(10.0, 10.0, "3V3")])
        assert f'(net {b.nets["3V3"]})' in vias[0]
        assert '(net "3V3")' not in vias[0]


# ----------------------------------------------------------- LLM reasoning MAX + SSE
class TestLLMReasoningEffort:
    def test_reasoning_effort_enables_sse_streaming_and_extracts_content(self, monkeypatch):
        from fluxpcb.llm import LLMAdapter
        import urllib.request

        monkeypatch.setenv("FLUXNT_LLM_PROVIDER", "openai")
        monkeypatch.setenv("FLUXNT_LLM_MODEL", "deepseek-v4-pro")
        monkeypatch.setenv("FLUXNT_LLM_BASEURL", "https://api.deepseek.com/v1")
        monkeypatch.setenv("FLUXNT_LLM_API_KEY_ENV", "TEST_DS_KEY")
        monkeypatch.setenv("TEST_DS_KEY", "sk-test-fake")
        monkeypatch.setenv("FLUXNT_LLM_REASONING_EFFORT", "max")
        monkeypatch.delenv("FLUXNT_LLM_MAX_TOKENS", raising=False)

        captured = {}

        class FakeResp(io.BytesIO):
            def __enter__(self): return self
            def __exit__(self, *a): return False

        def fake_urlopen(req, timeout=300):
            captured["body"] = json.loads(req.data.decode("utf-8"))
            sse = (
                b'data: {"choices":[{"delta":{"reasoning_content":"step1 "}}]}\n\n'
                b'data: {"choices":[{"delta":{"reasoning_content":"step2"}}]}\n\n'
                b'data: {"choices":[{"delta":{"content":"final "}}]}\n\n'
                b'data: {"choices":[{"delta":{"content":"answer"}}]}\n\n'
                b'data: [DONE]\n\n'
            )
            return FakeResp(sse)

        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
        adapter = LLMAdapter()
        res = adapter.complete("sys", "hello")
        assert res["ok"] is True
        assert res["text"] == "final answer"
        assert res["reasoning"] == "step1 step2"
        assert captured["body"]["reasoning_effort"] == "max"
        assert captured["body"]["stream"] is True
        assert "max_tokens" not in captured["body"]


# ----------------------------------------------------------- 12-Component Domain Fixes
class TestFluxpcbDomainFixes:
    def test_spec_lowering_and_conjectures_on_h7_spec(self):
        import yaml
        from fluxpcb.proof import conjectures
        sp = yaml.safe_load((REPO / "projects" / "h743-fc" / "spec.yaml").read_text(encoding="utf-8"))
        board_nets = {"USB_DP", "USB_DM", "CAN1_H", "CAN1_L", "CAN2_H", "CAN2_L",
                      "5V", "3V3", "GND", "VCAP", "VDDA"}
        lc = conjectures.lower_constraints(sp, board_nets=board_nets)
        dp_by_name = {d["name"]: d for d in lc["diff_pairs"]}
        assert set(dp_by_name) == {"usb", "can1", "can2"}
        assert dp_by_name["usb"]["p"] == "USB_DP" and dp_by_name["usb"]["n"] == "USB_DM"
        assert dp_by_name["usb"]["z_target"] == 90.0
        assert dp_by_name["usb"]["w_mm"] > 0.25
        assert dp_by_name["can1"]["z_target"] == 120.0
        assert lc["net_widths_mm"]["5V"] >= 0.45
        assert lc["net_widths_mm"]["3V3"] >= 0.35
        assert lc["net_widths_mm"]["GND"] >= 0.35

        # Compile conjectures on a synthetic minimal state containing 5V pads
        fake_st = {"items": [], "netPads": {"5V": ["p1", "p2"], "3V3": ["p3", "p4"]}, "rules": {}}
        claims, _ = conjectures.from_spec(sp, fake_st)
        cids = {c["id"] for c in claims}
        for req in ("POWER:DROP:3V3", "POWER:J:3V3", "POWER:DROP:5V", "POWER:J:5V",
                    "SIGNAL:ZDIFF:usb", "SIGNAL:SKEW:usb", "SIGNAL:COUPLED:usb",
                    "SIGNAL:ZDIFF:can1", "SIGNAL:COUPLED:can1",
                    "SIGNAL:ZDIFF:can2", "SIGNAL:COUPLED:can2"):
            assert req in cids, f"missing domain conjecture {req}"

    def test_domain_kernels_zdiff_ampacity_and_coupling(self):
        from fluxpcb.kernels import derive
        z_usb = derive.zdiff_edge_coupled(0.3048, 0.20, 0.20, 4.4, 0.035, kind="microstrip")
        assert z_usb["status"] == "derived"
        assert 85.0 <= z_usb["measured"] <= 95.0
        z_can = derive.zdiff_edge_coupled(0.1646, 0.18, 0.21, 4.4, 0.035, kind="microstrip")
        assert 114.0 <= z_can["measured"] <= 126.0
        assert derive.ipc_min_width_mm(0.5, t_mm=0.035, temp_rise_c=10.0) > 0.10

        # Coupled vs uncoupled differential pair audit
        st_good = {
            "items": [
                {"net": "USB_DP", "layers": ["F.Cu"], "shape": {"kind": "seg", "x1": 0, "y1": 0, "x2": 10_000_000, "y2": 0, "w": 304_800}},
                {"net": "USB_DM", "layers": ["F.Cu"], "shape": {"kind": "seg", "x1": 0, "y1": 504_800, "x2": 10_000_000, "y2": 504_800, "w": 304_800}},
            ]
        }
        good = derive.diff_coupling(st_good, "USB_DP", "USB_DM", target_s_mm=0.20, max_skew_mm=0.5)
        assert good["ok"] is True and good["measured"] >= 95.0 and good["skew_mm"] == 0.0
        assert abs(good["avg_gap_mm"] - 0.20) < 0.005

        st_bad = {
            "items": [
                {"net": "USB_DP", "layers": ["F.Cu"], "shape": {"kind": "seg", "x1": 0, "y1": 0, "x2": 10_000_000, "y2": 0, "w": 129_000}},
                {"net": "USB_DP", "layers": ["F.Cu", "In1.Cu"], "drill": 200_000, "shape": {"kind": "circle", "cx": 5_000_000, "cy": 0, "r": 200_000}},
                {"net": "USB_DM", "layers": ["F.Cu"], "shape": {"kind": "seg", "x1": 0, "y1": 5_000_000, "x2": 8_000_000, "y2": 5_000_000, "w": 129_000}},
            ]
        }
        bad = derive.diff_coupling(st_bad, "USB_DP", "USB_DM", target_s_mm=0.20, max_skew_mm=0.5)
        assert bad["ok"] is False and bad["via_diff"] == 1 and bad["skew_mm"] == 2.0

    def test_classifier_no_false_diff_positives_and_comm_fingerprint_tracks_widths(self):
        from fluxpcb.subagents.classifier import SignalClassifierSubagent
        from fluxpcb.contracts import SignalClass
        from fluxpcb.runtime.comm import CommRuntime
        clf = SignalClassifierSubagent()
        nets = ["USB_DP", "USB_DM", "CAN1_H", "CAN1_L", "VCAP", "NRST", "PWM_CH1", "OSC_IN", "5V", "GND"]
        cls_map = clf.classify(nets)
        assert cls_map["USB_DP"] == SignalClass.DIFFERENTIAL
        assert cls_map["USB_DM"] == SignalClass.DIFFERENTIAL
        assert cls_map["CAN1_H"] == SignalClass.DIFFERENTIAL
        assert cls_map["CAN1_L"] == SignalClass.DIFFERENTIAL
        for non_diff in ("VCAP", "NRST", "PWM_CH1", "OSC_IN", "5V", "GND"):
            assert cls_map[non_diff] != SignalClass.DIFFERENTIAL, f"false DIFFERENTIAL on {non_diff}"
        assert set(clf.find_diff_pairs(nets)) == {("USB_DP", "USB_DM"), ("CAN1_H", "CAN1_L")}

        # CommRuntime fingerprint must change when track width changes (same track count!)
        class _Pt:
            def __init__(self, x, y): self.x, self.y = x, y
        class _Track:
            def __init__(self, w): self._w = w
            def Type(self): return 0
            def GetStart(self): return _Pt(1000, 2000)
            def GetEnd(self): return _Pt(5000, 2000)
            def GetWidth(self): return self._w
            def GetLayer(self): return 0
            def GetNetCode(self): return 1
        class _Board:
            def __init__(self, w): self._t = [_Track(w)]
            def GetTracks(self): return self._t
            def GetFootprints(self): return []
            def Zones(self): return []
            def GetNetCount(self): return 2
            def GetCopperLayerCount(self): return 6
        comm = CommRuntime()
        fp1 = comm._compute_fingerprint(_Board(129_000))
        fp2 = comm._compute_fingerprint(_Board(450_000))
        assert fp1.board_hash != fp2.board_hash, "fingerprint missed track width mutation!"

        # Per-netclass pairwise clearance in ExactBoard
        import sys
        kg = str(REPO / "fluxpcb" / "kicadgen")
        if kg not in sys.path:
            sys.path.insert(0, kg)
        import route_pathfinder as rp
        outline = [(0, 0), (50_000_000, 0), (50_000_000, 50_000_000), (0, 50_000_000)]
        eb = rp.ExactBoard(n_layers=2, clear_nm=200_000, hole_nm=200_000,
                           edge_nm=0, outline=outline, net_clear_nm={2: 260_000})
        eb.add_item(1, (0,), (0, 10_000_000, 10_000_000, 20_000_000, 10_000_000, 200_000), owner="net:1")
        # Distance between centers = 430um -> edge-to-edge gap = 230um:
        # Legal for default net 3 (>= 200um), REFUSED for high-clearance net 2 (< 260um)!
        assert eb.seg_clear(10_000_000, 10_430_000, 20_000_000, 10_430_000, 200_000, 0, 3)[0] is True
        assert eb.seg_clear(10_000_000, 10_430_000, 20_000_000, 10_430_000, 200_000, 0, 2)[0] is False
