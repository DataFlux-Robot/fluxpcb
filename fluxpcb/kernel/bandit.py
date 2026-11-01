# -*- coding: utf-8 -*-
"""A2 — Thompson Sampling 修复臂调度器.

出处: PCBSchemaGen(2602.00510) 的"冻结验证器+推理期精炼"配方:
无参考答案领域的修复选择用贝叶斯臂获取替代静态启发式——
  * 臂 = (动作, 残差类) —— DS-MoE(2603.26735) 的类感知专家路由:
    escape-crossing 类的 fanout_repair 臂与 power-fragment 类的
    同名臂是不同专家, 各自从自己的战绩里学习;
  * 奖励 = 动作效果的连续分(闭合开路=1, 结构进展=0.7, 中性=0.5,
    劣化/回滚=0) —— 分层连续评分(A3)落地后换评分增量;
  * 探索保证: Beta 后验抽样下任何臂永不饿死 —— 直接治
    FC_L8 的 fanout-only 饥饿(静态权重把 18 个扇出候选排最前,
    收割臂零预算)。

契约:
  读=decision_journal.json(动作效果, 指纹无关的历史战绩);
  写=bandit_state.json(α/β 后验, 跨进程持久);
  语义前提=奖励只认"效果"不认"意图"(effect=0 的动作不吃 0.7)。
"""
from __future__ import annotations

import json
import random
from pathlib import Path

# 奖励表(动作效果 -> [0,1] 连续分)
R_CLOSED = 1.0       # delta_opens > 0: 真闭合
R_PROGRESS = 0.7     # effect>0 且 delta=0: 结构进展(落了扇出/缝合)
R_NEUTRAL = 0.5      # 无过失也无进展
R_REGRESS = 0.0      # delta<0 或回滚: 劣化


def outcome_reward(outcome: dict) -> float:
    """动作结果 -> 连续奖励(契约: 只认效果)."""
    if not outcome:
        return R_NEUTRAL
    if outcome.get("strict_rollback") or outcome.get("rolled_back"):
        return R_REGRESS
    d_open = float(outcome.get("delta_opens", 0) or 0)
    d_short = float(outcome.get("delta_shorts", 0) or 0)
    if d_short > 0:
        return R_REGRESS
    if d_open > 0:
        return R_CLOSED
    if d_open < 0:
        return R_REGRESS
    if float(outcome.get("effect", 0) or 0) > 0:
        return R_PROGRESS
    return R_NEUTRAL


class ArmBandit:
    """Beta-Thompson Sampling on (action, class) arms.

    state: {arm_key: [alpha, beta]}  — arm_key = "action|cls"
    """

    def __init__(self, state_path: Path | None = None,
                 prior_alpha: float = 1.0, prior_beta: float = 1.0,
                 rng: random.Random | None = None):
        self.state_path = Path(state_path) if state_path else None
        self.prior = (prior_alpha, prior_beta)
        self.rng = rng or random.Random()
        self.state: dict = {}
        if self.state_path and self.state_path.exists():
            try:
                self.state = json.loads(
                    self.state_path.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                self.state = {}     # ok-silent: 损坏状态=重置先验(失效安全)

    @staticmethod
    def arm_key(action: str, cls: str) -> str:
        return "%s|%s" % (action, cls or "untyped")

    def _posterior(self, key):
        return tuple(self.state.get(key, list(self.prior)))

    def sample(self, key) -> float:
        a, b = self._posterior(key)
        return self.rng.betavariate(max(a, 0.1), max(b, 0.1))

    def update(self, action: str, cls: str, reward: float):
        key = self.arm_key(action, cls)
        a, b = self._posterior(key)
        r = min(1.0, max(0.0, float(reward)))
        self.state[key] = [a + r, b + (1.0 - r)]
        return self.state[key]

    def rank(self, candidates: list, cls_of: dict | None = None) -> list:
        """TS 抽样排序: 每个候选按其臂的后验抽样, 样本值降序。
        探索保证: 低样本臂的后验宽, 偶尔抽高——收割臂永不饿死。
        稳定性: 同臂多候选共享一次抽样(避免同臂内乱序)。"""
        cls_of = cls_of or {}
        draws = {}
        scored = []
        for c in candidates:
            act = c.get("action", "")
            cls = c.get("class") or cls_of.get(c.get("_net", ""), "")
            key = self.arm_key(act, cls)
            if key not in draws:
                draws[key] = self.sample(key)
            scored.append((draws[key], c))
        scored.sort(key=lambda t: -t[0])
        return [c for (_s, c) in scored]

    def save(self):
        if not self.state_path:
            return
        try:
            self.state_path.write_text(
                json.dumps(self.state, indent=1), encoding="utf-8")
        except OSError:
            pass    # ok-silent: 状态落盘失败=下次重放 journal 重建

    def ingest_journal(self, journal_path: Path, cls_of: dict | None = None):
        """从决策日志重建后验(跨进程学习, 单次动作只喂一次)。"""
        cls_of = cls_of or {}
        try:
            entries = json.loads(
                Path(journal_path).read_text(encoding="utf-8"))
        except (ValueError, OSError):
            return 0
        n = 0
        seen = set()
        for e in entries:
            d = e.get("decision", {})
            o = e.get("outcome", {})
            act = d.get("action", "")
            if not act:
                continue
            # 同一 (action,params) 只计一次(params 截断对齐 routing key)
            sig = (act, json.dumps(d.get("params", {}),
                                   sort_keys=True)[:120])
            if sig in seen:
                continue
            seen.add(sig)
            net = str((d.get("params", {}) or {}).get("net", ""))
            if not net:
                nets = (d.get("params", {}) or {}).get("nets") or []
                net = str(nets[0]) if nets else ""
            cls = cls_of.get(net, "")
            self.update(act, cls, outcome_reward(o))
            n += 1
        return n
