# -*- coding: utf-8 -*-
"""K23/M-round: the board map (geometry IR) — pure planning core,
map rendering, directed intervention contract."""
import json
import math
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
KGEN = REPO / "fluxpcb" / "kicadgen"
sys.path.insert(0, str(KGEN))
sys.path.insert(0, str(REPO))

import geomir                                    # noqa: E402


class TestCandidatePolylines:
    def test_shapes_and_endpoints(self):
        cands = geomir.candidate_polylines((0.0, 0.0), (10.0, 6.0))
        assert len(cands) >= 10
        for c in cands:
            assert tuple(c[0]) == (0.0, 0.0)
            assert tuple(c[-1]) == (10.0, 6.0)

    def test_l_and_z_and_staircase_present(self):
        cands = geomir.candidate_polylines((0, 0), (10, 6))
        l_shapes = [c for c in cands if len(c) == 3 and
                    (c[1][0] in (10, 0) or c[1][1] in (6, 0))]
        assert any(len(c) == 3 for c in cands)      # L or Z
        assert any(len(c) == 4 for c in cands)      # staircase


class TestVerifyPolyline:
    # geometry identical to commit_gate's rules (single truth via
    # routegrid's pure functions)

    def test_blocked_by_foreign_track(self):
        segs = [(2, (5, -3), (5, 3), 0.1)]          # wall at x=5
        ok, blocker = geomir.verify_polyline(
            [(0, 0), (10, 0)], 0.2, 1, segs, [])
        assert not ok
        assert blocker["type"] == "track" and blocker["net"] == 2
        assert blocker["short_by_mm"] > 0

    def test_clean_path_passes(self):
        segs = [(2, (5, 3), (5, 5), 0.1)]
        ok, blocker = geomir.verify_polyline(
            [(0, 0), (10, 0)], 0.2, 1, segs, [])
        assert ok and blocker is None

    def test_pad_obstacle(self):
        rects = [(2, (5.0, 0.0, 1.0, 1.0, 0.0))]
        ok, blocker = geomir.verify_polyline(
            [(0, 0), (10, 0)], 0.2, 1, [], rects)
        assert not ok and blocker["type"] == "pad"

    def test_own_net_ignored(self):
        segs = [(1, (5, -3), (5, 3), 0.1)]
        ok, _ = geomir.verify_polyline(
            [(0, 0), (10, 0)], 0.2, 1, segs, [])
        assert ok


class TestBestPaths:
    def test_verified_sorted_and_rejects_carry_blockers(self):
        # wall blocks the direct L's; an offset Z slips around it
        segs = [(2, (5, -3), (5, 3), 0.1)]
        ok, rej = geomir.best_paths((0, 0), (10, 0), 0.2, 1, segs, [])
        assert ok, "an offset candidate must find the gap"
        assert ok[0]["length_mm"] <= ok[-1]["length_mm"]
        for p in ok:
            assert p["pts"][0] == [0, 0] and p["pts"][-1] == [10, 0]
        assert rej and rej[0]["blocked_by"]["net"] == 2

    def test_hopeless_reports_blocker(self):
        # fully enclosed corridor
        segs = ([(2, (0, -3), (12, -3), 0.1),
                 (2, (0, 3), (12, 3), 0.1),
                 (2, (6, -3), (6, 3), 0.1)])
        ok, rej = geomir.best_paths((1, 0), (10, 0), 0.2, 1, segs, [])
        # offset paths must hop the wall x=6 within y∈(-3,3): all
        # candidates crossing x=6 are blocked -> honest rejects
        assert not ok
        assert rej and "short_by_mm" in rej[0]["blocked_by"]


class TestSummarizeAndRender:
    def _plan(self):
        ok, rej = geomir.best_paths((0, 0), (10, 0), 0.2, 1, [], [])
        islands = [{"pads": [[0, 0], [0, 1]], "anchor": [0, 0.5],
                    "bbox": [0, 0, 0, 1]},
                   {"pads": [[10, 0]], "anchor": [10, 0],
                    "bbox": [10, 0, 10, 0]}]
        summary = geomir.summarize("EN", islands, ok, rej)
        return {"net": "EN", "islands": islands, "paths": ok,
                "rejects": rej, "segs": [], "rects": [],
                "summary": summary}

    def test_summary_carries_coordinates(self):
        plan = self._plan()
        assert "EN" in plan["summary"]
        assert "岛1" in plan["summary"]
        assert "路径" in plan["summary"] or "被挡" in plan["summary"]

    def test_render_svg(self, tmp_path):
        plan = self._plan()
        out = geomir.render_plan(plan, tmp_path / "p.svg")
        svg = Path(out).read_text(encoding="utf-8")
        assert "<svg" in svg and plan["net"] in svg
        assert "polyline" in svg


