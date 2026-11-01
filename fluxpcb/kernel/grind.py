# -*- coding: utf-8 -*-
"""阶段1 — 磨山状态机: 残差级跨轮记忆.

RSI 递归磨山的地基: 每个残差是一个独立的爬山问题, 有自己的
objective 轨迹、算子尝试史、黑名单与侵蚀台账。跨轮/跨进程持久
(grind_state.json), 不依赖 decision_journal(它截断 50 条会遗忘)。

残差标识: (net, pair_idx) — blocker_net 记入历史不进 key
(同一残差的 blocker 会随侵蚀移动)。

目标函数(爬山判据, 单调下降):
  gap_mm     — B/D 类: geomir rejects 的 min(short_by_mm); 有路=0
  crossings  — A/C 类: escape_crossings 族计数

契约:
  读=journal/grind_state(自持, 无上限追加同 chain_manifest);
  写=grind_state.json(每次 record_step 后落盘);
  语义前提=objective 改进才算步成功(闭合是副产品);
  侵蚀台账必须带还原令牌(防磨穿板无路回)。
"""
from __future__ import annotations

import json
import time
from pathlib import Path

VERSION = 1


def residual_key(net: str, pair_idx: int = 0) -> str:
    return "%s#%d" % (net, pair_idx)


def gap_objective(plan: dict) -> float:
    """B/D 爬山目标: 走廊缺口(mm)。有验证路径=0; 否则=最小近失。"""
    if not plan:
        return -1.0            # 无规划 = 未知(不当作 0)
    if plan.get("paths"):
        return 0.0
    gaps = []
    for r in plan.get("rejects") or []:
        bl = r.get("blocked_by") or {}
        sbm = bl.get("short_by_mm")
        if sbm is not None:
            gaps.append(float(sbm))
    return round(min(gaps), 4) if gaps else -1.0


class GrindState:
    """grind_state.json 的读写 + 磨山记账。"""

    def __init__(self, state_path: Path | None = None):
        self.state_path = Path(state_path) if state_path else None
        self.data = {"residuals": {}, "failed_branches": [],
                     "version": VERSION}
        if self.state_path and self.state_path.exists():
            try:
                loaded = json.loads(
                    self.state_path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict) and "residuals" in loaded:
                    self.data = loaded
            except (ValueError, OSError):
                pass    # ok-silent: 损坏状态=重建(失效安全, 可从
                        # journal 部分重建)

    def residual(self, net: str, pair_idx: int = 0) -> dict:
        key = residual_key(net, pair_idx)
        r = self.data["residuals"].get(key)
        if r is None:
            r = {"net": net, "pair_idx": pair_idx, "class": "",
                 "objective": {"kind": "", "trace": []},
                 "ops": [], "blacklist": [], "erosion": []}
            self.data["residuals"][key] = r
        return r

    def set_class(self, net: str, cls: str, pair_idx: int = 0):
        self.residual(net, pair_idx)["class"] = cls

    def record_step(self, net: str, op: str, params: dict,
                    obj_before: float, obj_after: float,
                    outcome: dict, pair_idx: int = 0,
                    cls: str | None = None) -> dict:
        """一步磨山记账。返回 {improved: bool, d_obj: float}。
        语义: objective 下降=改善(即使开路未闭合 — 闭合是副产品)。"""
        r = self.residual(net, pair_idx)
        d_obj = round(float(obj_before) - float(obj_after), 4) \
            if (obj_before >= 0 and obj_after >= 0) else 0.0
        # 成功 = objective 单调改进 或 开路真闭合(闭合是副产品,
        # 但闭合本身当然是成功 — 语义不许教条)
        improved = (d_obj > 0
                    or float(outcome.get("delta_opens", 0) or 0) > 0)
        rolled = bool(outcome.get("strict_rollback")
                      or outcome.get("rolled_back"))
        r["objective"]["trace"].append(
            [round(time.time()), float(obj_after) if obj_after >= 0
             else None])
        r["ops"].append({
            "op": op, "params": params,
            "d_obj": d_obj, "improved": improved,
            "d_opens": outcome.get("delta_opens", 0),
            "rolled_back": rolled,
            "summary": str(outcome.get("summary", ""))[:80],
            # L2 矩阵聚合三字段(类变更不污染历史)
            "class_at": cls if cls is not None else r.get("class", ""),
            "ts": round(time.time()),
            "effect": outcome.get("effect", 0),
        })
        # 防自欺(HarnessPro 反作弊): 自报 effect 不算成功, 只认
        # objective/开路; 同算子两连败即拉黑 — 杜绝 stitch 式空转
        _same_fails = sum(1 for o in r["ops"]
                          if o.get("op") == op and not o.get("improved"))
        _bl_fails = int(__import__("os").environ.get(
            "FLUXNT_BLACKLIST_FAILS", "2"))
        if rolled or (not improved and _same_fails >= _bl_fails):
            sig = self.op_sig(op, params)
            if sig not in r["blacklist"]:
                r["blacklist"].append(sig)
        if not improved:
            self.data["failed_branches"].append(
                {"net": net, "op": op,
                 "params": {k: str(v)[:40] for k, v in
                            (params or {}).items()},
                 "obj_before": obj_before, "obj_after": obj_after})
            self.data["failed_branches"] = \
                self.data["failed_branches"][-200:]
        self.save()
        return {"improved": improved, "d_obj": d_obj}

    def add_erosion(self, net: str, op: str, token: dict,
                    pair_idx: int = 0):
        """侵蚀台账: 每次墙侵蚀带还原令牌(widen_net .widths.json 范式)。"""
        self.residual(net, pair_idx)["erosion"].append(
            {"op": op, "token": token, "ts": round(time.time())})
        self.save()

    @staticmethod
    def op_sig(op: str, params: dict) -> str:
        return "%s:%s" % (op, json.dumps(params or {},
                                         sort_keys=True)[:80])

    def is_blacklisted(self, net: str, op: str, params: dict,
                       pair_idx: int = 0) -> bool:
        return self.op_sig(op, params) in \
            self.residual(net, pair_idx)["blacklist"]

    def op_blacklisted(self, net: str, op: str, pair_idx: int = 0) -> bool:
        """按算子名粒度拉黑(同算子不同参数一并避让 — 磨山粒度)。"""
        return any(b.startswith(op + ":") for b in
                   self.residual(net, pair_idx)["blacklist"])

    def stalled(self, net: str, k: int = 3, pair_idx: int = 0) -> bool:
        """连续 k 步无改善 = 该残差停滞(算子升级信号, 阶段5)。"""
        ops = self.residual(net, pair_idx)["ops"][-k:]
        return len(ops) >= k and not any(o.get("improved")
                                         for o in ops)

    def best_objective(self, net: str, pair_idx: int = 0):
        trace = self.residual(net, pair_idx)["objective"]["trace"]
        vals = [t[1] for t in trace if t[1] is not None]
        return min(vals) if vals else None

    def save(self):
        if not self.state_path:
            return
        try:
            self.state_path.write_text(
                json.dumps(self.data, ensure_ascii=False,
                           indent=1), encoding="utf-8")
        except OSError:
            pass    # ok-silent: 落盘失败=下次重建(失效安全)
