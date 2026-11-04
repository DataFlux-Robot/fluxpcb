# -*- coding: utf-8 -*-
"""z_stage5 — 性能 RL 总验收: 四条曲线聚合(阶段5).

用法: z_stage5.py
四曲线(计划验收线, 曲线制禁单点):
  ① 残差闭合率/wall-hour 随轮次 —— z_rsi_curve.json
  ② 跨板存活率(归真形态) —— arena_report.json
  ③ fc 板 10 开 -> 0 推进速率 —— 训练窗/arena 的 fc opens 序列
  ④ JLC 工艺通过率 —— _jlc_test fab 账(规则对齐后)
产出: z_lab/z_stage5_report.json + stdout 摘要。
缺数据的曲线诚实标注 in-flight, 不虚报。
"""
import json
from pathlib import Path

LAB = Path("E:/DATA/vscode/ZCODE/z_lab")


def _load(p):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


rep = {"curves": {}}

# ① 学习曲线
c = _load(LAB / "z_rsi_curve.json")
if c and c.get("rounds"):
    pts = [{"round": r.get("round"),
            "train_gph": (r.get("train") or {}).get("gain_per_hour"),
            "train_wins": (r.get("train") or {}).get("wins"),
            "test_wins": (r.get("test") or {}).get("wins"),
            "fit_rows": (r.get("fit") or {}).get("rows")}
           for r in c["rounds"]]
    rep["curves"]["learning"] = {"points": pts,
                                 "verdict": c.get("verdict")}
else:
    rep["curves"]["learning"] = "in-flight"

# ② 跨板存活(归真)
a = _load(LAB / "arena_report.json")
if a and a.get("survival"):
    rep["curves"]["survival"] = {
        "mode": a.get("mode"),
        "per_candidate": {k: v for k, v in a["survival"].items()},
        "arms": [{k: x.get(k) for k in
                  ("board", "PASS", "wins", "by_op",
                   "opens_before", "opens_after")}
                 for x in (a.get("arms") or [])]}
else:
    rep["curves"]["survival"] = "in-flight"

# ③ fc 推进序列(训练窗 + arena 的 fc 板 opens 前后)
seq = []
if c and c.get("rounds"):
    for r in c["rounds"]:
        for side in ("train", "test"):
            s = r.get(side) or {}
            if s.get("opens_before") is not None:
                seq.append([r.get("round"), side,
                            s.get("opens_before"), s.get("opens_after")])
if a:
    for x in (a.get("arms") or []):
        if x.get("board") == "fc" and x.get("opens_before") is not None:
            seq.append(["arena", "fc",
                        x.get("opens_before"), x.get("opens_after")])
fcb = _load(LAB / "z_v" / "FC_L13" / "v_result.json")
rep["curves"]["fc_progress"] = {
    "series": seq,
    "baseline_fc_l13": 10,
    "note": "目标 10 开 -> 0; 序列为各窗 fc 板 opens 前后对"}

# ④ JLC 工艺通过率
fw = _load(LAB / "_jlc_test" / "board" / "fab_waivers.json")
if fw:
    g = fw.get("genuine") or []
    w = fw.get("waivers") or []
    rep["curves"]["fab_pass"] = {
        "genuine_residuals": len(g),
        "policy_waivers": len(w),
        "probe": (fw.get("probe") or {}).get("probe"),
        "pass_rate_proxy": round(
            len(w) / max(1, len(w) + len(g)), 3),
        "note": "分母=豁免+真残差(规则对齐后口径)"}
else:
    rep["curves"]["fab_pass"] = "in-flight"

(LAB / "z_stage5_report.json").write_text(
    json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
print(json.dumps(rep, ensure_ascii=False, indent=1)[:1200])