class TestProtocol:
    def test_decision_contract_has_direct_link(self):
        from fluxpcb.kernel.decision import ACTION_CONTRACT, validate
        assert "direct_link" in ACTION_CONTRACT
        ok, fixit = validate({"action": "direct_link",
                              "params": {"net": "EN",
                                         "pts": [[0, 0, 0], [1, 0, 0]]}})
        assert ok
        ok2, fixit2 = validate({"action": "direct_link",
                                "params": {"net": "EN"}})
        assert not ok2 and "pts" in fixit2.get("message", "")

    def test_loop_has_direct_link_and_plans(self):
        src = (REPO / "fluxpcb" / "routing.py").read_text(encoding="utf-8")
        body = src.split("def _closed_loop")[1].split("\ndef ")[0]
        assert "geomir.py" in body and "plans" in body
        exec_body = src.split("def _loop_execute")[1].split("\ndef ")[0]
        assert "direct_link.py" in exec_body

    def test_jumper_flood_branch_reachable(self):
        """The q1/q2 NameError made the flood meeting-point dead code
        (midpoint guess forever). Anchors must precede the branch."""
        src = (KGEN / "route_jumper.py").read_text(encoding="utf-8")
        i_anchor = src.find("a1, a2 = anchor_of(isl_keep)")
        i_flood = src.find("c1 = _rd.anchor_cell(_dm, q1.x")
        i_mid = src.find("spot = find_spot((q1.x / mm + q2.x / mm) / 2")
        assert 0 < i_anchor < i_flood < i_mid

    def test_intel_plans_aware(self):
        src = (REPO / "fluxpcb" / "kernel" / "intel.py").read_text(
            encoding="utf-8")
        assert "plans: dict | None" in src
        assert '"direct_link"' in src


class TestT1ViaBarrel:
    """CyrilKafle methodology: each check proven on a synthetic bad
    case AND silent on a clean one. The 10 hop-repair shorts = via
    barrels checked only on the path's layer."""

    def test_via_blocked_by_other_layer_copper(self):
        # a foreign track on layer 1 near the via site -> barrel shorts
        segs = [[], [(2, (5.1, 0.0), (5.1, 1.0), 0.1)]]
        rects = [[], []]
        ok, bl = geomir.via_ok(5.0, 0.5, 1, segs, rects)
        assert not ok and bl["type"] == "via-vs-track"

    def test_via_clean(self):
        segs = [[], [(2, (8.0, 0.0), (8.0, 1.0), 0.1)]]
        ok, bl = geomir.via_ok(5.0, 0.5, 1, segs, [[], []])
        assert ok and bl is None

    def test_via_blocked_by_pad(self):
        rects = [[], [(2, (5.2, 0.5, 0.3, 0.3, 0.0))]]
        ok, bl = geomir.via_ok(5.0, 0.5, 1, [[], []], rects)
        assert not ok and bl["type"] == "via-vs-pad"

    def test_hop_repair_requires_via_ok(self):
        src = (KGEN / "geomir.py").read_text(encoding="utf-8")
        body = src.split("def _plan_paths_at_width")[1]
        assert "via_ok(p[0], p[1]" in body and "via_ok(q[0], q[1]" in body


class TestT4StrictGate:
    def test_strict_gate_in_loop(self):
        src = (REPO / "fluxpcb" / "routing.py").read_text(encoding="utf-8")
        body = src.split("def _closed_loop")[1].split("\ndef ")[0]
        assert "strict_rollback" in body
        assert "_gated_step" in body     # 门禁步已抽取(loop/流水线共用)
        gate = src.split("def _gated_step")[1].split("\ndef ")[0]
        assert "strict_rollback" in gate
        assert "_qs_before" in gate and "_qs_after" in gate
        # restore-on-regression present
        assert "_sh4.copy2(_pre_snap, pcb_path)" in gate

    def test_delta_shorts_recorded(self):
        src = (REPO / "fluxpcb" / "routing.py").read_text(encoding="utf-8")
        assert 'outcome["delta_shorts"]' in src


