# -*- coding: utf-8 -*-
"""六条反例引理回归(阶段 0 固定测试种子的机检部分).

每条对应一次实测契约断裂 —— 全部用真数据键格式(球位键断裂教训:
合成键曾掩蔽断裂), 防同类复发:
  1. ball_key_dual_format      —— 真键 'A1' 行列名, 兼容 'x,y'
  2. pairs_col_contract        —— net|x|y|l|x|y|l 端点在 (1,2)/(4,5)
  3. layer_index_single_source —— CuStack 序(0=F..last=B), 板级
  4. rules_table_single_source —— 规则表不采信 flux_* per-pair 规则
  5. plan_width_eq_draw_width  —— 验你所发: 圆整后坐标不过验=不发
  6. endpoint_layer_reachable  —— 端点三态(同层/借道/附着孔), 源级锁
"""
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
KGEN = REPO / "fluxpcb" / "kicadgen"
sys.path.insert(0, str(KGEN))
sys.path.insert(0, str(REPO))

PCB = REPO / "projects" / "h743-fc" / "pcb" / "board" / "h743-fc.kicad_pcb"

try:
    import pcbnew                     # noqa: F401
    HAS_PCBNEW = True
except Exception:
    HAS_PCBNEW = False


# ── 1. 球位键双格式(真键 'A1', 合成键掩蔽断裂案) ──────────────────
class TestBallKeyDualFormat:
    def test_real_grid_key(self):
        from fluxpcb.kernel import constraints
        # mcu_db 真键: 行字母+列号, 行号向下与板 y 同向
        assert constraints.ball_grid_xy("A1") == (1.0, 0.0)
        assert constraints.ball_grid_xy("D12") == (12.0, 3.0)

    def test_numeric_compat_key(self):
        from fluxpcb.kernel import constraints
        assert constraints.ball_grid_xy("3,10") == (3.0, 10.0)

    def test_bad_key_returns_none(self):
        from fluxpcb.kernel import constraints
        assert constraints.ball_grid_xy("?") is None
        assert constraints.ball_grid_xy("") is None


# ── 2. pairs 列契约(端点列错位案) ────────────────────────────────
class TestPairsColContract:
    def test_endpoints_at_cols_1_2_and_4_5(self):
        import geomir
        ln = "SDMMC1_CMD|28.4|20.0|0|7.495|7.4|0"
        out = geomir.pairs_by_net([ln])
        assert out["SDMMC1_CMD"] == [((28.4, 20.0), (7.495, 7.4))]

    def test_malformed_lines_dropped(self):
        import geomir
        out = geomir.pairs_by_net(["net|1|2|3", ""])
        assert out == {}


# ── 3. 层索引单源(CuStack 序, 板级) ─────────────────────────────
@pytest.mark.skipif(not HAS_PCBNEW, reason="needs KiCad python")
class TestLayerIndexSingleSource:
    def test_cupstack_order_f_first_b_last(self):
        import pcbnew
        import routegrid as rg
        b = pcbnew.LoadBoard(str(PCB))
        lids = [int(l) for l in rg.all_copper_ids(b)]
        assert lids[0] == int(pcbnew.F_Cu)
        assert lids[-1] == int(pcbnew.B_Cu)
        assert len(lids) == len(b.GetEnabledLayers().CuStack())


