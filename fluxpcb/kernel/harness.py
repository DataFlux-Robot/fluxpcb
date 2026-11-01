# -*- coding: utf-8 -*-
"""HarnessPro 完整移植: 三 LLM 角色 + 固定测试生成器 + Git 工作区.

检讨结论的直接产物 — HarnessPro 的核心是**三个 LLM agent**,
不是参数洗牌:
  ① 轨迹分析器(TrajectoryAnalyzer): LLM 读 rollout 原始轨迹
     (grind_state/journal/步日志) -> 失败+冗余+模式诊断;
  ② 进化规划器(EvolutionPlanner): 读自身诊断+跨变体 diff ->
     结构化修改提案 {changes, quality_impact, cost_impact, risks},
     **落点任意**(不再限 M1/M2/M3);
  ③ harness 编辑器(HarnessEditor): 在隔离工作区应用修改并
     Git 提交(diff 可比、可回滚);
  ④ 固定测试生成器(FixedTestGenerator): LLM 从任务+初始轨迹
     **一次性**生成评测套件, 此后冻结(HarnessPro 教训: 固定测试
     比 LLM 自评判失败召回翻倍)。

提示词强制两节(HarnessPro 原文纪律):
  * 反作弊: 禁跳过必需工作/放弃难题/削弱验证来省成本;
  * 证据驱动: 禁预设路线/阈值/分类法, 修改必须从证据推导。

三模式链(与全系统同构): llm -> rule fallback, 离线不冒充
(输出标注 planner: llm|rule)。
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path


def _llm():
    from .. import llm as _l
    return _l.llm


ANTI_CHEAT = (
    "【反作弊纪律】禁止以下行为: 跳过必需工作、放弃困难残差、"
    "削弱验证/评测标准来换取表面分数。真实效率增益才算改进。")
EVIDENCE_DRIVEN = (
    "【证据驱动】修改必须从给出的证据(轨迹/诊断/diff)推导; "
    "禁止输出预设路线/固定阈值/现成分类法——落点由证据指认。")


def research_context(knowhow: Path, grind_data: dict,
                     max_items: int = 3,
                     battery_path=None, literature: list = None) -> dict:
    """A3 research skill: 规划前语料挖掘 — KNOWHOW 条目 + 失败档案
    摘录 + 研究电池(research_battery.json, 之前是无人消费的死端)
    + 外部文献摘要(academic-search 产出)。证据不是预设路线。"""
    excerpts = []
    try:
        txt = Path(knowhow).read_text(encoding="utf-8")
        heads = [i for i, ln in enumerate(txt.splitlines())
                 if ln.startswith("## K")]
        for i in heads[-max_items:]:
            block = txt.splitlines()[i:i + 4]
            excerpts.append(" | ".join(
                b.strip()[:80] for b in block if b.strip()))
    except (OSError, ValueError):
        pass    # ok-silent: 语料缺失=规划器少证据不失败
    fails = (grind_data or {}).get("failed_branches") or []
    battery = []
    if battery_path:
        try:
            bat = json.loads(Path(battery_path).read_text(
                encoding="utf-8"))
            for dir_key, v in list(bat.items()):
                if isinstance(v, dict):
                    sig = v.get("summary") or v.get("finding") or ""
                    if sig:
                        battery.append("%s: %s"
                                       % (dir_key, str(sig)[:120]))
        except (OSError, ValueError):
            pass    # ok-silent: 同上
    # skill 索引(规划器可见: 提案可引用 skill:name 消费已验证机制)
    skills = []
    try:
        sdir = Path(__file__).resolve().parents[2] / ".agents" / "skills"
        for sk in sorted(sdir.glob("*/SKILL.md")):
            name = sk.parent.name
            desc = ""
            for ln in sk.read_text(encoding="utf-8").splitlines():
                if ln.startswith("description:"):
                    desc = ln.split(":", 1)[1].strip()[:100]
                    break
            skills.append("%s: %s" % (name, desc))
    except (OSError, ValueError):
        pass    # ok-silent: 无 skill 库=规划器少索引不失败
    return {"knowhow": excerpts,
            "failed_branches": fails[-8:],
            "research_battery": battery,
            "literature": [str(x)[:160] for x in (literature or [])][:8],
            "skills": skills,
            "note": "语料是证据, 不是预设路线(落点仍由证据推导); "
                    "提案可引用 skill:<name> 消费已验证机制"}


_CT_CACHE = {}


def contracts_preflight(repo_root=None) -> bool:
    """contracts_preflight 评测器: 六条反例引理回归(tests/
    test_contracts.py)跑一次并缓存 —— fixed suite 的 pytest 臂。"""
    if "ok" not in _CT_CACHE:
        try:
            root = Path(repo_root or
                        Path(__file__).resolve().parents[2])
            r = __import__("subprocess").run(
                [__import__("sys").executable, "-m", "pytest",
                 str(root / "tests" / "test_contracts.py"), "-q"],
                capture_output=True, timeout=120)
            _CT_CACHE["ok"] = (r.returncode == 0)
        except Exception:
            _CT_CACHE["ok"] = False
    return _CT_CACHE["ok"]


def jev_decide(state: str, questions: dict) -> dict:
    """JEV 决策模型(NeoHorse-Jev-4B, /v1/decision): 结构化判定。
    questions = {qid: {"type": "noul|choice|score", ...}}
    返回 answers dict; 不可用时 {"ok": False, "error": ...}。"""
    import os
    import urllib.request
    base = os.environ.get("FLUXNT_LLM_BASEURL",
                          "https://tokenrhythm.studio/v1")
    key = os.environ.get("FLUXNT_LLM_API_KEY", "")
    if not key:
        return {"ok": False, "error": "no api key"}
    body = {"model": "NeoHorse-Jev-4B", "state": state,
            "questions": questions}
    try:
        req = urllib.request.Request(
            base.rstrip("/") + "/decision",
            data=json.dumps(body).encode(),
            headers={"Authorization": "Bearer " + key,
                     "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=120) as r:
            d = json.loads(r.read())
        return {"ok": True, "answers": d.get("answers", {}),
                "raw": d}
    except Exception as e:
        return {"ok": False, "error": str(e)[:120]}


_AUDIT_SYS = (
    "你是 PCB 自动设计系统的**独立审计员**(Agent4Sci 三阶段协议)。"
    "输入是完整轨迹(算子步/回滚/黑名册/变异提案)。审查四类作弊"
    "(JSON 输出 {verdict: 'clean'|'suspect', findings: [str]}): "
    "1)选择性报告(报喜瞒败) 2)验证削弱(改评测/降标准换分数) "
    "3)指标操控(objective 与真实改进脱钩) 4)硬编码(对特定场景"
    "写死结果)。另给 checklist_answers: {creative_or_tuning: "
    "'creative'|'tuning', evidence: str}。" + ANTI_CHEAT)


def audit_trajectory(grind_data: dict, proposals: list,
                     log=print) -> dict:
    """A2 独立审计: 全轨迹(不经 slim 截断)反作弊审查。
    LLM 离线 -> 规则审计(结构核对), 输出恒带 source 标注。"""
    payload = {
        "ops_sample": [
            {"net": k, "op": o.get("op"),
             "d_obj": o.get("d_obj"), "rolled_back": o.get(
                 "rolled_back"), "summary": o.get("summary", "")[:60]}
            for k, v in (grind_data.get("residuals") or {}).items()
            for o in (v.get("ops") or [])][:40],
        "blacklists": {k: v.get("blacklist")
                       for k, v in (grind_data.get("residuals")
                                    or {}).items()},
        "proposals": [{k2: pr.get(k2) for k2 in
                       ("changes", "cost_impact", "risks",
                        "hypothesis", "hypothesis_class",
                        "verdict", "boldness")}
                      for pr in (proposals or [])][:5],
    }
    llm = _llm()
    if llm.online:
        try:
            rep = llm.complete(_AUDIT_SYS,
                               json.dumps(payload,
                                          ensure_ascii=False))
            if rep.get("ok"):
                d = _extract_json(rep.get("text", ""))
                if d:
                    d["source"] = "llm"
                    return d
            log("  [harness] 审计LLM失败->rule: %s"
                % (rep.get("error") or "不可解析")[:60])
        except Exception as e:
            log(f"  [harness] 审计LLM异常->rule: {str(e)[:60]}")
    # 规则审计: 结构核对(选报/削弱的弱代理)
    n_ops = len(payload["ops_sample"])
    n_rb = sum(1 for o in payload["ops_sample"]
               if o.get("rolled_back"))
    return {"verdict": "clean" if n_rb <= n_ops * 0.5 else "suspect",
            "findings": [f"回滚率 {n_rb}/{n_ops}"],
            "checklist_answers": {"creative_or_tuning": "tuning",
                                  "evidence": "rule fallback"},
            "source": "rule"}


def _extract_json(text: str) -> dict:
    t = (text or "").strip()
    # 去 markdown 代码围栏(mimo/deepseek 常见形态)
    if t.startswith("```"):
        t = t.split("`", 3)[-1] if t.count("`") >= 6 else             t.strip("`")
        t = t.removeprefix("json").removeprefix("JSON").strip()
    try:
        i = t.find("{")
        j = t.rfind("}")
        if i >= 0 and j > i:
            return json.loads(t[i:j + 1])
    except (ValueError, IndexError):
        pass
    return {}


# ---------------- ① 轨迹分析器 ----------------

_ANA_SYS = (
    "你是 PCB 自动设计系统的轨迹分析器。输入是若干次磨山 rollout "
    "的原始记录(算子步、objective 轨迹、回滚、耗时)。产出双诊断"
    "(JSON): {failure_modes: [{where, pattern, evidence}], "
    "redundancy: [{where, wasted_cost, evidence}], "
    "insights: [str], cost_analysis: str}。"
    "failure_modes 不只报'哪里失败', 要找**模式**(如某类残差总在"
    "第N步后反弹); redundancy 找白花钱的环节。" + ANTI_CHEAT)


def analyze_trajectories(rollouts: list, log=print) -> dict:
    """LLM 双诊断; 离线/失败 -> 机械聚合 fallback(evolve.diagnose)。"""
    slim = [{
        "gain": r.get("gain"), "wall_s": r.get("wall_s"),
        "ops": [{"op": o.get("op"), "d_obj": o.get("d_obj"),
                 "d_opens": o.get("d_opens"),
                 "rolled_back": o.get("rolled_back"),
                 "summary": o.get("summary", "")[:60]}
                for o in r.get("ops", [])][:12],
    } for r in rollouts]
    llm = _llm()
    if llm.online:
        try:
            rep = llm.complete(_ANA_SYS,
                               json.dumps(slim, ensure_ascii=False))
            if rep.get("ok"):
                d = _extract_json(rep.get("text", ""))
                if d:
                    d["source"] = "llm"
                    return d
                log("  [harness] 分析器LLM输出不可解析->rule: %s"
                    % (rep.get("text", "")[:80]))
            else:
                log("  [harness] 分析器LLM调用失败->rule: %s"
                    % rep.get("error", "")[:80])
        except Exception as e:
            log(f"  [harness] 分析器LLM异常->rule: {str(e)[:60]}")
    from . import evolve
    d = evolve.diagnose(rollouts)
    d["source"] = "rule"
    return d


# ---------------- ② 进化规划器 ----------------

_PLAN_SYS = (
    "你是 PCB 自动设计系统的进化规划器(harness 改进)。输入: "
    "(a)自身轨迹诊断 (b)跨变体 diff(其他变体改了什么/效果) "
    "(c)当前 harness 配置 variant "
    "(d) research_context 研究语料(knowhow 条目/失败档案/"
    "research_battery 研究电池/literature 文献摘要) —— 提案应引用"
    "其中证据(如 'per research_battery.dir3: ...'), 不引用=无据。"
    "产出修改提案列表(JSON): "
    "{proposals: [{changes, quality_impact, cost_impact, risks, "
    "edit: {<variant字段路径>: <新值>, ...}}]}。"
    "每条提案另带四个必填字段: hypothesis(可证伪假设句, bold 提案必填),"
    "hypothesis_class: '重构问题'|'打破假设'|'换尺度'|'跨界类比'"
    "(至少 2 条不同类别), "
    "verdict: 'creative'|'tuning'"
    "(自问: 本轮是结构性创新还是超参微调?), boldness: 'bold'|'conservative'。"
    "【多方案纪律】每轮**至少 3 条提案**, 其中至少 2 条分属不同"
    "假设类别, creative 与 tuning 各至少一条 —— 单方案=单点故障, "
    "组合面才是解。"
    "至少一条 bold 提案(新机制类别, 不止改参数) —— 像大胆的科学家"
    "设计不同类别的实验, 而不是反复拧旋钮。"
    "【疯狂科学家模式】对顽固残差, 不要调参, 要**重新想象问题类别**"
    "并给出可证伪的大胆假设: 重构问题(它真的是X问题吗?)、"
    "打破隐含假设(哪些'不能动'其实可以动?)、换尺度(整区推倒是否"
    "比局部修补便宜?)、跨界类比(其他领域的解法?)。每条 bold 提案"
    "必须是'如果…会怎样'的可证伪假设句式, 附判定实验。"
    "edit 的落点可以是 variant 的任何字段(算子参数/调度策略/预算/"
    "目标权重) —— 落点由证据指认, 不设预设类别。没有参数化落点的"
    "结构性提案也要输出(edit 给 {}), 它会进入 PENDING_IMPL 队列。"
    "输出示例(严格此形态, 纯JSON无围栏): "
    "{\"proposals\": [{\"changes\": \"...\", \"quality_impact\": \"...\", "
    "\"cost_impact\": \"...\", \"risks\": \"...\", "
    "\"hypothesis\": \"如果…会怎样(可证伪句)\", "
    "\"hypothesis_class\": \"打破假设\", "
    "\"edit\": {\"policy.stall_penalty\": 0.6, "
    "\"ops_params.nudge_blocker.dist\": 0.15}}]}"
    + ANTI_CHEAT + EVIDENCE_DRIVEN)


def _parse_proposals(props: list) -> list:
    """提案解析(单源): hypothesis/hypothesis_class 不再在解析层丢失;
    无 edit 的结构性提案标 PENDING_IMPL 不丢弃(修复静默丢弃暗坑)。"""
    out = []
    for pr in props[:5]:
        edits = pr.get("edit") or {}
        if not isinstance(edits, dict):
            edits = {}
        out.append({
            "source": "llm",
            "verdict": pr.get("verdict", ""),
            "boldness": pr.get("boldness", ""),
            "hypothesis": pr.get("hypothesis", ""),
            "hypothesis_class": pr.get("hypothesis_class", ""),
            "changes": pr.get("changes", ""),
            "quality_impact": pr.get("quality_impact", ""),
            "cost_impact": pr.get("cost_impact", ""),
            "risks": pr.get("risks", ""),
            "edits": edits,
            "status": "" if edits else "PENDING_IMPL",
        })
    return out


def _discipline_ok(props: list) -> tuple:
    """多方案纪律(代码级, 不只在提示词): ≥3 条、≥2 假设类别、
    creative/tuning 各一、≥1 bold。返回 (ok, 缺口描述)。"""
    classes = {p.get("hypothesis_class") for p in props
               if p.get("hypothesis_class")}
    verdicts = {p.get("verdict") for p in props}
    bolds = {p.get("boldness") for p in props}
    gaps = []
    if len(props) < 3:
        gaps.append("条数%d<3" % len(props))
    if len(classes) < 2:
        gaps.append("假设类别%d<2" % len(classes))
    if "creative" not in verdicts or "tuning" not in verdicts:
        gaps.append("creative/tuning 不齐")
    if "bold" not in bolds:
        gaps.append("缺 bold 提案")
    return (not gaps), "; ".join(gaps)


def plan_modification(diagnosis: dict, diffs: list, variant: dict,
                      log=print, research: dict = None) -> list:
    """LLM 提案; 离线/失败 -> 规则变异 fallback(evolve.mutate)。
    research: 显式研究语料(调用方注入 research_battery/文献检索);
    缺省回退内置 research_context(knowhow+失败档案)。
    多方案纪律不满足时一次补足重问, 仍不足则诚实标 insufficient。"""
    llm = _llm()
    if llm.online:
        try:
            if research is None:
                research = research_context(
                    Path(__file__).resolve().parents[2]
                    / "KNOWHOW.md",
                    {"failed_branches":
                     (diagnosis or {}).get("failed_branches", [])},
                    battery_path=__import__("os").environ.get(
                        "FLUXNT_RESEARCH_BATTERY") or None)
            payload = {"diagnosis": diagnosis, "diffs": diffs[:3],
                       "variant": variant,
                       "research_context": research}
            rep = llm.complete(_PLAN_SYS,
                               json.dumps(payload,
                                          ensure_ascii=False))
            out = []
            if rep.get("ok"):
                out = _parse_proposals(
                    (_extract_json(rep.get("text", ""))
                     or {}).get("proposals") or [])
            ok, gaps = _discipline_ok(out)
            if out and not ok:
                # 一次补足重问(HarnessPro: 纪律代码级, 不靠自觉)
                log("  [harness] 提案不满足多方案纪律(%s) -> 一次补足"
                    % gaps)
                payload2 = dict(payload)
                payload2["previous_proposals"] = [
                    {k: p.get(k) for k in
                     ("hypothesis", "hypothesis_class", "verdict",
                      "boldness", "changes")}
                    for p in out]
                payload2["discipline_gap"] = gaps
                rep2 = llm.complete(
                    _PLAN_SYS + "\n上轮产出不满足多方案纪律, 请补足"
                    "缺口后重新输出完整列表。",
                    json.dumps(payload2, ensure_ascii=False))
                if rep2.get("ok"):
                    out2 = _parse_proposals(
                        (_extract_json(rep2.get("text", ""))
                         or {}).get("proposals") or [])
                    ok2, _g2 = _discipline_ok(out2)
                    if ok2 or len(out2) > len(out):
                        out = out2
                        ok = ok2
                        gaps = _g2 if not ok2 else ""
                if not ok:
                    for p in out:
                        p["insufficient"] = gaps
            if out:
                return out
            log("  [harness] 规划器LLM无有效提案->rule: %s"
                % (rep.get("text", "")[:80]
                   if rep.get("ok") else rep.get("error", "")))
        except Exception as e:
            log(f"  [harness] 规划器LLM异常->rule: {str(e)[:60]}")
    from . import evolve
    subs = evolve.mutate(diagnosis, variant, diffs=diffs)
    for s in subs:
        s["source"] = "rule"
        s.setdefault("edits", {})
        s.setdefault("hypothesis", "")
        s.setdefault("hypothesis_class", "tuning")
    return subs


# ---------------- ③ harness 编辑器(Git 工作区) ----------------

def edit_variant(variant: dict, edits: dict) -> dict:
    """按 edit 路径应用修改(点分路径, 如 'policy.stall_penalty')。
    返回新 variant。schema 外字段拒绝。"""
    import copy
    v = copy.deepcopy(variant)
    allowed = {"ops_params", "policy", "budget", "objective"}
    for path, val in edits.items():
        parts = path.split(".")
        if parts[0] not in allowed:
            continue            # schema 外拒绝
        cur = v
        for p in parts[:-1]:
            cur = cur.setdefault(p, {})
        cur[parts[-1]] = val
    return v


def commit_workspace(ws: Path, note: str) -> str:
    """隔离工作区 Git 提交(HarnessPro: diff 可比、可回滚)。
    返回 commit hash 或 ''(无 git 时降级留 note 文件, 不冒充)。"""
    try:
        subprocess.run(["git", "init"], cwd=str(ws),
                       capture_output=True, timeout=30)
        subprocess.run(["git", "add", "-A"], cwd=str(ws),
                       capture_output=True, timeout=30)
        r = subprocess.run(["git", "commit", "-m", note],
                           cwd=str(ws), capture_output=True,
                           text=True, timeout=30)
        r2 = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                            cwd=str(ws), capture_output=True,
                            text=True, timeout=30)
        return (r2.stdout or "").strip()
    except (OSError, subprocess.TimeoutExpired):
        (ws / "COMMIT_NOTE.txt").write_text(note, encoding="utf-8")
        return ""


# ---------------- ④ 固定测试生成器 ----------------

_TEST_SYS = (
    "你是 PCB 自动设计系统的评测套件生成器。输入: 任务描述+初始"
    "轨迹样本。产出**固定评测套件**(JSON): {tests: [{name, "
    "scenario, pass_rule, evaluator}]}。套件一经产出即冻结, 后续"
    "所有候选都用它裁决(禁主观评判)。pass_rule 必须可机检"
    "(如 '残差类X的objective下降>0且0回滚')。" + ANTI_CHEAT)


def generate_fixed_tests(task: str, initial_rollouts: list,
                         log=print, frozen_path=None) -> dict:
    """一次性生成固定测试套件(此后冻结)。LLM 离线 -> 内置机检套件。
    frozen_path: 落盘持久化 —— 存在则加载(跨运行/跨进程冻结,
    HarnessPro 'tests stay frozen across epochs' 纪律), 否则生成
    一次落盘。反例引理六件套以 contracts_preflight 种子注册
    (机检由 pytest tests/test_contracts.py 承担, 真键格式)。"""
    if frozen_path:
        p = Path(frozen_path)
        if p.exists():
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
                if d.get("tests"):
                    d["loaded"] = True
                    return d
            except (OSError, ValueError):
                log("  [harness] 固定套件损坏 -> 重新生成(响亮)")
    llm = _llm()
    d = None
    if llm.online:
        try:
            payload = {"task": task,
                       "sample_trajectories": initial_rollouts[:2]}
            rep = llm.complete(_TEST_SYS,
                               json.dumps(payload, ensure_ascii=False))
            if rep.get("ok"):
                d = _extract_json(rep.get("text", ""))
                if d.get("tests"):
                    d["source"] = "llm"
                    d["frozen"] = True
        except Exception as e:
            log(f"  [harness] 测试生成LLM失败->内置: {str(e)[:60]}")
            d = None
    if d is None:
        d = {
            "source": "rule", "frozen": True,
            "tests": [
                {"name": "obj_monotone",
                 "scenario": "任意残差类任意算子",
                 "pass_rule": "objective 不升且 0 新增短路",
                 "evaluator": "grind.trace+gate"},
                {"name": "gain_over_cost",
                 "scenario": "变体 vs 基线",
                 "pass_rule": "gain 差>0 或 wall 差<0(Pareto 不被支配)",
                 "evaluator": "pareto_filter"},
                {"name": "no_regression",
                 "scenario": "父代保留对照",
                 "pass_rule": "子代不得低于父代性能底线",
                 "evaluator": "pareto_floor"},
            ],
        }
    # 反例引理种子(六条契约回归, 与套件同冻结)
    d["tests"].append({
        "name": "contracts_preflight",
        "scenario": "六条反例引理(球位键/pairs列/层索引/规则表/"
                    "验你所发/端点三态)",
        "pass_rule": "tests/test_contracts.py 全绿",
        "evaluator": "pytest"})
    if frozen_path:
        try:
            Path(frozen_path).write_text(
                json.dumps(d, ensure_ascii=False, indent=1),
                encoding="utf-8")
        except OSError as e:
            log(f"  [harness] 固定套件落盘失败: {str(e)[:60]}")
    return d


def run_fixed_tests(tests: dict, result: dict) -> dict:
    """用冻结套件裁决一个评测结果(机械执行, 禁 LLM 评判)。"""
    verdicts = {}
    for t in tests.get("tests") or []:
        name = t.get("name", "?")
        if name == "obj_monotone":
            verdicts[name] = bool(result.get("rollbacks", 0) == 0)
        elif name == "gain_over_cost":
            verdicts[name] = bool(result.get("gain", 0) > 0
                                  or result.get("wall", 0) <
                                  result.get("wall_base", 10 ** 9))
        elif name == "no_regression":
            verdicts[name] = bool(
                result.get("gain", 0) >= result.get("floor", 0))
        elif name == "contracts_preflight":
            # 六条反例引理(pytest tests/test_contracts.py), 调用方
            # 启动时跑一次把 contracts_ok 放进 result
            verdicts[name] = bool(result.get("contracts_ok"))
        else:
            verdicts[name] = None    # 未识别套件项: 不裁决不冒充
    return {"verdicts": verdicts,
            "passed": all(v is True for v in verdicts.values()),
            "frozen": tests.get("frozen", True)}