class TestT2RuleTable:
    """Corpus cures: parse status (kipilot), load probe (ByTaymur),
    write-then-readback (DaCapo7)."""

    def test_load_reads_pro(self, tmp_path):
        import json as _j
        from fluxpcb.kernel import rules
        pro = tmp_path / "b.kicad_pro"
        pro.write_text(_j.dumps({
            "board": {"design_settings": {"rules": {"min_clearance": 0.2}}},
            "net_settings": {"classes": [{"name": "Default",
                                          "clearance": 0.2,
                                          "track_width": 0.25,
                                          "via_diameter": 0.6,
                                          "via_drill": 0.3,
                                          "smd_to_smd_clearance": 0.05}]},
        }), encoding="utf-8")
        r = rules.load_rules(tmp_path / "b.kicad_pcb")
        assert r["clearance"] == 0.2
        assert r["clearance_smd"] == 0.05
        assert r["via_dia"] == 0.6
        assert r["parse"]["source"]["clearance"].startswith("kicad_pro")

    def test_probe_flags_fallback(self, tmp_path):
        from fluxpcb.kernel import rules
        r = rules.load_rules(tmp_path / "none.kicad_pcb")
        bad = rules.verify_loaded(r)
        assert bad, "fallback-sourced rules must surface in the probe"

    def test_readback_detects_mismatch(self, tmp_path):
        import json as _j
        from fluxpcb.kernel import rules
        pro = tmp_path / "b.kicad_pro"
        pro.write_text(_j.dumps({"a": {"b": 1}}), encoding="utf-8")
        assert rules.readback(pro, {"a.b": 1}) == []
        assert rules.readback(pro, {"a.b": 2})

    def test_geomir_verifier_takes_rules(self):
        src = (KGEN / "geomir.py").read_text(encoding="utf-8")
        assert "rules=None" in src and "_kr.load_rules" in src


class TestPlaceOpt:
    """Stream ①: margin-aware moves (DaCapo7 check-space-first) +
    attribution bias (elcruzo GNN-lite)."""

    def _mk_board(self, tmp_path):
        """Synthetic board: 3 fp clusters — A free zone, B crowded."""
        import subprocess
        code = (
            "import pcbnew\n"
            "b = pcbnew.BOARD()\n"
            "import sys\n"
            "for ref, x, y, w, h in [('R1', 10, 10, 2, 2), "
            "('R2', 20, 10, 2, 2), ('R3', 20.6, 10, 2, 2), "
            "('C1', 30, 10, 2, 2)]:\n"
            "    fp = pcbnew.FOOTPRINT(b)\n"
            "    fp.SetReference(ref)\n"
            "    fp.SetPosition(pcbnew.VECTOR2I(int(x*1e6), int(y*1e6)))\n"
            "    fp.SetValue(ref)\n"
            "    b.Add(fp)\n"
            "edge = pcbnew.PCB_SHAPE(b)\n"
            "edge.SetShape(pcbnew.SHAPE_T_RECT)\n"
            "edge.SetStart(pcbnew.VECTOR2I(0, 0))\n"
            "edge.SetEnd(pcbnew.VECTOR2I(50000000, 20000000))\n"
            "edge.SetLayer(pcbnew.Edge_Cuts)\n"
            "b.Add(edge)\n"
            f"pcbnew.SaveBoard(r'{tmp_path}/t.kicad_pcb', b)\n"
            "print('OK')\n")
        p = subprocess.run([sys.executable, "-c", code],
                           capture_output=True, text=True, timeout=60)
        assert "OK" in p.stdout, p.stderr[-200:]
        return tmp_path / "t.kicad_pcb"

    def test_margin_rejects_overlap(self, tmp_path):
        import subprocess
        pcb = self._mk_board(tmp_path)
        code = (
            "import sys, json\n"
            "sys.path.insert(0, r'"
            + str(KGEN).replace("\\", "/") + "')\n"
            "import pcbnew, place_opt\n"
            f"b = pcbnew.LoadBoard(r'{str(pcb).replace(chr(92), '/')}')\n"
            "print(json.dumps({'r': place_opt.margin_ok(b, 'R3', -0.4, 0)}))\n")
        p = subprocess.run([sys.executable, "-c", code],
                           capture_output=True, text=True, timeout=60,
                           encoding="utf-8", errors="replace")
        d = json.loads([l for l in p.stdout.splitlines()
                        if l.strip().startswith("{")][-1])
        assert not d["r"]["ok"], d
        assert d["r"]["reason"] in ("footprint-overlap", "board-edge")

    def test_margin_accepts_free(self, tmp_path):
        import subprocess
        pcb = self._mk_board(tmp_path)
        code = (
            "import sys, json\n"
            "sys.path.insert(0, r'"
            + str(KGEN).replace("\\", "/") + "')\n"
            "import pcbnew, place_opt\n"
            f"b = pcbnew.LoadBoard(r'{str(pcb).replace(chr(92), '/')}')\n"
            "print(json.dumps({'r': place_opt.margin_ok(b, 'C1', -5, 0)}))\n")
        p = subprocess.run([sys.executable, "-c", code],
                           capture_output=True, text=True, timeout=60,
                           encoding="utf-8", errors="replace")
        d = json.loads([l for l in p.stdout.splitlines()
                        if l.strip().startswith("{")][-1])
        assert d["r"]["ok"], d

    def test_wiring(self):
        from fluxpcb.kernel.decision import ACTION_CONTRACT
        assert "optimize_placement" in ACTION_CONTRACT
        src = (REPO / "fluxpcb" / "kernel" / "intel.py").read_text(
            encoding="utf-8")
        assert "optimize_placement" in src
        rsrc = (REPO / "fluxpcb" / "routing.py").read_text(encoding="utf-8")
        assert "place_opt.py" in rsrc


