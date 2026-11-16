# -*- coding: utf-8 -*-
"""L2-① 验证台账: 「残差类 × 算子」成功率矩阵.

RSI 闭环验证模式的核心件 — 一张表两用:
  (a) 验证证据源: 分层 A-E 判定直接由矩阵行推导(哪类在哪算子下
      有正向作用);
  (b) 选题依据: 零可达单元格 = 下一算子的自动选题证明
      (improve.py 的候选生成直接消费它)。

聚合源 = grind_state 的 ops[](class_at/ts/effect 已补全)。
契约:
  读=GrindState.data(无上限持久, journal 截断不可信);
  写=ledger.json(每轮快照追加, 带板指纹);
  语义前提=胜=improved(只认 objective/开路, 反自欺);
  类变更不污染历史(class_at 冻结尝试时类)。
"""
from __future__ import annotations

import json
import time
from pathlib import Path


def build_matrix(grind_data: dict, split: dict | None = None,
                 only: str | None = None) -> dict:
    """split/only: A1 分 split 聚合 — 选题(zero_reach/gap)只许用
    train, test 残差的战绩不得泄漏进规划器。"""
    if split and only:
        kept = {}
        for rk, res in (grind_data.get("residuals") or {}).items():
            if split.get(res.get("net", "")) == only:
                kept[rk] = res
        grind_data = dict(grind_data, residuals=kept)
    """grind_state.data -> 矩阵 {cls: {op: stats}}。

    stats = {trials, wins, sum_d_obj, sum_d_opens, blacklisted,
             last_ts}
    """
    matrix: dict = {}
    for rkey, res in (grind_data.get("residuals") or {}).items():
        bl = set(res.get("blacklist") or [])
        for o in res.get("ops") or []:
            cls = o.get("class_at") or res.get("class") or "untyped"
            op = o.get("op", "?")
            cell = matrix.setdefault(cls, {}).setdefault(op, {
                "trials": 0, "wins": 0, "sum_d_obj": 0.0,
                "sum_d_opens": 0, "blacklisted": 0, "last_ts": 0})
            cell["trials"] += 1
            if o.get("improved"):
                cell["wins"] += 1
            cell["sum_d_obj"] += float(o.get("d_obj", 0) or 0)
            cell["sum_d_opens"] += float(o.get("d_opens", 0) or 0)
            cell["last_ts"] = max(cell["last_ts"],
                                  int(o.get("ts", 0) or 0))
        for sig in bl:
            op = sig.split(":", 1)[0]
            cls = res.get("class") or "untyped"
            cell = matrix.setdefault(cls, {}).setdefault(op, {
                "trials": 0, "wins": 0, "sum_d_obj": 0.0,
                "sum_d_opens": 0, "blacklisted": 0, "last_ts": 0})
            cell["blacklisted"] += 1
    return matrix


def zero_reach(matrix: dict, known_ops: list | None = None) -> list:
    """零可达检测: 有残差活动的类里, 从未被尝试或零胜的算子格。
    返回 [{cls, op, trials, wins, reason}] —— improve.py 的选题证明。"""
    out = []
    for cls, row in matrix.items():
        active = any(c["trials"] > 0 for c in row.values())
        if not active:
            continue
        for op in (known_ops or list(row.keys())):
            cell = row.get(op)
            if cell is None:
                out.append({"cls": cls, "op": op, "trials": 0,
                            "wins": 0, "reason": "未尝试"})
            elif cell["wins"] == 0 and cell["trials"] >= 2:
                out.append({"cls": cls, "op": op,
                            "trials": cell["trials"], "wins": 0,
                            "reason": "零胜"})
    return out


def reachability_gap(matrix: dict, classes: list) -> list:
    """类级零可达: 某残差类在所有算子下合计零胜 —— 该类缺算子的
    结构性证明(new_op 候选的触发条件)。"""
    out = []
    for cls in classes:
        row = matrix.get(cls) or {}
        total_trials = sum(c["trials"] for c in row.values())
        total_wins = sum(c["wins"] for c in row.values())
        if total_trials >= 2 and total_wins == 0:
            out.append({"cls": cls, "trials": total_trials,
                        "wins": 0,
                        "proof": "类级零可达: %d 次尝试 %d 胜"
                                 % (total_trials, total_wins)})
    return out


class Ledger:
    """ledger.json 的读写 + 轮次快照。"""

    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path else None
        self.data = {"snapshots": [], "version": 1}
        if self.path and self.path.exists():
            try:
                loaded = json.loads(
                    self.path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict) and "snapshots" in loaded:
                    self.data = loaded
            except (ValueError, OSError):
                pass    # ok-silent: 损坏=重建(失效安全)

    def snapshot(self, matrix: dict, fp: str = "",
                 note: str = "") -> dict:
        snap = {"ts": round(time.time()), "fp": fp, "note": note,
                "matrix": matrix}
        self.data["snapshots"].append(snap)
        self.save()
        return snap

    def latest(self) -> dict | None:
        snaps = self.data.get("snapshots") or []
        return snaps[-1] if snaps else None

    def save(self):
        if not self.path:
            return
        try:
            self.path.write_text(
                json.dumps(self.data, ensure_ascii=False,
                           indent=1), encoding="utf-8")
        except OSError:
            pass    # ok-silent: 落盘失败=下轮重建(失效安全)


def split_residuals(nets: list, seed: int = 42) -> dict:
    """A1 DPP 切分(Agent4Sci: 测试集爬坡期物理隔离):
    net -> "train"|"val"|"test", 60/20/20, 按 net 名确定性 hash
    (跨轮稳定, 同 net 永远同 split)。"""
    import hashlib
    out = {}
    for n in nets:
        h = int(hashlib.md5(
            (str(seed) + ":" + str(n)).encode()).hexdigest()[:8], 16)
        out[str(n)] = ("train" if h % 10 < 6 else
                       "val" if h % 10 < 8 else "test")
    return out


def load_split(path: Path) -> dict:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {}


def freeze_snapshot(path: Path, pairs: list, taxonomy: dict,
                    plans: dict, fp: str = "") -> dict:
    """C1 残差快照冻结: 同残差集 A/B 对照的 join 键。
    pairs(全量行)+taxonomy(类x网)+plans(geomir 整包)+板指纹。"""
    snap = {"ts": round(time.time()), "fp": fp,
            "pairs": list(pairs or []),
            "taxonomy": {k: list(v) for k, v in
                         (taxonomy or {}).items()},
            "plans": plans or {}}
    try:
        Path(path).write_text(json.dumps(snap, ensure_ascii=False),
                              encoding="utf-8")
    except OSError:
        pass    # ok-silent: 落盘失败=对照退化为不同集(记录告警)
    return snap


def load_snapshot(path: Path) -> dict:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {}


def load_grind(path: Path) -> dict:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {}
