# -*- coding: utf-8 -*-
"""z_adopt — 采纳链驱动(活体全链最后一环): A/B 裁决 -> 审计 ->
采纳(PASS+clean)或证伪入账 -> 工作区 Git 提交 -> skill 账本.

用法: z_adopt.py CAND_JSON [BASE_NAME]
零手写: 候选/裁决全部来自流水线产物, 本驱动只做裁决执行。
"""
import json
import subprocess
import sys
from pathlib import Path

LAB = Path("E:/DATA/vscode/ZCODE/z_lab")
REPO = Path("E:/DATA/vscode/ZCODE/flux-neurotransmitter")
sys.path.insert(0, str(REPO))

cand_path = Path(sys.argv[1])
cand = json.loads(cand_path.read_text(encoding="utf-8"))
cid = cand.get("id", cand_path.stem)
base_name = sys.argv[2] if len(sys.argv) > 2 else "FC_L2"

from fluxpcb.kernel import harness                   # noqa: E402

vdir = LAB / "z_v" / ("AB_%s__%s" % (cid.replace("/", "_"), base_name))
verdict = json.loads((vdir / "verdict.json").read_text(encoding="utf-8"))

# 审计(独立审计员四类作弊检查) —— 载荷含 hypothesis/verdict/boldness
gd = {}
gs = vdir.parent / (vdir.name + "_cand") / "pcb" / "board" / "grind_state.json"
if gs.exists():
    gd = json.loads(gs.read_text(encoding="utf-8"))
audit = harness.audit_trajectory(gd, [cand], log=print)
print("审计:", audit.get("verdict"), audit.get("source"),
      (audit.get("checklist_answers") or {}))

record = {"candidate": cid, "PASS": bool(verdict.get("PASS")),
          "audit": {k: audit.get(k) for k in
                    ("verdict", "findings", "source")},
          "verdict_summary": {
              "base_d": (verdict.get("base") or {}).get("sum_d_obj"),
              "cand_d": (verdict.get("cand") or {}).get("sum_d_obj"),
              "fixed": (verdict.get("fixed") or {}).get("verdicts")}}

if verdict.get("PASS") and audit.get("verdict") == "clean":
    # 采纳: 注册表 + skill 存活 + 工作区提交
    reg = LAB / "ops_registry.json"
    data = {}
    if reg.exists():
        try:
            data = json.loads(reg.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            data = {}
    key = cand.get("op") or cid
    ent = data.get(key) or {}
    ent["status"] = "promoted"
    ent.setdefault("evidence", []).append("ab:%s" % cid)
    data[key] = ent
    reg.write_text(json.dumps(data, indent=1), encoding="utf-8")
    for sk in (cand.get("skill_refs") or []):
        harness.skill_ledger(skill=sk, outcome="adopted")
    record["adopted"] = key
    print("采纳:", key)
else:
    reason = ("FAIL" if not verdict.get("PASS") else "审计不净")
    for sk in (cand.get("skill_refs") or []):
        harness.skill_ledger(skill=sk,
                             outcome="falsified:%s" % cid)
    record["adopted"] = None
    record["falsified_reason"] = reason
    print("证伪入账:", cid, reason)

# 工作区 Git 提交(无论采纳/证伪 —— 三账都可追溯)
ws = LAB / ("z_v/ADOPT_%s" % cid.replace("/", "_").replace(":", "_"))
ws.mkdir(parents=True, exist_ok=True)
(ws / "candidate.json").write_text(
    json.dumps(cand, ensure_ascii=False), encoding="utf-8")
(ws / "verdict.json").write_text(
    json.dumps(verdict, ensure_ascii=False), encoding="utf-8")
(ws / "record.json").write_text(
    json.dumps(record, ensure_ascii=False), encoding="utf-8")
c = harness.commit_workspace(ws, "%s: %s" % (
    "adopt" if record["adopted"] else "falsified", cid))
print("commit:", c[:8] or "n/a")

# 经验卡入册(OpenRSI: 验证/证伪轨迹都回流规划器)
cards = LAB / "experience_cards"
cards.mkdir(exist_ok=True)
card = {
    "outcome": "adopted" if record["adopted"] else "falsified",
    "hypothesis": cand.get("hypothesis", ""),
    "mechanism": cand.get("changes", "")[:120],
    "evidence": json.dumps(record.get("verdict_summary"), ensure_ascii=False)[:120],
    "ts": round(__import__("time").time()),
}
(cards / ("%s.json" % cid.replace("/", "_").replace(":", "_"))).write_text(
    json.dumps(card, ensure_ascii=False), encoding="utf-8")
print(json.dumps(record, ensure_ascii=False))