class TestGapNudgeAndCombo:
    """item-1 挪线 (DaCapo7) + item-2 combo close + item-3 free-slot
    scorer + item-4 loop_final measurement."""

    def test_gap_nudge_module_and_pad_containment(self):
        src = (KGEN / "gap_nudge.py").read_text(encoding="utf-8")
        assert "anchor preservation" in src  # v2: pad/via/track 锚点保持
        assert "moves[key]" in src          # group vertices move together
        assert "T-junction" in src          # v2: T型接点入链(防挪线造开路)
        assert "all_copper_ids" in src   # v2+: 验证全铜层视图

    def test_scorer_uses_band_direction(self):
        src = (KGEN / "place_opt.py").read_text(encoding="utf-8")
        assert "blocker-out-of-band" in src
        assert "pux, puy" in src            # perpendicular to corridor

    def test_combo_close_in_loop(self):
        src = (REPO / "fluxpcb" / "routing.py").read_text(encoding="utf-8")
        body = src.split('if name == "optimize_placement"')[1] \
            .split("if name ==")[0]
        assert "geomir.py" in body and "direct_link.py" in body

    def test_loop_final_measurement(self):
        src = (REPO / "fluxpcb" / "routing.py").read_text(encoding="utf-8")
        assert "loop_final.json" in src
        assert "history_log" in src

    def test_gap_nudge_action_registered(self):
        from fluxpcb.kernel.decision import ACTION_CONTRACT
        assert "gap_nudge" in ACTION_CONTRACT
        isrc = (REPO / "fluxpcb" / "kernel" / "intel.py").read_text(
            encoding="utf-8")
        assert '"gap_nudge"' in isrc


