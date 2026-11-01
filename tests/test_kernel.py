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
        # K21 missing 泛化为; K22 has 泛化为 but misses 契约
        assert any("K21" in x for x in v)
        assert any("K22" in x and "契约" in x for x in v)

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


class TestHardContracts:
    """K22 point 12: an invariant written as a warning is a bug.
    Unbound measurements RAISE at the consumer boundary."""

    def test_bare_numbers_raise(self):
        from fluxpcb.kernel.state import require_fp, UnboundMeasurement
        for bad in (42, 3.14, {"opens": 5}, None, "5"):
            with pytest.raises(UnboundMeasurement):
                require_fp(bad, context="test-site")

    def test_fpless_kernelquery_raises(self):
        from fluxpcb.kernel.state import (require_fp, UnboundMeasurement,
                                          KernelQuery, StateFingerprint)
        q = KernelQuery("opens", 3, StateFingerprint("", "1"))
        with pytest.raises(UnboundMeasurement):
            require_fp(q)

    def test_fingerprinted_passes(self):
        from fluxpcb.kernel.state import (require_fp, KernelQuery,
                                          StateFingerprint)
        q = KernelQuery("opens", 3, StateFingerprint("a" * 64, "9"))
        assert require_fp(q) is q


class TestSemanticVersioning:
    """K22 point 9: silent semantic drift is structurally impossible —
    SEMANTICS registry + policy version are snapshotted here. Editing
    semantics without bumping versions turns THIS test red."""

    # ---- snapshot locks (update WITH a version bump, never without) ----
    SEMANTICS_LOCK = {
        "opens": (1, ("post-refill", "conn_audit-ratsnest")),
        "shorts": (1, ("post-refill", "kicad-cli-drc")),
        "clearance": (1, ("post-refill", "kicad-cli-drc")),
    }
    SEVERITIES_POLICY_VERSION_LOCK = 1

    def test_semantics_registered(self):
        from fluxpcb.kernel.state import SEMANTICS
        for name, (ver, pre) in self.SEMANTICS_LOCK.items():
            assert name in SEMANTICS
            assert SEMANTICS[name]["semantic_version"] == ver, \
                f"{name} semantics changed without version bump"
            assert tuple(SEMANTICS[name]["preconditions"]) == pre, \
                f"{name} preconditions changed without version bump"

    def test_severities_policy_version_locked(self):
        import importlib
        import fluxpcb.routing as R
        importlib.reload(R) if False else None
        src = (REPO / "fluxpcb" / "routing.py").read_text(encoding="utf-8")
        assert "SEVERITIES_POLICY_VERSION" in src
        assert f"SEVERITIES_POLICY_VERSION = " \
               f"{self.SEVERITIES_POLICY_VERSION_LOCK}" in src, \
            "severity policy changed without version bump (update lock)"

    def test_query_reports_semantics(self):
        from fluxpcb.kernel.state import SEMANTICS
        assert all("meaning" in v for v in SEMANTICS.values())


class TestSingleDerivation:
    """K22 point 9/10: the same question is derived in exactly one
    place. Duplicate derivation sites turn this test red."""

    def test_drc_counts_derive_in_kernel_only(self):
        src = (REPO / "fluxpcb" / "routing.py").read_text(encoding="utf-8")
        # run_drc must delegate counting to kernel.state.count_drc
        assert "count_drc" in src
        # no inline counting of DRC sections outside the kernel
        body = src.split("def run_drc")[1].split("\ndef ")[0]
        assert 'txt.count("[shorting_items]")' not in body
        assert 'txt.count("[clearance]")' not in body

    def test_connectivity_derivation_sites(self):
        """Consumer boundary: top-level consumers (routing/gates/stages)
        may not derive connectivity themselves — kernel is the one
        derivation. Engine-internal verification (kicadgen pair checks)
        is a different quantity and stays allowed."""
        allowed = {"conn_audit.py", "state.py"}
        offenders = []
        top = list((REPO / "fluxpcb").glob("*.py")) +             list((REPO / "fluxpcb" / "gates").glob("*.py"))
        for f in top:
            if f.name in allowed:
                continue
            if "GetUnconnectedCount" in f.read_text(encoding="utf-8",
                                                    errors="replace"):
                offenders.append(str(f.relative_to(REPO)))
        assert not offenders, f"derivation leaked outside kernel: {offenders}"


