# -*- coding: utf-8 -*-
"""阶段3 — 预测器 v1 (RSI-Anything 世界模型的最小实现).

预测每个 (残差, 算子) 微步的增益, 执行预算花在最可能有效的上。
v1 = bandit 后验(动作×类战绩) × 几何启发(缺口越小越接近闭合);
v2 = journal 特征回归(数据足量后替换, 接口不变)。

契约:
  读=bandit_state(后验) + grind_state(残差轨迹/黑名单);
  写=无(纯预测) — 预测偏差由 grind.record_step 的实际结果隐式校正;
  语义前提=预测只用于排序, 不用于跳过门禁(执行仍全过 _gated_step)。
"""
from __future__ import annotations

from . import bandit as _bd
from . import operators as _ops


def predicted_gain(op: str, cls: str, residual: dict,
                   bandit: _bd.ArmBandit | None = None,
                   policy=None, plan: dict | None = None,
                   explore: bool = False) -> float:
    """(残差,算子) 的预期 objective 增益(用于排序, 无量纲 [0,~1])。

    v1 合成: 臂后验均值 × 缺口接近度 × 停滞惩罚。
    v2(阶段1.2): explore 时后验**采样**(Thompson 探索, 替代均值
    贪心); policy(JournalPolicy)命中时与先验混合 —— journal 回归
    插槽兑现(predict.v2 docstring 的承诺), 冷启动先验主导。
    """
    # 臂后验(经验值; explore=采样)
    if bandit is not None:
        a, b = bandit._posterior(bandit.arm_key(op, cls))
        if explore:
            from .policy_rsi import thompson as _ts
            arm_mean = _ts(a, b)
        else:
            arm_mean = a / (a + b)
    else:
        arm_mean = 0.5
    # 缺口启发: 缺口小 = 接近闭合, 同样一步更可能翻越
    gap = None
    obj_trace = (residual.get("objective") or {}).get("trace") or []
    vals = [t[1] for t in obj_trace if t[1] is not None]
    if vals:
        gap = vals[-1]
    if gap is None:
        gap_factor = 0.7            # 未知目标 = 中性偏乐观
    elif gap <= 0:
        gap_factor = 1.0            # 已有路/已闭 — 收尾优先
    else:
        # 缺口 0.3mm 的翻越概率远低于 0.05mm — 反比压缩
        gap_factor = 1.0 / (1.0 + 4.0 * gap)
    # 停滞惩罚: 该算子在该残差上连续失败则降温
    ops_hist = residual.get("ops") or []
    same = [o for o in ops_hist if o.get("op") == op]
    if same and not any(o.get("improved") for o in same[-2:]):
        # L3 variant.policy 可配置(HarnessPro: 调度参数是进化对象)
        stall_penalty = float(__import__("os").environ.get(
            "FLUXNT_STALL_PENALTY", "0.4"))
    else:
        stall_penalty = 1.0
    # 成本折扣: light 算子便宜, 同增益下优先
    cost = (_ops.OPERATORS.get(op) or {}).get("cost", "heavy")
    cost_factor = 1.0 if cost == "light" else 0.85
    g = arm_mean * gap_factor * stall_penalty * cost_factor
    if policy is not None:
        from . import policy_rsi as _prsi
        # 复审修复: 打分含**动作**(state-only 让同残差不同算子同分)
        g = policy.score(_prsi.featurize(residual, plan, op=op), g)
    return round(g, 4)


def rank_microsteps(residuals: dict, cls_of: dict,
                    bandit: _bd.ArmBandit | None = None,
                    grind=None, policy=None, plans: dict | None = None,
                    explore: bool = False) -> list:
    """所有 (残差, 算子) 候选按预测增益降序。

    residuals: {residual_key: grind residual dict};
    cls_of: {net: class}。
    explore: Thompson 采样 + ε 扰动 top-3(防均值贪心早收敛)。
    """
    out = []
    for rkey, res in residuals.items():
        net = res.get("net", "")
        cls = res.get("class") or cls_of.get(net, "")
        stalled = 0
        ops_hist = res.get("ops") or []
        for o in reversed(ops_hist):
            if o.get("improved"):
                break
            stalled += 1
        for op in _ops.candidates_for(cls, stalled):
            if grind is not None and getattr(grind, "op_blacklisted", None):
                if grind.op_blacklisted(net, op):
                    continue
            g = predicted_gain(op, cls, res, bandit,
                               policy=policy,
                               plan=(plans or {}).get(net),
                               explore=explore)
            out.append({"net": net, "residual": rkey, "op": op,
                        "class": cls, "gain": g})
    out.sort(key=lambda c: -c["gain"])
    if explore and len(out) >= 2:
        # ε 扰动: 以小概率轮换 top-3(探索多样性, 熵保持)
        try:
            import random
            if random.random() < float(__import__("os").environ.get(
                    "FLUXNT_RSI_EPS", "0.15")):
                k = min(3, len(out))
                top = out[:k]
                random.shuffle(top)
                out = top + out[k:]
        except Exception:
            pass
    return out
