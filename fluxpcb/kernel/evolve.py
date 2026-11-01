# -*- coding: utf-8 -*-
"""L3 方法进化环(HarnessPro 模式): harness 变体的代际进化.

进化对象 = variant(一套完整改进方法), 不是孤立 trick:
  variant = {id, ops_params(算子参数), policy(调度参数),
             budget(步数/成本分配), objective(目标权重)}
种群 ≤3, 父代保留防退化, 多代递归。

核心机制(逐条对齐 HarnessPro):
  * diagnose(rollouts)   — 双诊断: 失败模式 + **冗余成本**
      (不只看哪败, 还看哪白花钱 — wall_s per gain) + 跨 rollout 方差;
  * mutate(diagnosis, variant, diffs) — **diff 驱动变异**:
      读自身诊断 + 跨变体差异提修改, 无预设类别(L2 的 R1/R2/R3
      预设规则废除);
  * pareto_filter       — 性能∧成本双目标, 被支配/低于底线淘汰;
  * select_tiers        — 归一化平方距离出 heavy/medium/lite 三档;
  * 反作弊: 固定评测器+预注册判据+自报无效(沿用 L2 已证机制)。

契约:
  读=rollout 记录(grind_state/journal/wall 时);
  写=evo_gen_<n>.json(代际账 — A-E 判定证据源升级为代际曲线);
  语义前提=变异只由诊断+diff 推导(禁预设路线), 每代留痕。
"""
from __future__ import annotations

import json
import time
import uuid


def new_variant(base: dict | None = None, name: str = "") -> dict:
    v = {
        "id": name or ("v-" + uuid.uuid4().hex[:6]),
        "ops_params": {},
        "policy": {
            "stall_penalty": 0.4,
            "blacklist_fails": 2,
            "upgrade_stride": 2,
        },
        "budget": {"steps_per_window": 8},
        "objective": {"w_opens": 1.0, "w_clearance": 0.2},
    }
    if base:
        for k in ("ops_params", "policy", "budget", "objective"):
            if k in base:
                v[k] = json.loads(json.dumps(base[k]))
    return v


def diagnose(rollouts: list) -> dict:
    """G 次 rollout -> 双诊断。rollout = {gain, wall_s, ops:[...]}"""
    ops_stat = {}
    gains = []
    costs = []
    for r in rollouts:
        gains.append(r.get("gain", 0.0))
        costs.append(r.get("wall_s", 0))
        for o in r.get("ops", []):
            st = ops_stat.setdefault(o.get("op", "?"), {
                "n": 0, "gain": 0.0, "wall": 0.0})
            st["n"] += 1
            st["gain"] += float(o.get("d_obj", 0) or 0)
            st["wall"] += float(o.get("wall_s", 0) or 0)
    redundancy = []
    for op, st in ops_stat.items():
        if st["n"] >= 2 and st["wall"] > 0 and st["gain"] <= 0:
            redundancy.append({"op": op, "n": st["n"],
                               "wall": round(st["wall"], 1),
                               "gain": st["gain"]})
    mean_g = sum(gains) / len(gains) if gains else 0.0
    var_g = (sum((g - mean_g) ** 2 for g in gains) / len(gains)
             ) ** 0.5 if gains else 0.0
    return {"failure_modes": [
                {"op": k, "n": v["n"], "gain": v["gain"]}
                for k, v in ops_stat.items() if v["gain"] <= 0],
            "redundancy": redundancy,
            "variance": round(var_g, 4),
            "mean_gain": round(mean_g, 4),
            "mean_cost": round(sum(costs) / len(costs), 1)
            if costs else 0.0}