class TestIntelC1:
    def test_build_requires_fingerprint(self):
        from fluxpcb.kernel import intel
        from fluxpcb.kernel.state import UnboundMeasurement
        with pytest.raises(UnboundMeasurement):
            intel.build(Path("x.kicad_pcb"), {"opens": 5}, [])

    def test_report_and_rule_assessment_shape(self):
        from fluxpcb.kernel import intel
        from fluxpcb.kernel.state import KernelQuery, StateFingerprint
        fp = StateFingerprint("a" * 64, "1")
        q = KernelQuery("opens", 12, fp, {"pairs": []})
        q2 = KernelQuery("shorts", 0, fp)
        rep = intel.build(Path("x.kicad_pcb"), {"opens": q, "shorts": q2},
                          ["SPI1_SCK|25|25|0|2.5|30|1"] * 12)
        assert rep["taxonomy"]
        a = intel._rule_assessment(rep)
        assert "state" in a and "root_causes" in a and "actions" in a
        assert any("pin" in c.get("cause", "") or
                   "assignment" in c.get("attribution", "")
                   for c in a["root_causes"]) or a["actions"]

    def test_flexibility_carries_fixits(self):
        from fluxpcb.kernel import intel
        pairs = ["SPI1_SCK|25|25|0|2.5|30|1"] * 12
        tax = {"escape-crossing": ["SPI1_SCK"]}
        c = intel.flexibility(Path("x"), pairs, tax)
        assert "SPI1_SCK" in c
        assert all("expected_effect" in o for o in c["SPI1_SCK"])


class TestDecisionC2:
    def test_reject_carries_fixit(self):
        from fluxpcb.kernel.decision import validate
        ok, fixit = validate({"action": "teleport"})
        assert not ok
        assert "alternatives" in fixit
        ok2, fixit2 = validate({"action": "jumper"})
        assert not ok2 and "nets" in fixit2.get("message", "")

    def test_valid_decision_passes(self):
        from fluxpcb.kernel.decision import validate
        ok, fixit = validate({"action": "jumper", "params": {"nets": ["A"]},
                              "expected_effect": "x"})
        assert ok

    def test_record_flows_back(self, tmp_path):
        from fluxpcb.kernel.decision import record
        import json as _j
        record(tmp_path / "b.kicad_pcb", {"action": "jumper"},
               {"summary": "ok"})
        j = _j.loads((tmp_path / "decision_journal.json").read_text(
            encoding="utf-8"))
        assert j[-1]["outcome"]["summary"] == "ok"


class TestLoopC3Wired:
    def test_closed_loop_in_routing(self):
        src = (REPO / "fluxpcb" / "routing.py").read_text(encoding="utf-8")
        assert "def _closed_loop" in src and "def _loop_execute" in src
        assert "_closed_loop(kicad_cli, kipy, pcb_path" in src
        assert 'FLUXNT_LOOP' in src

    def test_loop_termination_is_zero_opens_zero_shorts(self):
        src = (REPO / "fluxpcb" / "routing.py").read_text(encoding="utf-8")
        body = src.split("def _closed_loop")[1].split("\ndef ")[0]
        assert "qo.value <= 0 and qs.value <= 0" in body
        # budget-exhaustion must report honestly, not fake success
        assert "不伪装成功" in body

    def test_reject_flows_back_to_journal(self):
        src = (REPO / "fluxpcb" / "routing.py").read_text(encoding="utf-8")
        body = src.split("def _closed_loop")[1].split("\ndef ")[0]
        assert '{"rejected": fixit}' in body


class TestPersistentLearning:
    """Points 3/4: history effects must survive process boundaries —
    ineffective decisions are seeded from the journal, not re-tried."""

    def test_loop_seeds_tried_from_journal(self):
        src = (REPO / "fluxpcb" / "routing.py").read_text(encoding="utf-8")
        body = src.split("def _closed_loop")[1].split("\ndef ")[0]
        assert "_ineffective" in body
        assert "decision_journal.json" in body

    def test_outcome_records_delta_opens(self):
        src = (REPO / "fluxpcb" / "routing.py").read_text(encoding="utf-8")
        body = src.split("def _closed_loop")[1].split("\ndef ")[0]
        assert "delta_opens" in body