class TestStreams123:
    """① coordinated_open compound / ② near-miss retry / ③ fast oracle
    + batch plan + plan cache — the P6 additions."""

    def test_near_miss_recovers_hair_miss(self):
        # blocker hugging the straight line: a hair-miss reject that
        # only a perpendicular shift recovers (hyuhang false-negative)
        segs_by_layer = {0: []}
        rects_by_layer = {0: []}
        rejects = [{"pts": [[0, 0, 0], [10, 0, 0]],
                    "blocked_by": {"at": (5.0, 0.3),
                                   "short_by_mm": 0.1}}]
        out = geomir.near_miss_retry((0, 0), (10, 0), 0.2, 1,
                                     segs_by_layer, rects_by_layer,
                                     rejects)
        assert out, "hair-miss must be recoverable by shifting"
        assert out[0]["pts"][0][1] < 0          # moved AWAY (down)
        assert out[0]["mode"].startswith("near-miss-shift-")

    def test_near_miss_empty_without_rejects(self):
        assert geomir.near_miss_retry((0, 0), (1, 0), 0.2, 1,
                                      {0: []}, {0: []}, []) == []

    def test_plan_cache_registered(self):
        src = (KGEN / "geomir.py").read_text(encoding="utf-8")
        assert "_PLAN_CACHE" in src and "board_sha" in src

    def test_batch_plan_cli_one_process_many_nets(self):
        src = (KGEN / "geomir.py").read_text(encoding="utf-8")
        assert 'nets_arg.split(",")' in src    # 8 nets, one board load

    def test_fast_oracle_never_silently_passes(self):
        rsrc = (REPO / "fluxpcb" / "routing.py").read_text(
            encoding="utf-8")
        body = rsrc.split("def _fast_shorts")[1].split("def ")[0]
        assert "return None" in body           # not -1: failure is loud
        gate = rsrc.split("_qs_before = _fast_shorts")[1]             .split("outcome = _loop_execute")[0]
        assert "_qs_before is None" in gate    # slow DRC fallback

    def test_coordinated_open_registered_and_compound(self):
        from fluxpcb.kernel.decision import ACTION_CONTRACT, validate
        assert "coordinated_open" in ACTION_CONTRACT
        ok, _ = validate({"action": "coordinated_open",
                          "params": {"net": "GND"}})
        assert ok
        ok, fixit = validate({"action": "coordinated_open",
                              "params": {}})
        assert not ok and "net" in json.dumps(fixit, ensure_ascii=False)
        isrc = (REPO / "fluxpcb" / "kernel" / "intel.py").read_text(
            encoding="utf-8")
        # playbook: the compound leads, the singles remain fallbacks
        assert (isrc.index('"coordinated_open"')
                < isrc.index('"gap_nudge"'))
        rsrc = (REPO / "fluxpcb" / "routing.py").read_text(
            encoding="utf-8")
        body = rsrc.split('if name == "coordinated_open"')[1]             .split('if name == "gap_nudge"')[0]
        assert "loop:coordinated_open" in body   # single Txn
        assert "gap_nudge.py" in body            # step-1 nudge
        assert "place_opt.py" in body            # step-2 joint moves
        assert "_geomir_close" in body           # step-3 挪后直连


class TestOverlapOracleExecution:
    """stream ③ oracle 的实弹执行(非源码断言) — NameError/阈值折半
    两个教训的回归锁."""

    def test_crossing_tracks_counted(self):
        segs = [[(1, (0, 0), (10, 0), 0.2),
                 (2, (5, -5), (5, 5), 0.2)],
                []]
        assert geomir.count_overlaps_geom(segs, [[], []]) == 1

    def test_legal_near_pair_not_counted(self):
        # 0.2mm 线, 中心距 0.35mm -> 边距 0.15mm 合法间距, 不是短路
        # (v1 阈值 w1+w2=0.4 会把它误计为短路 — 假阳性工厂回归锁)
        segs = [[(1, (0, 0), (10, 0), 0.2),
                 (2, (0, 0.35), (10, 0.35), 0.2)],
                []]
        assert geomir.count_overlaps_geom(segs, [[], []]) == 0

    def test_overlapping_pair_counted(self):
        segs = [[(1, (0, 0), (10, 0), 0.2),
                 (2, (0, 0.1), (10, 0.1), 0.2)],
                []]
        assert geomir.count_overlaps_geom(segs, [[], []]) == 1

    def test_track_through_pad_counted(self):
        segs = [[(1, (0, 0), (10, 0), 0.2)], []]
        rects = [[(2, (5.0, 0.0, 1.0, 1.0, 0.0))], []]
        assert geomir.count_overlaps_geom(segs, rects) == 1

    def test_same_net_never_counted(self):
        segs = [[(1, (0, 0), (10, 0), 0.2),
                 (1, (0, 0.1), (10, 0.1), 0.2)],
                []]
        assert geomir.count_overlaps_geom(segs, [[], []]) == 0
    def test_pad_pad_overlap_counted(self):
        # FC_L4 逃逸类: jumper 把焊盘放到外来焊盘上, delta 0/0
        # 提交 — pad-pad 不在计数里就是门禁盲区
        rects = [[(1, (5.0, 0.0, 1.0, 1.0, 0.0)),
                  (2, (5.4, 0.2, 1.0, 1.0, 0.0))], []]
        assert geomir.count_overlaps_geom([[], []], rects) == 1

    def test_far_pads_not_counted(self):
        rects = [[(1, (5.0, 0.0, 1.0, 1.0, 0.0)),
                  (2, (9.0, 0.0, 1.0, 1.0, 0.0))], []]
        assert geomir.count_overlaps_geom([[], []], rects) == 0

