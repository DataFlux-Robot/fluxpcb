# -*- coding: utf-8 -*-
"""L2-② 改进规划器: 矩阵驱动的候选生成(RSI 环的选题端).

输入 = ledger 矩阵(残差类x算子战绩) + 失败档案;
输出 = 结构化改进候选(HarnessPro 式强制字段, 含**预注册判据**)。

选题规则(确定性 v1 — 选题不许拍脑袋):
  R1 类级零可达(reachability_gap) -> new_op 候选(原语组合草图)
  R2 零胜格(zero_reach, trials>=2) -> op_variant 候选(参数扫描)
  R3 目标盲区(改善被测不到) -> objective 候选

候选 schema:
  {id, kind: new_op|op_variant|objective, applies_to(cls),
   changes, expected_impact, cost, risk, prereg_criteria}
prereg_criteria = A/B 验证的预注册通过判据(z_ab.py 消费)。

契约:
  读=ledger.json/grind_state; 写=无(纯生成);
  语义前提=每候选必带可机检的 prereg_criteria(禁主观验收);
  LLM 富化可选(离线跳过), 规则层永远确定性。
"""
from __future__ import annotations

from . import ledger as _ledger

# 零可达类 -> new_op 草图(原语组合; 参数待 A/B 扫描)
NEW_OP_SKETCHES = {
    "zone-fragment": {
        "name": "zone_bridge",
        "changes": "平面层铜桥: 用 direct_link 在碎片间画跨切缝"
                   "桥接折线(全铜层校验), 配合 stitch v2 两端钉固",
        "primitives": ["geomir.plan_paths", "direct_link",
                       "power_stitch"],
    },
    # 真实 taxonomy 无 zone-fragment 类; FC_L13 实证的 zone 切割/
    # 口袋墙残差落在 unclassified —— 同解
    "unclassified": {
        "name": "zone_bridge",
        "changes": "zone 铜桥+口袋墙组合: 桥接折线(hop-repair 跨层"
                   "绕切缝) + 切割信号成组重路由让缝",
        "primitives": ["geomir.plan_paths", "direct_link",
                       "reroute_nets"],
    },
    "escape-crossing": {
        "name": "chain_swap",
        "changes": "换球搜索深度扩展: 链式交换候选加宽 + AF 松弛一档",
        "primitives": ["reassign_pins"],
    },
}

# 低胜率算子 -> 参数扫描档(算子参数化面已核)
OP_VARIANTS = {
    "nudge_blocker": {
        "changes": "gap_nudge DIST 扫描 [0.1, 0.15, 0.2, 0.3] "
                   "+ 方向含岛轴对角",
        "params_grid": [{"dist": d} for d in
                        (0.1, 0.15, 0.2, 0.3)]},
    "stitch": {
        "changes": "power_stitch VIA_SIZES 扫描 [0.15/0.075, "
                   "0.12/0.06] + 偏移环加密 12 向",
        "params_grid": [{"via_r": r} for r in (0.15, 0.12)]},
    "harvest": {
        "changes": "direct_link width 扫描 [0.11, 0.15, 0.2]",
        "params_grid": [{"width": w} for w in (0.11, 0.15, 0.2)]},
}


def _prereg(kind: str, cls: str) -> dict:
    """预注册判据(固定评测器, 禁主观 — HarnessPro 教训)。"""
    return {
        "evaluator": "objective+gate(固定, 不随候选改)",
        "pass": "A/B 同残差集: 候选臂均 d_obj > 基线臂 "
                "AND 零回滚 AND 0 新增短路",
        "window": "fc 小窗 N=8 微步; mini 冒烟先行",
        "ab_runs": 2,          # 两轮通过才可采纳
    }


def propose(matrix: dict, gap_classes: list | None = None) -> list:
    """矩阵 -> 改进候选(确定性, 可复现)。"""
    out = []
    # R1: 类级零可达 -> new_op
    gaps = gap_classes if gap_classes is not None else \
        _ledger.reachability_gap(matrix, list(matrix.keys()))
    for g in gaps:
        cls = g["cls"]
        sketch = NEW_OP_SKETCHES.get(cls, {
            "name": cls.replace("_", "-") + "-solver",
            "changes": "该类通用求解器(原语组合待定)",
            "primitives": [],
        })
        out.append({
            "id": "newop-%s" % cls,
            "kind": "new_op",
            "applies_to": cls,
            "changes": sketch["changes"],
            "expected_impact": "填补零可达类(%s)" % g.get("proof", ""),
            "cost": "high",
            "risk": "新原语组合需全量几何校验",
            "prereg_criteria": _prereg("new_op", cls),
            "sketch": sketch,
        })
    # R2: 零胜格 -> op_variant
    for cell in _ledger.zero_reach(matrix, list(OP_VARIANTS.keys())):
        if cell["trials"] < 2:
            continue
        spec = OP_VARIANTS.get(cell["op"])
        if not spec:
            continue
        out.append({
            "id": "variant-%s-%s" % (cell["cls"], cell["op"]),
            "kind": "op_variant",
            "applies_to": cell["cls"],
            "changes": spec["changes"],
            "expected_impact": "%s 在 %s 零胜 -> 参数扫描翻案"
                               % (cell["op"], cell["cls"]),
            "cost": "low",
            "risk": "参数过激引入劣化(门禁兜底)",
            "prereg_criteria": _prereg("op_variant", cell["cls"]),
            "params_grid": spec.get("params_grid", []),
            "op": cell["op"],
        })
    return out