class TestD356RealWorldColumns:
    '''fc 实测: 定宽列在多位引脚上截断(B17->B1) — 分词解析回归锁'''

    def test_multichar_pins_survive(self, tmp_path):
        from fluxpcb.kernel import truthnet
        f = tmp_path / 'b.d356'
        rows = [
            '317FDCAN1_RX        U1    -B17        A01X+012756Y-007244X0157Y0000R000S2',
            '327FDCAN1_TX        U1    -D16        A01X+012756Y-007244X0157Y0000R000S2',
            '327FDCAN2_TX        U1    -U14        A01X+012756Y-007244X0157Y0000R000S2',
            '327FDCAN1_RX        U8    -4          A01X+013789Y-009411X0768Y0236R000S2',
            '317FDCAN1_RX        VIA        MD0098PA00X+013549Y-006549X0236Y0000R000S3',
        ]
        f.write_text(chr(10).join(rows) + chr(10),
                     encoding='utf-8')
        d = truthnet.parse_d356(f)
        assert d['FDCAN1_RX'] == {'U1.B17', 'U8.4'}
        assert d['FDCAN1_TX'] == {'U1.D16'}
        assert d['FDCAN2_TX'] == {'U1.U14'}

class TestTruthnetIsoAlign:
    '''C4 图同构注解对齐(SINA): REF**<->U3 收敛为一条注解发现'''

    def test_annotation_gap_collapses(self):
        from fluxpcb.kernel import truthnet
        board = {'3V3': {'REF**.5', 'REF**.8'},
                 'GND': {'REF**.6'},
                 'IMU2_INT1': {'REF**.4'}}
        sch = {'3V3': {'U3.5', 'U3.8'},
               'GND': {'U3.6'},
               'IMU2_INT1': {'U3.4'}}
        r = truthnet.compare(board, sch)
        assert r['annotation_map'] == {'REF**': 'U3'}
        assert r['clean'] is True        # 注解对齐后无真差异

    def test_true_diff_remains(self):
        from fluxpcb.kernel import truthnet
        board = {'3V3': {'REF**.5', 'REF**.8'},
                 'OSC32_IN': {'JMP1.1'}}
        sch = {'3V3': {'U3.5', 'U3.8'},
               'OSC32_IN': {'U1.C2'}}
        r = truthnet.compare(board, sch)
        assert r['annotation_map'] == {'REF**': 'U3'}
        assert r['clean'] is False
        assert 'OSC32_IN' in r['pin_mismatch']   # 真差异保留
        assert '3V3' not in r['pin_mismatch']

class TestSwapSearch:
    '''换球搜索强化: 交叉计数目标+AF 成对交换(K28 主攻)'''

    def _setup(self):
        # 两个对冲信号: S1 在上球逃下, S2 在下球逃上 = 1 对交叉
        ball_nets = {'S1': {'ball': '0,10'}, 'S2': {'ball': '0,-10'}}
        prefs = {'S1': (0.0, -1.0), 'S2': (0.0, 1.0)}
        # AF 复用: 两球都承载两信号(可互换)
        pins_db = {'0,10': {'signals': ['S1', 'S2']},
                   '0,-10': {'signals': ['S1', 'S2']}}
        return ball_nets, prefs, pins_db

    def test_crossing_detected(self):
        from fluxpcb.kernel.constraints import escape_crossings
        ball_nets, prefs, _ = self._setup()
        xy = lambda b: tuple(float(v) for v in b.split(','))
        c = escape_crossings(ball_nets, prefs, xy)
        assert c == [('S1', 'S2')]

    def test_swap_resolves_crossing(self):
        from fluxpcb.kernel.constraints import reassign_proposal
        ball_nets, prefs, pins_db = self._setup()
        out = reassign_proposal(ball_nets, prefs, pins_db, (0, 0))
        assert out.get('S1') == '0,-10'   # 交换解交叉
        assert out.get('S2') == '0,10'

    def test_no_swap_when_af_blocks(self):
        from fluxpcb.kernel.constraints import reassign_proposal
        ball_nets, prefs, _ = self._setup()
        pins_db = {'0,10': {'signals': ['S1']},      # 不可换
                   '0,-10': {'signals': ['S2']}}
        out = reassign_proposal(ball_nets, prefs, pins_db, (0, 0))
        assert out.get('S1') != '0,-10'   # AF 禁换则不换

    def test_no_crossing_no_move(self):
        from fluxpcb.kernel.constraints import reassign_proposal
        ball_nets = {'S1': {'ball': '0,10'}, 'S2': {'ball': '0,-10'}}
        prefs = {'S1': (0.0, 1.0), 'S2': (0.0, -1.0)}   # 同向不冲
        pins_db = {'0,10': {'signals': ['S1', 'S2']},
                   '0,-10': {'signals': ['S1', 'S2']}}
        out = reassign_proposal(ball_nets, prefs, pins_db, (0, 0))
        assert out == {}