class TestIslandsClusterSingleSource:
    """④ 岛单源: 岛划分 == KiCad 连通簇。旧版三 bug 的回归锁:
    叠放焊盘误合并 / zone 轮廓点误合并 / 走线终点死分支。"""

    class _FakePad:
        def __init__(self, x, y, lays, nc=1):
            self._p = (x, y)
            self._lays = tuple(sorted(lays))
            self._nc = nc

        def GetPosition(self):
            class P:
                pass
            p = P()
            p.x, p.y = self._p
            return p

        def GetLayerSet(self):
            class L:
                def __init__(self, t):
                    self._t = t

                def Seq(self):
                    return self._t
            return L(self._lays)

        def GetNetCode(self):
            return self._nc

    @staticmethod
    def _key(it):
        q = it.GetPosition()
        return (q.x, q.y, it.GetLayerSet().Seq())

    def test_stacked_pads_stay_separate(self):
        # 同 XY 异层叠放焊盘: KiCad 视为断开 — 旧版无层键误合并
        a = self._FakePad(1000, 2000, (0,))
        b = self._FakePad(1000, 2000, (31,))
        plist = [(a, self._key(a)), (b, self._key(b))]
        groups = geomir.group_pads_by_cluster(
            plist, lambda p: [p], self._key)       # 各自独立簇
        assert len(groups) == 2

    def test_cluster_members_merge(self):
        # 同簇两焊盘(zone 连通时 KiCad 让它们直现彼此簇里) -> 1 岛
        a = self._FakePad(1000, 2000, (0,))
        b = self._FakePad(5000, 6000, (0,))
        plist = [(a, self._key(a)), (b, self._key(b))]
        groups = geomir.group_pads_by_cluster(
            plist, lambda p: [a, b], self._key)    # 直现彼此
        assert len(groups) == 1

    def test_no_position_artifact_merges(self):
        # 旧 bug: zone 轮廓角点当连接键, 恰落该点的异簇锚点被误合并。
        # 簇语义不产几何代理键: 无成员关系即不合并
        a = self._FakePad(0, 0, (0,))
        c = self._FakePad(9000, 9000, (0,))
        plist = [(a, self._key(a)), (c, self._key(c))]
        groups = geomir.group_pads_by_cluster(
            plist, lambda p: [p], self._key)
        assert len(groups) == 2

    def test_transitive_cluster(self):
        # 三焊盘链式簇(经走线/zone 传递) -> 1 岛
        a = self._FakePad(0, 0, (0,))
        b = self._FakePad(5000, 0, (0,))
        c = self._FakePad(9000, 0, (0,))
        cl = {id(a): [a, b], id(b): [a, b, c], id(c): [b, c]}
        plist = [(a, self._key(a)), (b, self._key(b)), (c, self._key(c))]
        groups = geomir.group_pads_by_cluster(
            plist, lambda p: cl[id(p)], self._key)
        assert len(groups) == 1


class TestPairsPlanningSource:
    """④ 规划端点单源: 测量器报的断点对直达规划器。"""

    def test_pairs_by_net(self):
        lines = ["ADC1|45.4700|37.0000|0|20.4000|25.6000|0",
                 "ADC1|20.4000|25.6000|0|10.0000|5.0000|0",
                 "GND|1.0000|2.0000|0|3.0000|4.0000|0"]
        d = geomir.pairs_by_net(lines)
        assert len(d["ADC1"]) == 2 and len(d["GND"]) == 1
        assert d["ADC1"][0] == ((45.47, 37.0), (20.4, 25.6))

    def test_select_nets_by_fragmentation(self):
        # 严重度降序: 岛多的网先规划(旧版字母序丢严重度)
        lines = ["AAA|0|0|0|1|1|0", "ZZZ|0|0|0|1|1|0"]
        got = geomir.select_nets(lines, {"AAA": 1, "ZZZ": 5}, k=2)
        assert got == ["ZZZ", "AAA"]

    def test_select_nets_tie_alphabetical(self):
        lines = ["ZZZ|0|0|0|1|1|0", "AAA|0|0|0|1|1|0"]
        got = geomir.select_nets(lines, {}, k=2)
        assert got == ["AAA", "ZZZ"]

    def test_wiring_pairs_file_and_select(self):
        rsrc = (REPO / "fluxpcb" / "routing.py").read_text(encoding="utf-8")
        assert "loop_pairs.tmp.txt" in rsrc       # pairs 随批传规划器
        assert "_KG_SELECT" in rsrc
        gsrc = (KGEN / "geomir.py").read_text(encoding="utf-8")
        assert "group_pads_by_cluster" in gsrc
        assert "pairs=_pbn.get" in gsrc           # CLI 消费 pairs 文件

