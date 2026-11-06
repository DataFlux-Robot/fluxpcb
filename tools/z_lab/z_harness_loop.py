# -*- coding: utf-8 -*-
"""z_harness_loop — 层2 Harness Loop: 复盘聚类器(工具进化循环的机械半边).

触发: 层1 z_agent_loop 报 stall/only_gaps_left 时运行(不做常驻).
职责: 读 journal+report -> 同类失败聚类(>=2 次=工具缺陷信号) ->
     出 z_harness_todo.md(缺陷清单+回归命令). **改代码是 agent 的活**,
     本脚本只做复盘定位 —— 修完跑反例回归(tests/)再提交, 闭环完成.

判据: 修复数 x 每修复带来的层1效率增量(闭合/轮). 不上行=修错, 回滚.
"""
import json
import re
from collections import Counter
from pathlib import Path

LAB = Path("E:/DATA/vscode/ZCODE/z_lab")
JOURNAL = LAB / "z_agent_journal.jsonl"
REPORT = LAB / "z_agent_loop_report.json"
TODO = LAB / "z_harness_todo.md"


def err_class(err):
    """失败聚类; 返回 None=不计缺陷信号(如旧版未记 err 的遗留行)."""
    if not err:
        return None
    if "timeout" in err:
        return "timeout:长对需分段收割算子(靶2a)"
    if "via-vs-track" in err or "stale-plan" in err:
        return "blocked:拆死残段未泛化(靶1)"
    if "no path" in err.lower() or "no-path" in err:
        return "no_path:缺走廊保留复合算子/板级动作(靶2b/靶3)"
    if "no_gain" in err:
        return "no_gain:算子假成功或形态不适用(需形态再判)"
    if err.startswith("CRASH"):
        return "crash:驱动崩溃(stderr 在 journal)"
    return "other:%s" % re.sub(r"[\d.]+", "#", err)[:60]


rows = []
if JOURNAL.exists():
    for line in JOURNAL.read_text(encoding="utf-8").splitlines():
        try:
            rows.append(json.loads(line))
        except ValueError:
            pass

rep = {}
if REPORT.exists():
    rep = json.loads(REPORT.read_text(encoding="utf-8"))

# closed 判定: landed=True 或 verdict 前缀 closed(兼容新旧 schema)
fail_rows = [r for r in rows
             if not (r.get("landed") is True
                     or str(r.get("verdict", "")).startswith("closed"))]
cls = Counter()
legacy = 0
for r in fail_rows:
    c = err_class(r.get("err") or r.get("error") or "")
    if c is None:
        legacy += 1        # 旧版未记 err —— 不算缺陷信号
        continue
    cls[c] += 1
for net, g in (rep.get("gaps") or {}).items():
    c = err_class(g) or err_class(str(g))
    if c:
        cls[c] += 1

todo = ["# Harness Loop 复盘清单 (由 z_harness_loop 生成)",
        "", "- 层1 终态: opens=%s done=%s"
        % (rep.get("opens_end"), rep.get("done")),
        "- 尝试 %d, 闭合 %d, 失败 %d (旧版遗留无 err 行 %d 不计缺陷)"
        % (len(rows), len(rows) - len(fail_rows), len(fail_rows), legacy),
        "", "## 同类失败聚类 (>=2 次 = 工具缺陷信号)", ""]
flagged = []
for k, v in cls.most_common():
    mark = "**缺陷**" if v >= 2 else "观察"
    todo.append("- [%s] %s × %d" % (mark, k, v))
    if v >= 2:
        flagged.append(k)

todo += ["", "## 修复规程 (层2一轮 = 修一处工具缺陷)", "",
         "1. 定位: 上表最高频缺陷 -> 对应工具(z_harvest/z_surg_stub/z_agent_loop)",
         "2. 修 harness (机制级, 禁 trick)",
         "3. 回归: python -m pytest tests/ -x -q (反例引理须绿)",
         "4. 提交 (K 编号+反例账), 层1 效率增量记入本文件末尾", "",
         "## 效率记账", "",
         "| 修复 | 层1 闭合/轮(前->后) |", "|---|---|"]
for line in JOURNAL.read_text(encoding="utf-8").splitlines()[-1:] if JOURNAL.exists() else []:
    todo.append("| (待填) | |")

TODO.write_text("\n".join(todo) + "\n", encoding="utf-8")
print("[harness] 复盘完成: %d 尝试, 缺陷信号 %d 类 -> %s"
      % (len(rows), len(flagged), TODO.name))
for k in flagged:
    print("  缺陷:", k)