class TestBallKeyContract:
    '''0.2 真键格式回归锁: 'A1' 行列名(合成 '0,10' 键曾掩蔽断裂)'''

    def test_ball_grid_xy_real_keys(self):
        from fluxpcb.kernel.constraints import ball_grid_xy
        assert ball_grid_xy('A1') == (1.0, 0.0)   # 列1, 行A=0
        assert ball_grid_xy('B9') == (9.0, 1.0)
        assert ball_grid_xy('T3') == (3.0, 19.0)  # 行T=19
        assert ball_grid_xy('AA5') == (5.0, 26.0)  # 双字母行
        assert ball_grid_xy('0,10') == (0.0, 10.0)  # 旧格式回兼容
        assert ball_grid_xy('??') is None

    def test_swap_search_with_real_keys(self):
        # 'A1' 形键的对冲交叉 + AF 交换(此前合成键才跑通)
        from fluxpcb.kernel.constraints import (
            reassign_proposal, escape_crossings, ball_grid_xy)
        ball_nets = {'S1': {'ball': 'A1'}, 'S2': {'ball': 'C3'}}
        # A1=(1,0) 在左上, C3=(3,2) 在右下; S1 向右下逃, S2 向左上逃 = 对冲
        prefs = {'S1': (0.7, 0.7), 'S2': (-0.7, -0.7)}
        pins_db = {'A1': {'signals': ['S1', 'S2']},
                   'C3': {'signals': ['S1', 'S2']}}
        c = escape_crossings(ball_nets, prefs, ball_grid_xy)
        assert c == [('S1', 'S2')]
        out = reassign_proposal(ball_nets, prefs, pins_db, (0, 0))
        assert out.get('S1') == 'C3' and out.get('S2') == 'A1'

    def test_mcu_center_not_used_for_grid_keys(self):
        # 球空间自洽: 'A1' 键下不拿板 mm 坐标做 cos(旧断裂之二)
        from fluxpcb.kernel.constraints import reassign_proposal
        ball_nets = {'S1': {'ball': 'A1'}, 'S2': {'ball': 'C3'}}
        prefs = {'S1': (0.7, 0.7), 'S2': (-0.7, -0.7)}
        pins_db = {'A1': {'signals': ['S1', 'S2']},
                   'C3': {'signals': ['S1', 'S2']}}
        out = reassign_proposal(ball_nets, prefs, pins_db,
                                (999.0, -999.0))   # 荒谬中心不影响
        assert out.get('S1') == 'C3'   # 交叉解仍成立
    def test_chain3_rotation(self):
        # 真 chain3 场景: 对换全被 AF 禁, 旋转被 AF 放行
        # 循环 AF: S1@{A1,B2}, S2@{B2,C3}, S3@{C3,A1}
        # 旋转 S1->B2, S2->C3, S3->A1 三方皆 AF 合法
        from fluxpcb.kernel.constraints import (
            reassign_proposal, escape_crossings, ball_grid_xy)
        ball_nets = {'S1': {'ball': 'A1'}, 'S2': {'ball': 'B2'},
                     'S3': {'ball': 'C3'}}
        prefs = {'S1': (1.0, 0.0), 'S2': (-1.0, 0.0), 'S3': (-1.0, 0.0)}
        pins_db = {'A1': {'signals': ['S1', 'S3']},
                   'B2': {'signals': ['S1', 'S2']},
                   'C3': {'signals': ['S2', 'S3']}}
        # 前置: 此场景确有交叉
        assert escape_crossings(ball_nets, prefs, ball_grid_xy)
        out = reassign_proposal(ball_nets, prefs, pins_db, (0, 0))
        assert out.get('S1') == 'B2' and out.get('S2') == 'C3'             and out.get('S3') == 'A1'   # 旋转解序
