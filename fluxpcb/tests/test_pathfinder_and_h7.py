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