# ── 4. 规则表单源(dru 解析不采信 flux_* 豁免规则) ────────────────
class TestRulesTableSingleSource:
    def test_flux_waiver_min_not_global(self, tmp_path):
        from fluxpcb.kernel import rules as kr
        pcb = tmp_path / "t.kicad_pcb"
        pcb.write_text("", encoding="utf-8")
        (tmp_path / "t.kicad_dru").write_text(
            "(version 1)\n"
            '(rule "flux_waiver_A-B_0p1426"\n'
            '  (condition "A.NetName == \'A\'")\n'
            "  (constraint clearance (min 0.1426mm)))\n"
            '(rule "global_clear"\n'
            '  (condition "")\n'
            "  (constraint clearance (min 0.2mm)))\n",
            encoding="utf-8")
        r = kr.load_rules(pcb)
        # flux_* per-pair min 是个别网对工艺声明, 不是全局约束
        assert r["clearance"] == 0.2

    def test_flux_only_dru_keeps_fallback(self, tmp_path):
        from fluxpcb.kernel import rules as kr
        pcb = tmp_path / "t.kicad_pcb"
        pcb.write_text("", encoding="utf-8")
        (tmp_path / "t.kicad_dru").write_text(
            "(version 1)\n"
            '(rule "flux_policy_A-B_0p12"\n'
            '  (condition "")\n'
            "  (constraint clearance (min 0.12mm)))\n",
            encoding="utf-8")
        r = kr.load_rules(pcb)
        assert r["parse"]["source"]["clearance"] != "kicad_dru:clearance"


# ── 5. 验你所发(圆整后坐标不过验 = 不进 verified) ────────────────
class TestPlanWidthEqDrawWidth:
    def test_unrounded_pass_rounded_fail_rejected(self):
        """USART6 3μm 拒案的最小复现: 未圆整过的线擦边过, 圆整后
        差 0.0003 不过 —— 修复后该候选必须落 rejects, 不得进 verified
        (旧版验证未圆整坐标、发出圆整坐标 -> plan 过 execute 拒)。"""
        import geomir
        width = 0.2
        nc = 7
        # 阻塞轨(他网): 水平线 y=-0.3497, hw=0.1 -> 需求间距 0.35
        segs = [[(99, (0.0, -0.3497), (10.0, -0.3497), 0.1)]]
        rects = [[]]
        # 候选: y=0.00049(未圆整), 圆整到 0.0 后贴向阻塞轨 0.0003
        cand = [(0.00049, 0.00049), (10.0, 0.00049)]
        verified, rejects = [], []
        geomir._plan_paths_at_width(
            cand[0], cand[-1], width, nc, segs, rects, 1,
            [cand], verified, rejects, 4)
        assert verified == [], "圆整后不过验的候选不得出库"
        assert rejects, "必须落 rejects 留证"

    def test_emitted_pts_verify_on_own_coords(self):
        import geomir
        width = 0.2
        nc = 7
        segs = [[]]
        rects = [[]]
        cand = [(0.123456, 0.654321), (5.0, 0.654321)]
        verified, rejects = [], []
        geomir._plan_paths_at_width(
            cand[0], cand[-1], width, nc, segs, rects, 1,
            [cand], verified, rejects, 4)
        assert verified, "自由场候选应出库"
        pts = verified[0]["pts"]
        for (x, y, li) in pts:
            assert x == round(x, 3) and y == round(y, 3)
        # 出库坐标自身可过验(验你所发的恒等式)
        ok, _bl = geomir.verify_polyline(
            [(pts[0][0], pts[0][1]), (pts[1][0], pts[1][1])],
            width, nc, segs[0], rects[0])
        assert ok


# ── 6. 端点三态(direct_link 源级锁: 逻辑存在 + 解析先于验证) ─────
class TestEndpointLayerReachable:
    def _src(self):
        return (KGEN / "direct_link.py").read_text(encoding="utf-8")

    def test_three_state_logic_present(self):
        src = self._src()
        assert "endpoint-attach" in src      # ③ 附着过孔
        assert "pad_on" in src               # ① 同层直连判定
        assert "via_pts" in src              # ② 借道/附着统一账

    def test_endpoint_resolution_precedes_reverify(self):
        """端点改写必须发生在验证之前 —— 否则被改段逃出验证覆盖
        (浮空走线 drawn=3 连通不变案的防御)。"""
        src = self._src()
        i_resolve = src.index("端点三态解析")
        i_verify = src.index("re-verification")
        assert i_resolve < i_verify

    def test_layer_id_wraps_negative(self):
        """quiet 层负索引回绕与验证端一致(1-li 在 li>=2 时为负,
        绘制端旧版不回绕 = 验证 bottom 绘制 F 的错层案)。"""
        assert "abs(k) < len(_LIDS)" in self._src()