def mutate(diagnosis: dict, variant: dict,
           diffs: list | None = None) -> list:
    """诊断+跨变体 diff -> 变异子代。每子代带 HarnessPro 四字段。
    变异落点由诊断指认(禁预设候选类别)。"""
    out = []
    base = json.loads(json.dumps(variant))
    for rd in (diagnosis.get("redundancy") or [])[:2]:
        v = json.loads(json.dumps(base))
        v["id"] = variant["id"] + "-m1cut"
        v["policy"] = dict(v.get("policy") or {})
        v["policy"]["stall_penalty"] = min(
            0.9, v["policy"].get("stall_penalty", 0.4) + 0.2)
        v["ops_params"] = dict(v.get("ops_params") or {})
        v["ops_params"][rd.get('op', '?')] = dict(
            v["ops_params"].get(rd.get('op', '?')) or {}, _demote=True)
        out.append({"variant": v,
                    "changes": "stall_penalty+0.2 & %s 降权"
                               "(冗余 %.0fs/零增益)"
                               % (rd.get('op', '?'), rd.get('wall', rd.get('wasted_cost', 0))),
                    "quality_impact": "去掉白花钱步, 提高有效步密度",
                    "cost_impact": "negative(省钱)",
                    "risks": "过度惩罚错过慢热算子"})
    for fm in (diagnosis.get("failure_modes") or [])[:2]:
        v = json.loads(json.dumps(base))
        v["id"] = variant["id"] + "-m2try"
        v["ops_params"] = dict(v.get("ops_params") or {})
        cur = v.get("ops_params", {}).get(fm.get('op', '?'), {})
        v["ops_params"][fm.get('op', '?')] = dict(cur, _retry_shift=True)
        v["policy"] = dict(v.get("policy") or {})
        v["policy"]["blacklist_fails"] = v["policy"].get(
            "blacklist_fails", 2) + 1
        out.append({"variant": v,
                    "changes": "%s 参数扰动+拉黑阈值放宽到 %d"
                               "(失败 %d 次)"
                               % (fm.get('op', '?'),
                                  v["policy"]["blacklist_fails"],
                                  fm.get('n', 0)),
                    "quality_impact": "给失败算子第二档机会",
                    "cost_impact": "positive(多试)",
                    "risks": "延长无效尝试"})
    for d in (diffs or [])[:1]:
        if d.get("better"):
            v = json.loads(json.dumps(d["variant"]))
            v["id"] = variant["id"] + "-m3follow"
            out.append({"variant": v,
                        "changes": "跟随优势变体 diff: %s"
                                   % d.get("note", ""),
                        "quality_impact": "借势",
                        "cost_impact": "neutral",
                        "risks": "过拟合该变体环境"})
    return out


def pareto_filter(cands: list, baseline: dict) -> list:
    """双目标(gain↑, wall↓)非支配过滤; 性能底线=baseline.gain。"""
    keep = []
    for c in cands:
        if c["gain"] < baseline.get("gain", 0.0):
            continue
        dominated = False
        for o in cands:
            if o is c:
                continue
            if (o["gain"] >= c["gain"] and o["wall"] <= c["wall"]
                    and (o["gain"] > c["gain"]
                         or o["wall"] < c["wall"])):
                dominated = True
                break
        if not dominated:
            keep.append(c)
    return keep


def select_tiers(front: list, baseline: dict) -> dict:
    """归一化平方距离 J = α·d_perf² + β·d_cost² 三档代表。"""
    if not front:
        return {}
    gains = [c["gain"] for c in front] + [baseline.get("gain", 0)]
    walls = [c["wall"] for c in front] + [baseline.get("wall", 1)]
    gspan = (max(gains) - min(gains)) or 1.0
    wspan = (max(walls) - min(walls)) or 1.0

    gmax, wmin = max(gains), min(walls)

    def dist(c, a, b):
        # 距理想点(max gain, min wall)的归一化平方距离
        dp = (gmax - c["gain"]) / gspan
        dc = (c["wall"] - wmin) / wspan
        return a * dp * dp + b * dc * dc

    tiers = {}
    for name, (a, b) in (("heavy", (2.0, 1.0)),
                         ("medium", (1.0, 1.0)),
                         ("lite", (1.0, 2.0))):
        best = min(front, key=lambda c: dist(c, a, b))
        tiers[name] = {"variant_id": best["variant"]["id"],
                       "gain": best["gain"], "wall": best["wall"]}
    return tiers
