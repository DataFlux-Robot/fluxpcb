# -*- coding: utf-8 -*-
"""④ 加工闭环 — fab-margin waiver: 证据随行/写后探针/绝不豁免真违例/
locale 无关解析(真实 KiCad 10 中文 schema)."""
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
KGEN = REPO / "fluxpcb" / "kicadgen"
sys.path.insert(0, str(KGEN))
sys.path.insert(0, str(REPO))

import fab_waiver                                    # noqa: E402


def _viol(req, act, x=1.0, y=2.0, net1="IO10", net2="IO9"):
    return {"type": "clearance",
            "description": "Clearance violation (netclass 'Default' "
                           f"clearance {req:.4f} mm; actual {act:.4f} mm)",
            "items": [{"uuid": "u1", "pos": {"x": x, "y": y},
                       "description": f"Track [{net1}] on F.Cu"},
                      {"uuid": "u2", "pos": {"x": x, "y": y},
                       "description": f"Pad 5 [{net2}] of J2"}]}


_ZH_VIOL = {
    "type": "clearance",
    "description": "间距违规 ( 间距 0.1500 mm; 实际 0.1370 mm)",
    "items": [
        {"description": "走线 [IO10] (bottom_copper), 长度: 25.5400 mm",
         "pos": {"x": 26.722467, "y": 17.597004},
         "uuid": "1a871b3c"},
        {"description": "J2 的PTH 焊盘 5 [IO9]",
         "pos": {"x": 26.67, "y": 16.51},
         "uuid": "7247d41c"}],
    "severity": "error"}


class TestParseClassify:
    def test_parse_english_and_chinese_locale(self):
        [it] = fab_waiver.parse_clearance({"violations": [_viol(0.15, 0.1482)]})
        assert it["required_mm"] == 0.15 and it["actual_mm"] == 0.1482
        # 真实 KiCad 10 中文 schema(P6 truth.json 实录) — 语言无关解析
        [itz] = fab_waiver.parse_clearance({"violations": [_ZH_VIOL]})
        assert itz["required_mm"] == 0.15 and itz["actual_mm"] == 0.137
        assert itz["nets"] == ["IO10", "IO9"]

    def test_near_miss_waived_with_evidence(self):
        [it] = fab_waiver.parse_clearance({"violations": [_viol(0.15, 0.1482)]})
        w, g = fab_waiver.classify([it])
        assert len(w) == 1 and not g
        assert w[0]["verdict"] == "waived-fab-margin"
        assert "fab_min" in w[0]["evidence"]      # 证据随行

    def test_genuine_violation_never_waived(self):
        # actual < fab_min: 不可加工, 照实交付
        [it] = fab_waiver.parse_clearance({"violations": [_viol(0.15, 0.05)]})
        w, g = fab_waiver.classify([it])
        assert not w and len(g) == 1
        assert g[0]["verdict"] == "genuine-violation"

    def test_three_class_separation(self):
        # 真实 P6 案例: 0.137 gap 在工艺带内但弯 13μm 超窗 ->
        # bend-too-big(不是 genuine-violation, 不是 waived)
        [it] = fab_waiver.parse_clearance({"violations": [_ZH_VIOL]})
        w, g = fab_waiver.classify([it])
        assert not w and g[0]["verdict"] == "bend-too-big"
        # 窗内近失(4.8μm) -> waived
        [it2] = fab_waiver.parse_clearance(
            {"violations": [_viol(0.15, 0.1452)]})
        w2, g2 = fab_waiver.classify([it2])
        assert w2 and not g2
        # 短缺超窗且低于工艺能力 -> genuine
        [it3] = fab_waiver.parse_clearance(
            {"violations": [_viol(0.15, 0.05)]})
        w3, g3 = fab_waiver.classify([it3])
        assert not w3 and g3[0]["verdict"] == "genuine-violation"


class TestDruWaivers:
    def test_rule_written_with_evidence_comment(self, tmp_path):
        dru = tmp_path / "b.kicad_dru"
        # 窗内近失(4.8μm) — 真实 ZH schema 形状
        zh = dict(_ZH_VIOL, description="间距违规 ( 间距 0.1500 mm; "
                                        "实际 0.1452 mm)")
        [it] = fab_waiver.parse_clearance({"violations": [zh]})
        w, _ = fab_waiver.classify([it])
        n = fab_waiver.write_waivers(dru, w)
        assert n == 1
        txt = dru.read_text(encoding="utf-8")
        assert "(constraint clearance (min 0.1442mm))" in txt  # 1um 安全边际
        assert "fab_min" in txt                  # 注释带证据
        assert "IO10" in txt and "IO9" in txt    # 网对条件双向
        assert txt.count("IO10") == 3            # 规则名 + A/B 对称条件
        # 幂等: 重复写不重复追加
        assert fab_waiver.write_waivers(dru, w) == 0

    def test_evidenceless_waiver_refused_loudly(self, tmp_path):
        dru = tmp_path / "b.kicad_dru"
        [it] = fab_waiver.parse_clearance({"violations": [_ZH_VIOL]})
        with pytest.raises(ValueError, match="without evidence"):
            fab_waiver.write_waivers(dru, [it])   # 未分类 = 无证据

    def test_probe_contract_is_loud(self):
        # 探针语义: 豁免残留必须报 PROBE_FAILED, 绝不静默
        src = (KGEN / "fab_waiver.py").read_text(encoding="utf-8")
        assert "PROBE_FAILED" in src
        assert "写后探针" in src
        # 真违例绝不进豁免分支
        assert "genuine-violation" in src
        # exclusions 机制已废弃(JSON 无 marker 坐标, 匹配靠不住)
        assert "_exclusion_str" not in src