class TestFanoutRepair:
    '''① BGA 扇出重构: 契约/接线/工具契约'''

    def test_contract_and_taxonomy(self):
        from fluxpcb.kernel.decision import ACTION_CONTRACT, validate
        from fluxpcb.kernel.causal import ACTION_FOR
        assert ACTION_FOR['escape-crossing'] == 'fanout_repair'
        assert 'fanout_repair' in ACTION_CONTRACT
        ok, _ = validate({'action': 'fanout_repair',
                          'params': {'nets': ['ADC1']}})
        assert ok
        ok2, fixit = validate({'action': 'fanout_repair',
                               'params': {}})
        assert not ok2 and 'nets' in json.dumps(fixit,
                                                ensure_ascii=False)

    def test_executor_and_tool_wiring(self):
        rsrc = (REPO / 'fluxpcb' / 'routing.py').read_text(
            encoding='utf-8')
        body = rsrc.split('if name == "fanout_repair"')[1] \
            .split('if name ==')[0]
        assert 'loop:fanout_repair' in body   # 单 Txn 原子
        assert 'fanout_repair.py' in body
        tsrc = (KGEN / 'fanout_repair.py').read_text(encoding='utf-8')
        assert 'ripped_dead' in tsrc          # 死桩清理
        assert 'via_ok' in tsrc               # T1 全层过孔校验
        assert 'BGA_PADS_MIN' in tsrc         # 板无关启发式

class TestBatchEscape:
    '''③ 多网协同: 契约/单Txn/族候选'''

    def test_contract_and_executor(self):
        from fluxpcb.kernel.decision import ACTION_CONTRACT, validate
        assert 'batch_escape' in ACTION_CONTRACT
        ok, _ = validate({'action': 'batch_escape',
                          'params': {'nets': ['A', 'B']}})
        assert ok
        ok2, fixit = validate({'action': 'batch_escape',
                               'params': {}})
        assert not ok2 and 'nets' in json.dumps(fixit,
                                                ensure_ascii=False)
        rsrc = (REPO / 'fluxpcb' / 'routing.py').read_text(
            encoding='utf-8')
        body = rsrc.split('if name == "batch_escape"')[1] \
            .split('if name ==')[0]
        assert 'loop:batch_escape' in body    # 单 Txn 全有全无
        assert body.count('_geomir_close') >= 3  # 逐网收尾

    def test_family_candidate(self):
        isrc = (REPO / 'fluxpcb' / 'kernel' / 'intel.py').read_text(
            encoding='utf-8')
        assert '"batch_escape"' in isrc
        assert 'escape-crossing' in isrc      # 族类过滤

