# -*- coding: utf-8 -*-
"""P-phase tests: true-connectivity routing chain + Pixhawk-class h743-fc.

Covers the conn_audit path bug (K7), the ratsnest-driven auto_route, the
depth-2 evicting MCU allocator, BMI088/ICM-45686 pinmaps, and the fcfull
board family (50x50, 8 PWM/8 UART/TF/dual-CAN/2 ADC).
"""
import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent


# ------------------------------------------------------------- P1: gate path
class TestConnAuditPath:
    def test_gate_finds_conn_audit(self):
        """The routing_completeness gate must resolve conn_audit.py — the
        original bug pointed at gates/kicadgen/ (nonexistent)."""
        from fluxpcb.gates import routing_completeness as _gate
        import inspect
        src = inspect.getsource(_gate)
        assert 'parent.parent / "kicadgen"' in src
        audit = (REPO / "fluxpcb" / "kicadgen" / "conn_audit.py")
        assert audit.exists()

    def test_conn_audit_pairs_mode_writes_pairs(self, tmp_path):
        """--pairs mode: island pairs file + same JSON as gate mode."""
        src = (REPO / "fluxpcb" / "kicadgen" / "conn_audit.py").read_text(
            encoding="utf-8")
        assert "--pairs" in src
        assert "GetUnconnectedCount" in src
        assert "GetConnectedItems" in src     # zone-traversing 1-arg form
        # position-keyed union-find (SWIG wrapper ids are NOT stable)
        assert "_UF" in src and "uf.union" in src

    def test_ratsnest_state_helper_exists(self):
        from fluxpcb.routing import ratsnest_state, _CONN_AUDIT
        assert _CONN_AUDIT.exists()
        assert _CONN_AUDIT.name == "conn_audit.py"


# --------------------------------------------------- P2: allocator + pinmaps
class TestFCAllocator:
    def test_fc_want_all_signals_present(self):
        from fluxpcb.kicadgen import mcu_db
        assert len(mcu_db.FC_WANT) >= 52
        pins = mcu_db.load_st_xml(mcu_db.MCU_CONFIG["h743"]["xml"])
        want = (list(mcu_db.FC_WANT) + mcu_db.USB_SIGNALS["h743"]
                + mcu_db.SWD_SIGNALS)
        alloc = mcu_db.allocate(want, pins)
        missing = [s for s in want if s not in alloc]
        assert not missing, f"unallocated: {missing}"

    def test_no_ball_conflicts(self):
        from fluxpcb.kicadgen import mcu_db
        pins = mcu_db.load_st_xml(mcu_db.MCU_CONFIG["h743"]["xml"])
        want = (list(mcu_db.FC_WANT) + mcu_db.USB_SIGNALS["h743"]
                + mcu_db.SWD_SIGNALS)
        alloc = mcu_db.allocate(want, pins)
        balls = [a["ball"] for a in alloc.values()]
        assert len(balls) == len(set(balls))

    def test_uart5_tx_rescued_by_eviction(self):
        """UART5_TX sits only on PB6/PC12/PB13, all held by prio-1 signals —
        the depth-2 eviction chain (I2C1_SCL -> D5 held by TIM4_CH3 -> ...) 
        must rescue it."""
        from fluxpcb.kicadgen import mcu_db
        pins = mcu_db.load_st_xml(mcu_db.MCU_CONFIG["h743"]["xml"])
        want = list(mcu_db.FC_WANT) + mcu_db.USB_SIGNALS["h743"] \
            + mcu_db.SWD_SIGNALS
        alloc = mcu_db.allocate(want, pins)
        assert "UART5_TX" in alloc

    def test_fc_pwm_map_eight_channels(self):
        from fluxpcb.kicadgen import mcu_db
        pins = mcu_db.load_st_xml(mcu_db.MCU_CONFIG["h743"]["xml"])
        bn = mcu_db.build_ball_nets("h743", pins, want=mcu_db.FC_WANT,
                                    pwm_map=mcu_db.FC_PWM_MAP)
        pwms = sorted(n for n in set(bn.values()) if n.startswith("PWM_"))
        assert pwms == [f"PWM_CH{i}" for i in range(1, 9)]

    def test_sdmmc_priority_beats_tim(self):
        """SDMMC signals live on 1-2 balls each — prio 1 keeps TIM from
        stealing them."""
        from fluxpcb.kicadgen import mcu_db
        src = (REPO / "fluxpcb" / "kicadgen" / "mcu_db.py").read_text(
            encoding="utf-8")
        assert '"SDMMC" in s' in src

    def test_legacy_px4_allocation_unchanged(self):
        from fluxpcb.kicadgen import mcu_db
        pins = mcu_db.load_st_xml(mcu_db.MCU_CONFIG["h743"]["xml"])
        bn = mcu_db.build_ball_nets("h743", pins)
        pwms = sorted(n for n in set(bn.values()) if n.startswith("PWM_"))
        assert pwms == ["PWM_11", "PWM_12", "PWM_13", "PWM_14",
                        "PWM_31", "PWM_32"]


