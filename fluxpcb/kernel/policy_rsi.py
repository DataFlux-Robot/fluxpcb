# -*- coding: utf-8 -*-
"""policy_rsi — 内环性能 RL(流派一: 磨山即训练).

OpenRSI L3 映射: 验证轨迹(journal)回流训练改进者(policy)。
  state  = featurize(residual, plan): 形态格 one-hot + 历史 + plan 特征
  action = (op, params) 带 mask(predict.candidates_for + 黑名单)
  reward = reward_norm(阶段0.2 归一 Δobjective), **上尾 ×T 尾加权**
           (OpenRSI tail shaping: 偏重大突破)
  policy = Ridge 回归拟合 journal(reward_norm 目标) + bandit 先验混合
           (数据少时先验主导, 数据多时回归主导 —— 冷启动安全)
  探索  = Thompson(后验采样替代均值) + ε 随机掩码候选

DPP 纪律: 拟合只用 train split 网的 journal 行; test split 只评估。
"""
from __future__ import annotations

import json
import math
from pathlib import Path

MORPH_CELLS = [
    ("topology", "connectivity"), ("topology", "planarity"),
    ("geometry", "spacing"), ("geometry", "width"),
    ("geometry", "escape"), ("rule", "drc_rule"), ("rule", "netlist"),
    ("capacity", "corridor"), ("capacity", "current"),
]
FEAT_DIM = 9 + 5     # 形态 one-hot + [stalled, n_ops, blk_frac,
                     #              gap_norm, has_paths]


def featurize(residual: dict, plan: dict | None = None) -> list:
    """state -> 定长向量(离线可重建: 全部来自 ops/plan 落盘字段)."""
    plan = plan or {}
    m = (residual or {}).get("morphology") or {}
    cell = tuple(m.get("cell") or ())
    onehot = [1.0 if cell == c else 0.0 for c in MORPH_CELLS]
    ops = (residual or {}).get("ops") or []
    stalled = 0
    for o in reversed(ops):
        if o.get("improved"):
            break
        stalled += 1
    bl = (residual or {}).get("blacklist") or []
    trace = ((residual or {}).get("objective") or {}).get("trace") or []
    gap = trace[-1][1] if trace and trace[-1][1] is not None else -1.0
    gap_norm = max(0.0, min(1.0, float(gap) / 20.0)) if gap >= 0 else 0.5
    return onehot + [
        min(stalled, 6) / 6.0,
        min(len(ops), 20) / 20.0,
        min(len(bl), 8) / 8.0,
        gap_norm,
        1.0 if (plan.get("paths") or plan.get("islands")) else 0.0,
    ]


class JournalPolicy:
    """ridge 回归打分器(predict.v2 插槽): x -> 预期 reward_norm.
    fit() 只吃 train-split 行; predict() 与 bandit 先验按样本量混合。"""

    def __init__(self, ridge: float = 1.0, tail_boost: float = 4.0):
        self.w = None
        self.n = 0
        self.ridge = ridge
        self.tail_boost = tail_boost      # OpenRSI 上尾加权

    def fit(self, xs: list, ys: list) -> float:
        """加权 ridge(上尾样本 ×tail_boost)。返回留出均方差(自评)."""
        if not xs:
            return -1.0
        n, d = len(xs), len(xs[0]) + 1
        X = [[1.0] + list(x) for x in xs]
        wts = [self.tail_boost if y > 0.05 else 1.0 for y in ys]
        # 正规方程 (X'WX + λI) w = X'Wy
        A = [[0.0] * d for _ in range(d)]
        b = [0.0] * d
        for xi, yi, wi in zip(X, ys, wts):
            for i in range(d):
                b[i] += wi * xi[i] * yi
                for j in range(d):
                    A[i][j] += wi * xi[i] * xi[j]
        for i in range(d):
            A[i][i] += self.ridge
        self.w = _solve(A, b)
        self.n = len(xs)
        errs = [(self.predict_raw(x) - y) ** 2 for x, y in zip(xs, ys)]
        return math.sqrt(sum(errs) / len(errs))

    def predict_raw(self, x: list) -> float:
        if self.w is None:
            return 0.0
        s = self.w[0]
        for wi, xi in zip(self.w[1:], x):
            s += wi * xi
        return s

    def score(self, x: list, prior: float) -> float:
        """与 bandit 先验混合: 样本 30 条起回归主导(冷启动安全)."""
        if self.w is None or self.n < 5:
            return prior
        mix = min(self.n / 30.0, 1.0)
        return round((1 - mix) * prior + mix * self.predict_raw(x), 4)


def thompson(alpha: float, beta: float) -> float:
    """Beta 后验采样(探索注入: 替代均值贪心)."""
    try:
        import random
        return random.betavariate(max(alpha, 0.1), max(beta, 0.1))
    except Exception:
        return alpha / max(alpha + beta, 1e-9)


def load_journal(rows_path: Path) -> tuple:
    """journal 转移表 -> (xs, ys)。期望行: {features, reward_norm,
    split, op, cls} —— 由 collect_journal 从 grind_state 生成。"""
    try:
        rows = json.loads(Path(rows_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return [], []
    xs = [r["features"] for r in rows if r.get("split") == "train"]
    ys = [float(r.get("reward_norm") or 0) for r in rows
          if r.get("split") == "train"]
    return xs, ys


def collect_journal(grind_data: dict, split_of: dict,
                    plan_of: dict | None = None) -> list:
    """grind_state -> 转移行(features+reward_norm+split+op+cls)。
    split_of: {net: 'train'|'val'|'test'}(ledger.split_residuals)。"""
    rows = []
    for key, res in (grind_data.get("residuals") or {}).items():
        net = res.get("net", "")
        plan = (plan_of or {}).get(net) or {}
        for o in (res.get("ops") or []):
            if "reward_norm" not in o:
                continue        # 旧格式行无归一奖励, 不入训(诚实)
            # 特征重建: 用该行时刻的形态(冻结 class_at)+行字段
            r_at = dict(res, morphology=res.get("morphology") or {})
            feats = featurize(r_at, plan)
            # 用行内 obj_before 盖 gap 维(时点值, 非当前值)
            if o.get("obj_before") is not None and o.get("obj_kind"):
                gi = 4 + 3     # gap_norm 槽位在 onehot 之后第 4 个
                v = float(o["obj_before"])
                feats[gi] = (max(0.0, min(1.0, v / 20.0))
                             if o.get("obj_kind") == "gap" else 0.5)
            rows.append({
                "features": feats,
                "reward_norm": o.get("reward_norm"),
                "op": o.get("op"), "cls": o.get("class_at"),
                "ts": o.get("ts"),
                "split": split_of.get(net, "train"),
            })
    return rows


def _solve(A: list, b: list) -> list:
    """高斯消元(小维 FEAT_DIM+1, 无需 numpy)."""
    n = len(b)
    M = [row[:] + [b[i]] for i, row in enumerate(A)]
    for i in range(n):
        p = max(range(i, n), key=lambda r: abs(M[r][i])) or i
        M[i], M[p] = M[p], M[i]
        piv = M[i][i] or 1e-9
        for r in range(n):
            if r == i:
                continue
            f = M[r][i] / piv
            for c in range(i, n + 1):
                M[r][c] -= f * M[i][c]
    return [M[i][n] / (M[i][i] or 1e-9) for i in range(n)]
