# -*- coding: utf-8 -*-
"""z_arena — 多板对抗验证场(阶段5③: 广度压力代替方法论自觉).

用法: z_arena.py CAND1.json [CAND2.json ...] [N]
board_pool/manifest.json 板池 x 候选 -> 每 (板,候选) 跑 z_ab 双臂
(tag 带板维度), 汇总**跨板存活率**:
  survival = PASS 板数 / 参测板数
  < 2/4 板存活 = 自动降权为 trick(诚实标注, 不是机制)
冻结套件/审计纪律由 z_ab 内部执行; 本层只做调度与存活记账
(连同 skills_ledger 的 'survived:<board>' 记账)。
产出: z_lab/arena_report.json。
"""
import json
import subprocess
import sys
import time
from pathlib import Path

LAB = Path("E:/DATA/vscode/ZCODE/z_lab")
POOL = LAB / "board_pool"
N = 8


def _load_out(p):
    try:
        d = json.loads(Path(p).read_text(encoding="utf-8"))
        return d if d.get("opens_before") is not None else None
    except (OSError, ValueError):
        return None


def main():
    global N
    NATURAL = "--natural" in sys.argv
    cands = [Path(a) for a in sys.argv[1:]
             if not a.isdigit() and a != "--natural"]
    if not cands:
        print("用法: z_arena.py CAND1.json [CAND2.json ...] [N] [--natural]")
        sys.exit(1)
    if any(a.isdigit() for a in sys.argv[1:]):
        N = int([a for a in sys.argv[1:] if a.isdigit()][-1])
    manifest = json.loads(
        (POOL / "manifest.json").read_text(encoding="utf-8"))
    boards = [b for b in manifest["boards"] if b.get("artifacts_ok")]
    report = {"ts": round(time.time()), "n_steps": N,
              "mode": "natural" if NATURAL else "pinned", "arms": []}
    for cand_path in cands:
        cid = json.loads(cand_path.read_text(
            encoding="utf-8")).get("id", cand_path.stem)
        surv = {}
        for binfo in boards:
            base = Path(binfo["path"])
            tag = "%s__%s" % (cid, binfo["pool_name"])
            if NATURAL:
                # 归真形态(修钉扎 confound): 自然调度跑环, 判据=净进步
                # 且零损伤; 机制归因(harvest 等赢家)单列不进判据。
                # 断点续跑: per-board out 落盘即缓存, 重跑跳过
                out = LAB / ("_natural_%s.json" % tag.replace("/", "_"))
                r = None
                if not (out.exists() and _load_out(out)):
                    r = subprocess.run(
                        [sys.executable, str(LAB / "z_rollout.py"),
                         str(base), str(LAB / "_rsi_variant.json"), tag,
                         str(out), "all"],
                        capture_output=True, text=True, encoding="utf-8",
                        errors="replace", timeout=14400)
                ok = False
                detail = {}
                if out.exists():
                    d = json.loads(out.read_text(encoding="utf-8"))
                    ops = [o for o in (d.get("ops") or [])
                           if isinstance(o, dict)]
                    wins = sum(1 for o in ops if o.get("improved"))
                    by_op = {}
                    for o in ops:
                        if o.get("improved"):
                            by_op[o.get("op")] = \
                                by_op.get(o.get("op"), 0) + 1
                    ok = bool((d.get("opens_after") or 99)
                              <= (d.get("opens_before") or 0)
                              and wins >= 1)
                    if (d.get("opens_before") or 0) <= 0:
                        ok = None    # 成品板(0 开路)无可测, 不计 FAIL
                    detail = {"wins": wins, "by_op": by_op,
                              "opens_before": d.get("opens_before"),
                              "opens_after": d.get("opens_after"),
                              "wall": d.get("wall_s")}
                else:
                    detail = {"error": (r.stdout or "")[-150:]}
                surv[binfo["pool_name"]] = ok
                report["arms"].append({"candidate": cid,
                                       "board": binfo["pool_name"],
                                       "PASS": ok, **detail})
                print("[%s x %s natural] %s %s"
                      % (cid, binfo["pool_name"],
                         "PASS" if ok else "FAIL", detail.get("by_op")))
                continue
            r = subprocess.run(
                [sys.executable, str(LAB / "z_ab.py"), str(base),
                 str(cand_path), str(N)],
                capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=14400)
            # z_ab 内部 tag = 'AB_<cid>__<BASE.name>'
            vpath = (LAB / "z_v" / ("AB_%s__%s" % (
                cid.replace("/", "_"), base.name))) / "verdict.json"
            ok = None
            detail = {}
            if vpath.exists():
                v = json.loads(vpath.read_text(encoding="utf-8"))
                ok = bool(v.get("PASS"))
                detail = {"base_d": (v.get("base") or {})
                          .get("sum_d_obj"),
                          "cand_d": (v.get("cand") or {})
                          .get("sum_d_obj"),
                          "fixed": (v.get("fixed") or {})
                          .get("verdicts")}
            else:
                ok = False
                detail = {"error": (r.stdout or "")[-150:]}
            surv[binfo["pool_name"]] = ok
            report["arms"].append({"candidate": cid,
                                   "board": binfo["pool_name"],
                                   "PASS": ok, **detail})
            print("[%s x %s] %s" % (cid, binfo["pool_name"],
                                    "PASS" if ok else "FAIL"))
        n_pass = sum(1 for v in surv.values() if v)
        n_tested = sum(1 for v in surv.values() if v is not None)
        survival = round(n_pass / max(1, n_tested), 2)
        report.setdefault("survival", {})[cid] = {
            "survival": survival, "per_board": surv,
            "tested": n_tested,
            "verdict": ("mechanism" if survival >= 0.5
                        else "trick(跨板存活不足, 降权)")}
        print("== %s 跨板存活率 %.2f -> %s"
              % (cid, survival,
                 report["survival"][cid]["verdict"]))
    (LAB / "arena_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=1),
        encoding="utf-8")
    print("arena_report.json written")


if __name__ == "__main__":
    main()