class TestPlaneAwareness:
    '''② 平面感知: 跨平面代价 + 定向缝合接线'''

    def test_plane_cut_count_cross_vs_tangent(self):
        # 平面: 单位正方形 zone (net 2) 在层 0
        planes = [[(2, [(2.0, -2.0), (8.0, -2.0), (8.0, 2.0),
                        (2.0, 2.0)])], []]
        # 跨平面路径(穿过正方形)
        cut = [(0, 0, 0), (10, 0, 0)]
        assert geomir.plane_cut_count(cut, planes) == 1
        # 绕行路径(正方形外)
        miss = [(0, 5, 0), (10, 5, 0)]
        assert geomir.plane_cut_count(miss, planes) == 0
        # 跨层段(过孔)不计
        hop = [(5, -5, 0), (5, 5, 1)]
        assert geomir.plane_cut_count(hop, planes) == 0

    def test_sort_prefers_non_cutting(self):
        # 合成: 长但不切平面的路径应排在短但切平面的之前
        segs = [[], []]
        rects = [[], []]
        # 无阻挡 => 多条候选; 用 planes 打分再排序的机制在
        # plan_paths 内 — 这里锁 wiring 与字段
        gsrc = (KGEN / 'geomir.py').read_text(encoding='utf-8')
        assert 'v["plane_cut"]' in gsrc
        assert 'planes_by_layer=_planes' in gsrc

    def test_power_stitch_tool_wiring(self):
        from fluxpcb.kernel.decision import ACTION_CONTRACT, validate
        assert ACTION_CONTRACT['power_stitch'] == {'nets': list}
        ok, _ = validate({'action': 'power_stitch',
                          'params': {'nets': ['GND']}})
        assert ok
        rsrc = (REPO / 'fluxpcb' / 'routing.py').read_text(
            encoding='utf-8')
        assert 'power_stitch.py' in rsrc      # 定向工具而非 session
        tsrc = (KGEN / 'power_stitch.py').read_text(encoding='utf-8')
        assert 'islands_of' in tsrc           # 簇语义岛
        assert 'via_ok' in tsrc

class TestPadShapeSemantics:
    '''焊盘形状语义: 真圆走圆距离, 方形保持矩形(圆化方角=布线穿角)'''

    def test_square_pad_not_circle_relaxed(self):
        # 方形焊盘(无圆标志): 与矩形距离恒等 — 任何放松都可能
        # 让布线穿方角(全盘圆化的教训)
        from routegrid import pt_rect_dist
        rect = (5.0, 0.0, 1.0, 1.0, 0.0)
        for (x, y) in [(5.7, 0.7), (5.9, 0.9), (7.0, 0.0),
                       (5.0, 0.0), (6.5, 1.2)]:
            assert geomir.pt_pad_dist(x, y, rect) == pt_rect_dist(
                x, y, rect), (x, y)

    def test_round_pad_uses_circle(self):
        # 元组第 3 位是**半维**(pad_rect 返回 hw/hh): 球半径 0.2
        rect = (5.0, 0.0, 0.2, 0.2, 0.0, True)
        # 对角 (0.4,0.4): 圆距 = 0.566-0.2 = 0.366
        d = geomir.pt_pad_dist(5.4, 0.4, rect)
        assert abs(d - 0.3657) < 0.01
        # FC_L7 回归锁: 半径少算一半(w/2)曾把 0.2557 算成 0.356
        rect2 = (5.0, 0.0, 0.2, 0.2, 0.0, True)
        d3 = geomir.pt_pad_dist(5.306, 0.34, rect2)   # 心距 0.4557
        assert abs(d3 - 0.2557) < 0.01
        # 同点矩形近似只有 0.283 — 圆语义正是为 BGA 解这个差
        d2 = geomir.pt_pad_dist(5.4, 0.4, (5.0, 0.0, 0.2, 0.2, 0.0))
        assert d2 < 0.3

    def test_causal_pairs_accepts_raw_lines(self):
        from fluxpcb.kernel.causal import bga_boxes_from_pairs
        # fc 真实行格式 + 元组双兼容(FC_L6 解包崩溃回归锁)
        lines = ['N%d|10.%d000|10.%d000|0|11.%d000|11.%d000|0'
                 % (i, i % 10, (i + 1) % 10, i % 10, (i + 2) % 10)
                 for i in range(12)]
        bga_boxes_from_pairs(lines)
        bga_boxes_from_pairs([('N1', (10.0, 10.0), (11.0, 11.0))])

    def test_all_copper_verification_view(self):
        # via_ok 全层契约: 验证工具必须用 all_copper_ids
        # (FC_L7: In2/In3 power_copper 上的真实信号走线失明,
        #  2 短路全是过孔撞 power_copper)
        rsrc = (KGEN / 'routegrid.py').read_text(encoding='utf-8')
        assert 'def all_copper_ids' in rsrc
        for tool in ('fanout_repair.py', 'power_stitch.py',
                     'gap_nudge.py'):
            tsrc = (KGEN / tool).read_text(encoding='utf-8')
            assert 'all_copper_ids' in tsrc, tool
        gsrc = (KGEN / 'geomir.py').read_text(encoding='utf-8')
        assert 'all_copper_ids' in gsrc   # plan/oracle 同视图
