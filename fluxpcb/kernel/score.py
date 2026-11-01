# -*- coding: utf-8 -*-
"""A3 — 分层连续评分器.

出处: PCBSchemaGen(2602.00510) 的确定性分层验证器(连续分+引脚级
定位) + OmniLayout/OmniRouting(2607.03261/2608.04434) 的
"几何→可布线性→电气→工具链"分层约束评估。五层:
  L1 connectivity   — 连通(开路对)
  L2 clearance      — 间距违例
  L3 width_via      — 线宽/过孔/钻孔工艺规则
  L4 electrical     — 短路/电气语义(硬层: 短路直接重罚)
  L5 manufacturability — 加工(真违例/待豁免弯折)

契约:
  读=DRC JSON 报告(真实引擎, PCBWorld"引擎当裁判")+conn_audit 对数;
  写=score dict(总分+每层连续分+**证据链**: 违例描述+uuid —
     Chat Debugging(2608.02955)的"决策必须指向证据节点");
  语义前提=连续分只用于比较与调度(单调性), 不是绝对质量度量。
"""
from __future__ import annotations

import json
from pathlib import Path

# 层权重(单调性不受权重影响; 权重反映工程优先级)
WEIGHTS = {
    "connectivity": 0.30,
    "clearance": 0.20,
    "width_via": 0.10,
    "electrical": 0.30,
    "manufacturability": 0.10,
}

_VIOL_BUCKETS = {
    "clearance": "clearance",
    "hole_clearance": "clearance",
    "copper_edge_clearance": "clearance",
    "track_width": "width_via",
    "via_diameter": "width_via",
    "drill_out_of_range": "width_via",
    "hole_to_hole": "width_via",
    "shorting_items": "electrical",
    "starved_thermal": "electrical",
}


def _cap01(x: float) -> float:
    return max(0.0, min(1.0, float(x)))


def score_board(opens: int, total_pairs: int, drc_report: dict,
                fab: dict | None = None) -> dict:
    """五层连续评分 + 证据链。

    opens/total_pairs: conn_audit 的开路对(分子/分母);
    drc_report: kicad-cli --format json 报告(dict);
    fab: fab_waiver.classify 的 genuine 清单(可空)。
    """
    violations = (drc_report or {}).get("violations") or []
    counts = {"clearance": 0, "width_via": 0, "electrical": 0}
    evidence = []
    for v in violations:
        bucket = _VIOL_BUCKETS.get(v.get("type", ""))
        if not bucket or bucket not in counts:
            continue
        counts[bucket] += 1
        if len(evidence) < 30:
            items = v.get("items") or []
            evidence.append({
                "layer": bucket,
                "type": v.get("type", ""),
                "desc": (v.get("description") or "")[:120],
                "uuids": [it.get("uuid", "") for it in items[:2]],
                "nets": [it.get("description", "")[:60]
                         for it in items[:2]],
            })
    denom = max(1, int(total_pairs or 1))
    conn = _cap01(1.0 - (opens or 0) / denom)
    clearance = _cap01(1.0 - counts["clearance"] / 10.0)
    wv = _cap01(1.0 - counts["width_via"] / 10.0)
    # L4 硬层: 短路一条扣 0.2, 温度孤岛轻微
    elec = _cap01(1.0 - 0.2 * counts["electrical"])
    fab = fab or {}
    genuine = len(fab.get("genuine") or [])
    bend = len(fab.get("bend") or [])
    mfg = _cap01(1.0 - (genuine + 0.5 * bend) / 10.0)
    layers = {
        "connectivity": round(conn, 4),
        "clearance": round(clearance, 4),
        "width_via": round(wv, 4),
        "electrical": round(elec, 4),
        "manufacturability": round(mfg, 4),
    }
    total = sum(WEIGHTS[k] * v for k, v in layers.items())
    return {"score": round(total, 4), "layers": layers,
            "evidence": evidence}


def progress(prev: dict, cur: dict, min_gain: float = 0.005) -> bool:
    """流水线阶段推进判据(2602.00510 连续奖励):
    总分增益不足 min_gain = 阶段平台期, 推进下一阶段。"""
    if not prev:
        return False
    return (cur.get("score", 0) - prev.get("score", 0)) < min_gain


def score_delta_reward(prev: dict, cur: dict) -> float:
    """A2 的评分增量奖励(映射到 [0,1]): 评分升=1, 平=0.5, 降=0。"""
    if not prev:
        return 0.5
    d = cur.get("score", 0) - prev.get("score", 0)
    if d > 0.002:
        return 1.0
    if d < -0.002:
        return 0.0
    return 0.5


def load_drc_json(path: Path) -> dict:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {}    # ok-silent: 报告缺失=按空报(层分会暴露缺口)
