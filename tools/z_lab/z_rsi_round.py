# -*- coding: utf-8 -*-
"""z_rsi_round — 内环性能 RL 训练环(流派一: 磨山即训练).

用法: z_rsi_round.py BASE_DIR [ROUNDS=3] [STEPS=8]
每轮(OpenRSI L3: 验证轨迹回流训练改进者):
  1. train 窗: 探索开(Thompson+ε) 跑 N 微步(train split)
  2. journal 累积 -> JournalPolicy 岭回归拟合(上尾 ×4 加权)
  3. test 窗: 探索关(纯利用) 跑 N 微步(test split, DPP 隔离)
  4. 曲线点入账: {win_rate, sum_d_obj, gain_per_hour}
产出: z_lab/z_rsi_curve.json —— **学习曲线上行=训练有效,
不上行=诚实证伪**(验收判据, 禁单点报喜)。
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

LAB = Path("E:/DATA/vscode/ZCODE/z_lab")
REPO = Path("E:/DATA/vscode/ZCODE/flux-neurotransmitter")
sys.path.insert(0, str(REPO))

BASE = Path(sys.argv[1]) if len(sys.argv) > 1 else LAB / "z_v/FC_L13"
ROUNDS = int(sys.argv[2]) if len(sys.argv) > 2 else 3
STEPS = int(sys.argv[3]) if len(sys.argv) > 3 else 8

from fluxpcb.kernel import policy_rsi as prsi            # noqa: E402
from fluxpcb.kernel import harness as kharness           # noqa: E402

# 冻结套件(跨轮)
FIXED = kharness.generate_fixed_tests(
    "RSI 训练环", [], log=lambda m: None,
    frozen_path=LAB / "fixed_tests.json")

curve = {"base": str(BASE), "rounds": [], "ts": round(time.time())}
journal_rows = []          # 累积转移(features+reward_norm+split)
_journal_path = LAB / "journal_rows.json"
_seen_keys = set()


def _merge_rows(rows):
    """journal 合并落盘((ts,op,cls,split) 去重) —— 跨重启可累积."""
    global journal_rows
    fresh = []
    for x in rows:
        k = (x.get("ts"), x.get("op"), x.get("cls"), x.get("split"))
        if k not in _seen_keys:
            _seen_keys.add(k)
            journal_rows.append(x)
            fresh.append(x)
    if fresh:
        try:
            _journal_path.write_text(
                json.dumps(journal_rows, ensure_ascii=False),
                encoding="utf-8")
        except OSError:
            pass    # 落盘失败不挡环(内存账继续)
    return fresh


# 启动回灌(断点续跑: 旧轮 journal 不浪费)
try:
    if _journal_path.exists():
        for x in json.loads(_journal_path.read_text(encoding="utf-8")):
            k = (x.get("ts"), x.get("op"), x.get("cls"), x.get("split"))
            if k not in _seen_keys:
                _seen_keys.add(k)
                journal_rows.append(x)
except (OSError, ValueError):
    pass
for _wsd in (LAB / "z_v").glob("EVO_r*_train/pcb/board/grind_state.json"):
    try:
        _gd = json.loads(_wsd.read_text(encoding="utf-8"))
        _sp = _wsd.parent / "residual_split.json"
        _split_of = (json.loads(_sp.read_text(encoding="utf-8"))
                     if _sp.exists() else {})
        _merge_rows(prsi.collect_journal(_gd, _split_of))
    except (OSError, ValueError):
        pass

# 断点续跑(环境性清场防御: 机器重启/进程终止后从缺轮继续)
_prior = None
try:
    _pp = LAB / "z_rsi_curve.json"
    if _pp.exists():
        _prior = json.loads(_pp.read_text(encoding="utf-8"))
        curve["rounds"] = _prior.get("rounds") or []
        curve["verdict"] = _prior.get("verdict")
except (OSError, ValueError):
    _prior = None
_done_rounds = {r.get("round") for r in curve["rounds"]
                if (r.get("train") or {}).get("steps") is not None
                and (r.get("test") or {}).get("steps") is not None}


def _run_window(tag: str, split: str, explore: bool) -> dict:
    os.environ["FLUXNT_RSI_EXPLORE"] = "1" if explore else "0"
    out = LAB / ("_rsi_%s.json" % tag)
    errlog = LAB / ("_rsi_%s.err.log" % tag)
    t0 = time.time()
    r = subprocess.run(
        [sys.executable, str(LAB / "z_rollout.py"), str(BASE),
         str(LAB / "_rsi_variant.json"), tag, str(out), split],
        capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=14400)
    # 子进程输出落盘(空窗事故教训: 静默吞错 = 盲飞)
    try:
        errlog.write_text(((r.stdout or "") + "\n--stderr--\n"
                           + (r.stderr or ""))[-4000:], encoding="utf-8")
    except OSError:
        pass
    res = {}
    if out.exists():
        res = json.loads(out.read_text(encoding="utf-8"))
    res["wall"] = round(time.time() - t0)
    # 新算子过滤(曲线计数教训: ops 含 BASE 历史, wins=历史胜场)
    res["ops"] = [o for o in (res.get("ops") or [])
                  if isinstance(o, dict) and (o.get("ts") or 0) >= t0 - 10]
    return res


# 变体(默认政策; 训练对象是 policy 权重不是旋钮 —— 参数天花板定律)
(LAB / "_rsi_variant.json").write_text(json.dumps({
    "id": "rsi-default",
    "ops_params": {}, "policy": {"stall_penalty": 0.4,
                                 "blacklist_fails": 2,
                                 "upgrade_stride": 2},
    "budget": {"steps_per_window": STEPS},
    "objective": {"w_opens": 1.0, "w_clearance": 0.2}}),
    encoding="utf-8")

policy = prsi.JournalPolicy()
for r in range(ROUNDS):
    if r in _done_rounds:
        print("[round %d] 已有记录, 跳过(断点续跑)" % r)
        continue
    pt = {"round": r}
    # 1. train 窗(探索)
    tr = _run_window("r%d_train" % r, "train", explore=True)
    tr_ops = tr.get("ops") or []
    tr_wins = sum(1 for o in tr_ops if isinstance(o, dict)
                  and o.get("improved"))
    pt["train"] = {"gain": tr.get("gain"), "wins": tr_wins,
                   "steps": len(tr_ops), "wall": tr.get("wall")}
    # 2. journal 累积 + 拟合(train 行)
    gpath = LAB / ("_rsi_r%d_train_gs.json" % r)
    # rollout 的 grind_state 在工作区副本里 —— 由 out 附带不了, 直接
    # 从 EVO_ 工作区读
    wsd = LAB / "z_v" / ("EVO_r%d_train" % r) / "pcb" / "board" / \
        "grind_state.json"
    if wsd.exists():
        gd = json.loads(wsd.read_text(encoding="utf-8"))
        _sp = wsd.parent / "residual_split.json"
        _split_of = (json.loads(_sp.read_text(encoding="utf-8"))
                     if _sp.exists() else {})
        _merge_rows(prsi.collect_journal(gd, _split_of))
    fit_rmse = -1.0
    if len(journal_rows) >= 5:
        fit_rmse = policy.fit(
            [x["features"] for x in journal_rows],
            [float(x.get("reward_norm") or 0) for x in journal_rows])
    pt["fit"] = {"rows": len(journal_rows), "rmse": fit_rmse}
    # 3. test 窗(纯利用, DPP test split)
    te = _run_window("r%d_test" % r, "test", explore=False)
    te_ops = te.get("ops") or []
    te_wins = sum(1 for o in te_ops if isinstance(o, dict)
                  and o.get("improved"))
    pt["test"] = {"gain": te.get("gain"), "wins": te_wins,
                  "steps": len(te_ops), "wall": te.get("wall")}
    # 4. 曲线指标(每 wall-hour 增益 = 样本效率+策略质量合成)
    for side in ("train", "test"):
        w = max(pt[side]["wall"] or 1, 1)
        pt[side]["gain_per_hour"] = round(
            (pt[side]["gain"] or 0) / w * 3600.0, 3)
    curve["rounds"].append(pt)
    (LAB / "z_rsi_curve.json").write_text(
        json.dumps(curve, ensure_ascii=False, indent=1), encoding="utf-8")
    print("[round %d] train gain=%s wins=%s | test gain=%s wins=%s | "
          "fit rmse=%s rows=%d"
          % (r, pt["train"]["gain"], pt["train"]["wins"],
             pt["test"]["gain"], pt["test"]["wins"],
             fit_rmse, len(journal_rows)))

# 学习曲线判定(验收: 上行=有效, 否则证伪)
if len(curve["rounds"]) >= 2:
    first, last = curve["rounds"][0], curve["rounds"][-1]
    lg = last["test"].get("gain") or 0
    fg = first["test"].get("gain") or 0
    lw = last["test"].get("wins") or 0
    fw = first["test"].get("wins") or 0
    # 严格判据: 至少一项严格上行且无项下行 —— 0>=0 平局不算上行
    # (退化通过教训: 首版 0>=0 计 learning-curve-up 是假阳性)
    strict_up = (lg > fg or lw > fw) and lg >= fg and lw >= fw
    flat = (lg == fg and lw == fw)
    curve["verdict"] = ("learning-curve-up" if strict_up
                        else ("flat(平局, 非上行)" if flat
                              else "down(诚实证伪)"))
    (LAB / "z_rsi_curve.json").write_text(
        json.dumps(curve, ensure_ascii=False, indent=1), encoding="utf-8")
    print("学习曲线判定:", curve["verdict"])
