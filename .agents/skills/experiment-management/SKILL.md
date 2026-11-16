---
name: experiment-management
description: 实验管理 skill —— A/B 双臂验证、固定套件裁决、审计三账、Git 可回滚。当要验证一个假设/算子/修改是否真的有效时使用。禁主观评判，一切走固定评测器。
argument-hint: "[候选JSON或A/B工作目录]"
allowed-tools: "Read, Write, Bash(python*), Bash(git*)"
---

# experiment-management: A/B 验证与三账

## Why This Exists

Agent4Sci 三阶段协议（爬坡/评估/审计）的实证教训：无审计的自评=作弊
温床。本项目实证：裸跑 zone_bridge 净闭 4 开但 +2 短 +97 间距——
无门禁的"增益"是假增益。

## 核心纪律

1. **A/B 双臂同基线**：两臂从同一 BASE 的新鲜副本出发（拷后必删
   v_result.json），唯一差异=被验证的机制。
2. **固定套件裁决**（z_lab/fixed_tests.json 冻结跨运行）：
   obj_monotone / gain_over_cost / no_regression / contracts_preflight
   （六条反例引理红灯=整体不过）。禁 LLM 主观评判。
3. **预注册判据**：实验前写下 PASS 条件（prereg_criteria），跑完按
   预注册判——不许跑完再找赢的理由。
4. **审计前置采纳**：PASS ≠ 采纳。audit_trajectory 四类作弊检查
   （选择性报告/验证削弱/指标操控/硬编码）clean 才进注册表。
5. **三账**：实验账（做了什么）、收益账（实测 delta）、反例账
   （什么失败了、为什么）。
6. **Git 可回滚**：采纳必 commit_workspace（candidate/verdict/audit
   三件套入工作区提交）。

## Workflow

1. 立候选 JSON（带 hypothesis/hypothesis_class/prereg_criteria）。
2. `python z_ab.py BASE_DIR CANDIDATE_JSON N` 双臂对照。
3. 读 verdict.json: PASS + fixed 套件 verdicts + edits_applied。
4. 审计（harness.audit_trajectory）→ clean 才采纳。
5. 采纳落 ops_registry（status=promoted）+ 工作区 Git 提交。

## 反例（别这么干）

- 用 stale 快照当基线/目标（VDDA 假闭合案）—— pairs 必须实时 query。
- 跑完看结果再改判据 —— 预注册的意义就没了。
- PASS 后跳过审计直接落库 —— 审计员抓的就是这条路。
- 只报赢的轮次 —— 选择性报告是四类作弊之首。