class TestSensorPinmaps:
    def test_bmi088_pinmap(self):
        sp = json.loads((REPO / "reference" / "fculib" /
                         "sensor_pinmaps.json").read_text(encoding="utf-8"))
        pm = sp["BMI088"]
        assert len(pm) == 16
        assert pm["8"] == "SCK" and pm["9"] == "SDI"     # main SPI
        assert pm["14"] == "CSB1" and pm["15"] == "SDO1"
        assert pm["3"] == "VDD" and pm["11"] == "VDDIO"

    def test_icm45686_pinmap(self):
        sp = json.loads((REPO / "reference" / "fculib" /
                         "sensor_pinmaps.json").read_text(encoding="utf-8"))
        pm = sp["ICM-45686"]
        assert len(pm) == 14
        # pin-compatible with ICM-42688-P (same 14-LGA family)
        assert pm == sp["ICM-42688-P"]

    def test_bmi088_footprint_is_bmi055_family(self):
        fp = REPO / "reference" / "fculib" / "BMI055.kicad_mod"
        txt = fp.read_text(encoding="utf-8", errors="replace")
        assert txt.count('(pad ') == 16


# ------------------------------------------------------------ P3: fcfull board
class TestFcBoard:
    def test_placement_validates_clean(self):
        from fluxpcb.kicadgen import fcfull
        assert fcfull.validate_placement() == []

    def test_nets_cover_all_requirements(self):
        from fluxpcb.kicadgen import fcfull
        nets = set(fcfull.NETS)
        for i in range(1, 9):
            assert f"PWM_CH{i}" in nets          # 8 PWM
        for u in ("USART1", "USART2", "USART3", "USART6",
                  "UART4", "UART5", "UART7", "UART8"):
            assert f"{u}_TX" in nets and f"{u}_RX" in nets   # 8 UART
        for s in ("SDMMC1_CK", "SDMMC1_CMD", "SDMMC1_D0", "SDMMC1_D3"):
            assert s in nets                      # TF card
        for c in ("FDCAN1_TX", "FDCAN2_TX", "CAN1_H", "CAN2_L"):
            assert c in nets                      # 2 CAN
        for a in ("ADC1_INP10", "ADC1_INP11"):
            assert a in nets                      # 2 ADC
        assert "USB_DP" in nets and "SWDIO" in nets

    def test_mcu_ball_nets_cover_fc_want(self):
        from fluxpcb.kicadgen import fcfull
        bn = fcfull.mcu_ball_nets()
        vals = set(bn.values())
        # every FC interface lands on a real net
        for s in ("SDMMC1_CK", "FDCAN2_TX", "USART1_TX", "UART8_RX",
                  "ADC1_INP10", "IMU1_CS2"):
            assert s in vals, s

    def test_hdr3x08_generated_footprint(self, tmp_path):
        from fluxpcb.kicadgen.fcfull import gen_hdr3x08
        out = gen_hdr3x08(tmp_path / "HDR.kicad_mod")
        txt = out.read_text(encoding="utf-8")
        assert txt.count('(pad "') == 24
        assert 'thru_hole' in txt

    def test_connector_count(self):
        from fluxpcb.kicadgen import fcfull
        pl = fcfull.placement()
        js = [r for r in pl if r.startswith("J")]
        assert len(js) == 17        # 8 UART + SWD + USB + PWM + 2CAN + ADC
                                    # + GND + DEBUG + TF


class TestFcBom:
    def test_all_fc_roles_select(self):
        import yaml
        from fluxpcb import partslib
        spec = yaml.safe_load((REPO / "projects" / "h743-fc" / "spec.yaml")
                              .read_text(encoding="utf-8"))
        for need in partslib.fc_roles(spec):
            part = partslib.select(need["role"], need.get("value"))
            assert part.mpn

    def test_px4_roles_h743_uses_h743_mcu(self):
        from fluxpcb.partslib import px4_roles
        roles = px4_roles({"constraints": {"mcu": "h743"}})
        top = [r["role"] for r in roles]
        assert "mcu_h743" in top and "mcu_n6" not in top
        assert "ind_vddcore" not in top        # H7 has VCAP, no inductor

    def test_px4_roles_n6_default(self):
        from fluxpcb.partslib import px4_roles
        roles = px4_roles({"constraints": {}})
        assert [r["role"] for r in roles][0] == "mcu_n6"


# ------------------------------------------------------- P1: router mechanics
class TestRouterUpgrades:
    def test_route_embedded_power_via_pass(self):
        src = (REPO / "fluxpcb" / "kicadgen" / "route_embedded.py").read_text(
            encoding="utf-8")
        assert "def power_via_pass" in src
        assert "not pad.IsOnLayer(layer_id)" in src  # F pads don't block B
        assert "PAD_ATTRIB_PTH" in src               # KiCad 10 enum rename

    def test_shorts_cleanup_blast_radius(self):
        src = (REPO / "fluxpcb" / "kicadgen" / "shorts_cleanup.py").read_text(
            encoding="utf-8")
        assert "best_d = 0.65" in src     # closest-item-only, no 2.5mm blast

    def test_auto_route_uses_ratsnest(self):
        import inspect
        from fluxpcb import routing
        src = inspect.getsource(routing.auto_route)
        assert "ratsnest_state" in src
        assert "extract_pairs" not in src.split("def auto_route")[1].split("def ")[0]
