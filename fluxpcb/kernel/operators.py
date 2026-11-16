# -*- coding: utf-8 -*-
"""阶段2 — 微扰动算子库: 注册表 + 升级链 + 动作发射.

RSI 磨山的"手": 每个算子是一小步微扰动, 不求解只求 objective
下降。算子全部包装现有原语(动作), 由磨山循环按预测增益调用。

升级链(阶段5 元规则 v1): 同残差连续失败 -> 从轻侵蚀升级到重
侵蚀: nudge_blocker -> rip_blocker -> widen_rescue -> move_blocker。
swap 类走换球搜索(reassign_pins, mode 区分搜索深度)。

契约:
  emit(op, net, ctx) -> 动作 dict(直接进 _gated_step/decision.validate);
  算子成本分级(light/heavy)供预算分配;
  升级链有界(到底后报 stalled, 不再空转)。
"""
from __future__ import annotations

# 成本分级: light≈一次子进程; heavy≈重布/重链(分钟级)
OPERATORS = {
    # ── A/C 平面性类(换球搜索) ──
    "swap_pair":    {"classes": ("escape-crossing",), "cost": "heavy",
                     "action": "reassign_pins"},
    "swap_chain3":  {"classes": ("escape-crossing",), "cost": "heavy",
                     "action": "reassign_pins"},
    "single_drift": {"classes": ("escape-crossing",), "cost": "heavy",
                     "action": "reassign_pins"},
    # ── B/D 口袋墙类(墙侵蚀链) ──
    "nudge_blocker": {"classes": ("local-congestion", "unclassified",
                                  "escape-crossing"), "cost": "light",
                      "action": "gap_nudge"},
    "rip_blocker":   {"classes": ("local-congestion", "unclassified",
                                  "escape-crossing"), "cost": "heavy",
                      "action": "reroute_nets"},
    "widen_rescue":  {"classes": ("local-congestion", "unclassified",
                                  "escape-crossing"), "cost": "heavy",
                      "action": "corridor_rescue"},
    "move_blocker":  {"classes": ("local-congestion", "unclassified",
                                  "escape-crossing"), "cost": "heavy",
                      "action": "optimize_placement"},
    # ── 平面类 ──
    "stitch": {"classes": ("power-fragment",), "cost": "light",
               "action": "power_stitch"},
    # ── C1: zone 铜桥(PENDING_IMPL 收官, _zb 实测 11->8) ──
    "zone_bridge": {"classes": ("power-fragment", "unclassified",
                                "escape-crossing"), "cost": "heavy",
                    "action": "zone_bridge"},
    # ── 收尾: 有验证路径即收割(缺口=0 的残差) ──
    "harvest": {"classes": ("escape-crossing", "local-congestion",
                            "unclassified", "power-fragment"),
                "cost": "light", "action": "direct_link"},
}

# 墙侵蚀升级链(轻->重, 到底即 stalled)
UPGRADE_CHAIN = ["nudge_blocker", "rip_blocker", "widen_rescue",
                 "move_blocker"]
# 换球搜索深度链
SWAP_CHAIN = ["single_drift", "swap_pair", "swap_chain3"]


# D1: 数据驱动实验算子(ops_registry.json) — L2 候选验证期的
# 临时算子, A/B 通过后升默认。结构:
#   {name: {"action": <既有动作>, "params_default": {...},
#           "classes": [...], "status": "experimental|promoted"}}
_EXPERIMENTAL: dict = {}


def load_registry(path=None) -> int:
    """载入实验算子注册表(损坏=空, 失效安全)。
    路径单源: 显式 path > FLUXNT_OPS_REGISTRY(调用方注入, A/B 臂的
    dst 注册表/采纳注册表) > 仓库根 ops_registry.json。
    (断裂案: z_ab 写工作区根、z_l2 写 z_lab、loader 读仓库根 ——
    三方错位, 注入恒静默 no-op。)"""
    import json as _j
    import os as _os
    from pathlib import Path as _P
    global _EXPERIMENTAL
    if path is None:
        path = _os.environ.get("FLUXNT_OPS_REGISTRY") or None
    p = _P(path) if path else _P(__file__).resolve().parent.parent \
        .parent / "ops_registry.json"
    try:
        data = _j.loads(p.read_text(encoding="utf-8"))
        _EXPERIMENTAL = {k: v for k, v in data.items()
                         if isinstance(v, dict)
                         and v.get("status") in ("experimental",
                                                 "promoted")}
    except (ValueError, OSError):
        _EXPERIMENTAL = {}
    return len(_EXPERIMENTAL)