# ── 阶段4: 四原子动作空间(OpenRSI Draft/Improve/Debug/Crossover) ──
RSI_ACTIONS = ("draft", "improve", "debug", "crossover")


def propose_rsi(matrix: dict, journal_rows: list | None = None) -> list:
    """四原子动作 -> 算子进化候选(进 ops_registry 的种子)。
      draft     : 零可达类新算子(=R1, 换名归类)
      improve   : 零胜格参数变体(=R2, 换名归类)
      debug     : 连败算子的修复案(同算子换机制部件, 非再拧旋钮)
      crossover : 跨类优胜算子重组(A 类赢家的部件 + B 类赢家的部件
                  -> 第三类复合算子; 结构性新组合)
    全部候选过固定套件+证书沙盒; 跨板存活 >=2/4 才转正(阶段4验收)。
    """
    out = propose(matrix)
    for c in out:
        c["rsi_action"] = "draft" if c["kind"] == "new_op" else "improve"
    # debug: 连败(trials>=2 且 wins==0)的算子 -> 修复案
    for cell in _ledger.zero_reach(matrix, list(OP_VARIANTS.keys())):
        if cell.get("trials", 0) < 2:
            continue
        spec = OP_VARIANTS.get(cell["op"]) or {}
        out.append({
            "id": "debug-%s-%s" % (cell["cls"], cell["op"]),
            "kind": "op_repair",
            "rsi_action": "debug",
            "applies_to": cell["cls"],
            "op": cell["op"],
            "changes": "修复 %s 在 %s 的连败: 换机制部件(%s 之外的"
                       "参数族/前提条件), 非同参重试"
                       % (cell["op"], cell["cls"],
                          (spec.get("params_grid") or ["原参"])[0]),
            "expected_impact": "翻案零胜格",
            "cost": "medium",
            "risk": "修复无效(沙盒裁决)",
            "prereg_criteria": _prereg("op_variant", cell["cls"]),
            "params_grid": [],
        })
    # crossover: 跨类赢家部件重组
    winners = {}
    for cls, ops in (matrix or {}).items():
        for op, st in (ops or {}).items():
            if (st.get("wins") or 0) > 0:
                winners.setdefault(cls, []).append(op)
    cls_list = sorted(winners)
    if len(cls_list) >= 2:
        a, b = cls_list[0], cls_list[1]
        third = cls_list[2] if len(cls_list) > 2 else a
        out.append({
            "id": "xover-%s-%s-%s" % (a, b, third),
            "kind": "op_combo",
            "rsi_action": "crossover",
            "applies_to": third,
            "changes": "重组 %s@%s 与 %s@%s 的机制部件 -> %s 复合算子"
                       % (winners[a][0], a, winners[b][0], b, third),
            "expected_impact": "跨域部件借用(结构性新组合)",
            "cost": "high",
            "risk": "部件不兼容(沙盒裁决)",
            "prereg_criteria": _prereg("new_op", third),
            "sketch": {"name": "xover-%s-%s" % (winners[a][0],
                                                winners[b][0]),
                       "changes": "复合序列(先 %s 后 %s)"
                                  % (winners[a][0], winners[b][0]),
                       "primitives": [winners[a][0], winners[b][0]]},
        })
    return out


def rank_candidates(cands: list) -> list:
    """候选排序: new_op(补短边)>op_variant(翻案)>objective;
    同 kind 内 cost 低者先(便宜的先验证)。"""
    kind_rank = {"new_op": 0, "op_variant": 1, "objective": 2}
    cost_rank = {"low": 0, "medium": 1, "high": 2}
    out = sorted(cands, key=lambda c: (
        0 if (c.get("kind") == "new_op"
              and "零可达类" in str(c.get("expected_impact", "")))
        else kind_rank.get(c.get("kind", ""), 3),
        cost_rank.get(c.get("cost", "medium"), 1)))
    return out