def emit(op: str, net: str, ctx: dict | None = None) -> dict:
    """算子 -> 动作 dict(_gated_step 消费)。ctx 可带 blockers 等。
    D1: 实验算子走注册表(action+params_default 合并)。"""
    ctx = ctx or {}
    spec = OPERATORS.get(op)
    if spec is None:
        exp = _EXPERIMENTAL.get(op)
        if exp is None:
            raise ValueError("unknown operator: %s" % op)
        act = exp.get("action", "")
        base = dict(exp.get("params_default") or {})
        if act == "direct_link":
            paths = (ctx.get("plan") or {}).get("paths") or []
            if not paths:
                raise ValueError("experimental harvest needs path")
            p0 = paths[0]
            params = {"net": net, "pts": p0.get("pts"),
                      "width": base.get("width",
                                        p0.get("width", 0.2))}
        elif act in ("gap_nudge", "optimize_placement"):
            params = dict(base)
            params["net"] = net
        else:
            params = dict(base)
            params.setdefault("nets", [net])
        return {"action": act, "params": params,
                "expected_effect": "实验算子 %s(%s)"
                                   % (op, exp.get("status"))}
    act = spec["action"]
    if act == "reassign_pins":
        return {"action": "reassign_pins",
                "params": {"nets": [net], "mode": op},
                "expected_effect": "换球消交叉(%s)" % op}
    if act == "gap_nudge":
        pp = {"net": net}
        pp.update({k: v for k, v in (ctx.get("op_params") or {}).items()
                   if k in ("dist", "dx", "dy")})
        at = (ctx.get("at") or [None])[0]
        if at:
            pp["at"] = at
        return {"action": "gap_nudge", "params": pp,
                "expected_effect": "挪阻塞线让缝(dist=%s)"
                                   % pp.get("dist", 0.2)}
    if act == "reroute_nets":
        bl = ctx.get("blockers") or []
        pp = {"nets": ([net] + list(bl))[:4]}
        at = (ctx.get("at") or [None])[0]
        if at:
            pp["at"] = at
        return {"action": "reroute_nets", "params": pp,
                "expected_effect": "撕阻塞网重布让走廊"}
    if act == "corridor_rescue":
        bl = ctx.get("blockers") or []
        pp = {"target": net, "blockers": list(bl)[:3]}
        at = (ctx.get("at") or [None])[0]
        if at:
            pp["at"] = at
        return {"action": "corridor_rescue", "params": pp,
                "expected_effect": "FAT 走廊逼退阻挡"}
    if act == "optimize_placement":
        pp = {"net": net}
        at = (ctx.get("at") or [None])[0]
        if at:
            pp["at"] = at
        return {"action": "optimize_placement", "params": pp,
                "expected_effect": "挪阻挡件开走廊"}
    if act == "zone_bridge":
        return {"action": "zone_bridge", "params": {"nets": [net]},
                "expected_effect": "跨切缝桥接(hop-repair 下潜)"}
    if act == "power_stitch":
        return {"action": "power_stitch", "params": {"nets": [net]},
                "expected_effect": "via 缝合平面碎片"}
    if act == "direct_link":
        paths = (ctx.get("plan") or {}).get("paths") or []
        if not paths:
            raise ValueError("harvest needs a verified path")
        p0 = paths[0]
        return {"action": "direct_link",
                "params": {"net": net, "pts": p0.get("pts"),
                           "width": p0.get("width", 0.2)},
                "expected_effect": "收割已验证路径 %smm"
                                   % p0.get("length_mm", "?")}
    raise ValueError("unmapped action: %s" % act)


def candidates_for(cls: str, stalled_ops: int = 0) -> list:
    """残差类 -> 算子序(初始序 + 升级链推进)。
    stalled_ops: 该残差已连续无改善的步数 —— 推进升级链。"""
    if cls == "escape-crossing":
        # 平面性类: 换球为主, 墙侵蚀为辅(卡墙时)
        base = ["zone_bridge"] + list(SWAP_CHAIN) +             ["nudge_blocker"]
    elif cls == "power-fragment":
        # FC_L13 续磨诊断: 只给 stitch = GND 短跳被挡时无路可走;
        # 平面碎片的完整梯子: 收割(短跳直连)->缝合->墙侵蚀
        base = ["harvest", "zone_bridge", "stitch"] +             list(UPGRADE_CHAIN)
    else:
        # K33 补录教训(算子注册≠算子入梯): 未分类/局部拥塞也要看得见
        # 收割与桥接 —— 有验证路时它们是最优解, 漏挂=窗内纯空转
        base = ["harvest", "zone_bridge"] + list(UPGRADE_CHAIN)
    # 升级链推进: 每 2 次无改善前进一档
    shift = min(stalled_ops // 2, len(UPGRADE_CHAIN) - 1)
    out = []
    for op in base:
        if op in UPGRADE_CHAIN:
            idx = UPGRADE_CHAIN.index(op)
            out.append(UPGRADE_CHAIN[min(idx + shift,
                                         len(UPGRADE_CHAIN) - 1)])
        else:
            out.append(op)
    # 去重保序
    seen = set()
    uniq = []
    for op in out:
        if op not in seen:
            seen.add(op)
            uniq.append(op)
    return uniq


def next_after_fail(op: str) -> str | None:
    """升级链的下一档(到底 None = stalled)。"""
    for chain in (UPGRADE_CHAIN, SWAP_CHAIN):
        if op in chain:
            i = chain.index(op)
            return chain[i + 1] if i + 1 < len(chain) else None
    return None
