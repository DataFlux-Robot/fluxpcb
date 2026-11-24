# fluxpcb agent/LLM 共性本质实验注册 — 2026-11-22

依据：五路并行综述（元架构/类型化契约/接地元学习/弃权升级/评估覆盖）。
本文件是阶段 2（分层）与阶段 3（注册 + 校准自检）的产物。

---

## 0. 五路综述的收敛判据

1. **元层是否承载（load-bearing）**：其输出必须是执行器下一步动作的输入；
   移除它必须改变 held-out 任务上的动作分布（HarnessPro 的 `--harness-dir`
   与 hook；DGM 的 archive；AFlow 的 workflow DAG）。其余都是报表。
2. **语法保证 ≠ 语义保证**：约束解码只保证 token 属于某引擎实现的语法；
   类型化工具调用消灭"名字/形状"错误，不消灭"指称"错误（哪张规则表、
   哪种 completion、什么单位）。唯一能消灭指称错误的是**共享类型化 IR +
   名义/单位类型 + 外部检查器**。
3. **未接地的经验学习会学错**：奖励翻转/移除几乎不改变 in-context 学习曲线；
   LLM 自写 skill 的 held-out 增益为 +0.0pp，人类策展为 +16.2pp；21 个
   skill 中 16 个在 held-out 上丢失全部或部分增益。需要 (state, rules,
   referee) 三元组 + 反事实重放。
4. **合法弃权是独立机制**：升级通道把 reward hacking 从 23.6% 降到 5.3%，
   但主导失败是**漏升级**（44.3% 监测到 vs 11.8% 报告）。GAP/ASSUMED 是
   缺失的两个合法状态。不可行性应报告 MUS/MCS，而不是二值 fail。
5. **资格 ≠ 覆盖**：负控制证明"门能失败一次"，不证明"门能检出该类失败"。
   GateTruth：72% 的外部设计低于 95% 的 mutation 下限。最强方法是给门集
   加一层**覆盖测量**（artifact mutation + 边界扫描 + oracle 独立性 +
   对抗审计），而不是再加一个门。

---

## 1. Tier 1（高风险/高回报）

### T1-1 承载式 harness A/B（meta-on vs meta-off）

- **类别**：架构/元层承载性
- **假设**：若执行器被强制加载一个由元层生成、带 claims + 工具预算 +
  拒绝 hook 的 harness，并在 held-out 板族上对比 meta-off，则会出现
  **计划哈希分叉 + 结果分叉**；否则元层是装饰。因为 HarnessPro 的
  `--harness-dir` + hook 是配置而非注释（+2.0pp / −26.1% cost；移除
  planner −2–6pp），而 AFlow/ADAS 的增益都来自"计划是可执行的控制流"。
- **依据**：综述 A §1–§2、§5；本地 `devloop.diagnose([])` /
  `completion_pct=80.0` / grounding map 硬编码 `active`。
- **指标**：held-out 板族 pass rate、cost/time、计划哈希分叉率、
  rollback 次数；移除 harness 后的动作分布变化。
- **运行估计**：工程 2–3 天；2 小时计算（118 板子集 × 2 臂 × 3 seeds）。
- **风险**：harness 生成可能无效——这正是可证伪点。

### T1-2 门覆盖 mutation 矩阵 + 绑定门

- **类别**：评估覆盖/门集充分性
- **假设**：若对已路由板注入按失败类分层的最小变异（thin/move/delete/
  short/shift-pad/via-drill），并加入 `project_identity` 与
  `report_binding` 两个绑定门，则 per-class mutation score 可测，且当前
  两个已验证的 false-pass 向量（自洽错误项目、异板零错报告）被关闭。
  因为资格只证明"能失败一次"，而 GateTruth 显示 72% 的设计低于 95%
  检出下限；`rule_congruence` 比较两张表，若两张表同错则通过。
- **依据**：综述 E §2、§8、§10；本地验证：`rule_congruence` 对
  `{A:[150,150]} vs {A:[150,150]}` 判 PASS；`drc_clean` 对
  `n_error_violations=0` 判 PASS，不绑定报告与板。
- **指标**：`MS_c`（每类 mutation score）、`FPR_c`、`FAR`、
  `FPR(δ)`、`AHR`；两个绑定向量的检出率。
- **运行估计**：工程 1 天；变异矩阵 118×5×3≈1770 个 mutant × ~3–5s
  DRC ≈ 2–2.5 小时（先做 10 板 × 5 类 × 3 δ 的分层样本，~15 分钟）。
- **风险**：等价 mutant 需人工裁决；用 per-class 报告而非单一总分。

### T1-3 GAP/ASSUMED + MUS 不可行性报告接入主路径

- **类别**：弃权/升级/保证
- **假设**：若账本加入 GAP（无合格门覆盖该 claim）与 ASSUMED（依赖未验证
  前提），并在不可行时输出 MUS/MCS 而不是二值 fail，则 14 块失败板会被
  分成 FAIL / INFEASIBLE / GAP / ASSUMED，且"pass"附带条件；因为漏升级
  是主导失败（44.3% vs 11.8%），而 GAP/ASSUMED 是 nopekit 与 GSN 的
  对应状态。
- **依据**：综述 D §1–§3、§6；综述 E §7；本地 ledger 无 GAP/ASSUMED，
  `routing_llm.py` 的 escalate 只有 `{"reason": str}`。
- **指标**：重分类板数、GAP 数、INFEASIBLE+MUS 数、ASSUMED 数、
  报告忠实度（引用的规则是否真在冲突集中）。
- **运行估计**：工程 1 天；分类 118 份 cert ≈ 10 分钟。
- **风险**：MUS 提取对非线性几何昂贵；先做"规则/设计承诺/需求"三层
  分类 + 局部冲突，不做全局求解。

### T1-4 类型化契约内核（Completion/Clearance/RuleSetId/Board[Phase]）

- **类别**：语义契约/类型系统
- **假设**：若跨组件边界只允许类型化对象（`Completion{segments,nets,
  criterion}`、`Clearance{mm,rule_set:RuleSetId}`、
  `Board[PreFloor]/Board[PostFloor]`），且 JSON 由类型生成、单点校验，
  则"completion 指称两个量"、"clearance 来自两张表"、"floor 在动作前后"
  三类缺陷在构造上不可表达；因为名义/单位类型 + 共享 IR 是唯一能消灭
  指称错误（而非仅检测）的机制（SA4U：>10% 无人机固件 bug 是单位错误）。
- **依据**：综述 B §1–§8；本地 `contract/quant.py` 已有分辨率，但
  `Completion`/`Clearance` 仍是裸 float。
- **指标**：边界类型违规数（目标 0）、三个注入在构造期被拒（3/3）、
  118 板无回归。
- **运行估计**：工程 1–2 天；回归 1 小时。
- **风险**：Python 注解默认擦除——必须有运行时校验器，否则是文档。

---

## 2. Tier 2（有据、低成本）

| ID | 内容 | 依据 | 指标 |
|---|---|---|---|
| T2-1 | ledger 加 GAP/ASSUMED 两态 + worst-first 顺序 | 综述 D | 状态机完整性 |
| T2-2 | `project_identity`（manifest 哈希）与 `report_binding`（board+report 哈希 sidecar） | 综述 E §10 | 两个 false-pass 向量关闭 |
| T2-3 | 变异算子 M1–M6（thin/move/delete/short/shift-pad/via-drill） | 综述 E §2 | 每类 kill rate |
| T2-4 | 每字段测通道分辨率 + δ 扫描；修正"0.4µm 是亚分辨率"的错误夹具说法 | 综述 E §10.4 | δ=0 必 INDETERMINATE |
| T2-5 | `fill_fresh` 增加 fill 几何摘要（不只 key） | 综述 E §10.5 | 陈旧 fill 可检出 |
| T2-6 | out_of_model → 规则寄存器 + `R_om` | 综述 E §10.6 | R_om 可测 |
| T2-7 | 删除 `devloop.diagnose([])` / `completion_pct=80.0` / 硬编码 `active`；无证据即 `inactive` | 综述 A §2.5 | 元层不再"不可错" |
| T2-8 | 结构性禁止 `relaxed_clearance` 类候选（门 weakening） | 综述 A §2.5、C §6 | 元层不能改裁判 |

---

## 3. 校准自检（Tier 1）

| 检查 | 回答 |
|---|---|
| 专家会真正惊讶吗？ | T1-2 会：综述 E 明确"没有 PCB DRC 引擎的 peer-reviewed mutation 研究"；本地的两个 false-pass 向量（自洽错项目、异板零错报告）是可复现的新发现。T1-1 的方法不新，但"元层是装饰"的本地证据（diagnose([])、80.0、硬编码 active）是。T1-3/T1-4 是成熟方法的正确应用，不宣称新颖。 |
| SOTA 启发还是安全迭代？ | T1-1 直接来自 HarnessPro/GEPA/DGM 的 held-out 证据；T1-2 来自 GateTruth/BenchJack 的 mutation/red-team 协议；T1-3 来自 Gomez 的升级通道 + MUS；T1-4 来自 SA4U/MLIR/refinement types。四者都不是在给定基线上做稳妥迭代。 |
| 是否有 Tier 1 其实是超参调整？ | 无。T1-1 改决策路径，T1-2 测覆盖，T1-3 加合法状态，T1-4 加类型。没有一项调阈值/权重/线宽。 |
| 降级项 | ①"再加修复启发式"——降级，批级接受会吞掉局部成功；②"conformal 给每板保证"——降级，只有边际保证，每板真相必须来自验证器；③"调接受权重"——拒绝，权重只用于排序两个可行态，不能换硬约束。 |

---

## 4. 执行结果

### E1 绑定门（T1-2 的一部分）— 已完成

新增 `project_identity`、`report_binding`、`rule_scope` 三个门（账本现有
10 个门，10/10 通过负控制资格）。`inject_corruption.py` 扩展到 **19/19**，
并显式证明两个 false-pass 向量：

| 检查 | 结果 |
|---|---|
| G1 自洽的错误项目 | 旧 `rule_congruence` 判 **PASS**（两张表同错） |
| G2 `project_identity` | 判 **FAIL**（项目哈希与 manifest 不符） |
| H1 异板零错报告 | 旧 `drc_clean` 判 **PASS**（只看计数，不绑定） |
| H2 `report_binding` | 判 **FAIL**（sidecar 的 board_sha 与当前板不符） |
| I1 规则寄存器 | 28/40 → **FAIL**，暴露模型缺口 |

### E2 mutation 矩阵（T1-2）— 已完成（8 板 × 14 变异 = 112 个）

分层样本：4×D3-A + 2×D3-B + 2×D3-C；单故障、规则邻近、三个 δ。

| 算子 | 生成 | 越过声明规则 | 越界后检出 |
|---|---|---|---|
| thin（减宽） | 24 | 17 | **17/17 = 100%** |
| move（移向邻线） | 24 | 8 | **8/8 = 100%** |
| delete（删段） | 8 | —（连通性） | **8/8 = 100%** |
| short（跨网桥接） | 8 | —（短路） | **8/8 = 100%** |
| via_drill（加大钻孔） | 12 | **0** | 0（annulus 规则在这些板上为 0） |
| shift_pad（移焊盘） | 24 | 未分类 | 5/24（19 个 survivor 未判定） |

**结论（带边界）**：对**已经声明的**数值规则，门集是 100% 敏感的——
这比 GateTruth 的 72% 低于 95% 下限要好。但 `via_drill` 的 0/12 说明
问题不在检出能力，而在**规则寄存器只有 5/40 类**（R_om=0.875）：
annulus 规则没有被声明，所以"加大钻孔"是等价变异，不是盲点。
`shift_pad` 缺一个"是否越过声明规则"的度量，19 个 survivor 不能算检出，
也不能算等价——如实记为未分类。

### E3 GAP/ASSUMED 分诊（T1-3）— 已完成

对 118 份 cert 重新分类：

| 状态 | 数量 | 说明 |
|---|---|---|
| ASSUMED | **104** | 全部依赖 `default_rule_policy`（我们注入的 pcb 级默认规则） |
| FAIL | 10 | 连通性/间距/短路残差 |
| INFEASIBLE | 4 | 仅有宽度残差且盲加宽被事务门拒绝（0048/0054/0400/0599） |
| 无条件 PASS | **0** | 没有一块板的 pass 不依赖假设 |

`R_om = 1 − 5/40 = 0.875`。这就是"诚实边界"：104 个 pass 是有条件的，
不是无条件的"可加工"。

### E4 类型化契约内核（T1-4）— 内核完成，回归未跑

`fluxpcb/contract/types.py`：13/13 检查通过。

| 检查 | 结果 |
|---|---|
| 外来 `RuleSetId` 的 `Clearance` | 构造期 ValueError |
| 无 criterion 的 `Completion` | 构造期 ValueError |
| 无 state_rho 的 `Measurement` | 构造期 ValueError |
| `BoardState(PRE_FLOOR)` 下的 `widen` | 拒绝（typestate） |
| 裸 dict 过边界 | TypeError |
| 不同 rho 的两个 Measurement | 不可比 |

内核可用；**尚未**把 12 组件主路径全部改为只交换这些类型（这是下一步）。

### E5 元层去装饰（T2-7/T2-8）— 已完成

- `MetaDevLoop.select` 不再伪造 `completion_pct=80.0` / `drc_errors=5`；
  无 evaluator 直接 `RuntimeError`。`run()` 无真实轨迹时返回
  `status='no_data'`，无 evaluator 时 `status='no_evaluator'`。
- 删除 `relaxed_clearance` 与 `clearance_mm=0.1` 候选——元层不得降低门；
  改为 `reroute_violating_nets`。
- `GroundingMap` 的状态改为证据驱动：meta-meta 仅在
  `harness_diffs>0` 且 `heldout_delta>0` 时 active；design agent 需要
  带 claims 的 plan；comm runtime 需要真实 stale_count；其余无证据即
  inactive。

### T1-1 承载式 harness A/B — 未执行（登记保留）

需要先实现 harness 加载器（`policy.json` + hook 拒绝）与按板族切分的
held-out 集；这是本轮唯一未执行的 Tier 1。按 HarnessPro 的判据，当前
元层在修复前是 level 0–1（报表/建议），修复后至少不再伪造——但**尚未**
证明它能改变 held-out 动作分布。这是下一步的主实验。



---

## 5. P0/P1 执行结果（2026-11-22）

### P0 失败关闭（已验收）

| 项 | 结果 |
|---|---|
| `run_drc` 失败关闭 | 缺二进制/超时/缺报告/解析失败 → `ok=False`；先删除陈旧报告 |
| `KICAD_CLI` | `run_drc` 全链路生效 |
| `accept()` | 新增"不得引入新违规类型" + `total` 不得增加；reason 记录 new_types |
| `transactional_repair` | action/eval 异常均恢复字节快照 |
| `save_preserving_project` | 同时保护 `.kicad_pro` 与 `.kicad_dru` |
| DRC 解析 | 结构化字段优先；英文/中文描述回退；缺失测量标 `has_measurement=False`，算子跳过 |
| `bench_runner._evaluate` | 用 `save_preserving_project` + 复制源项目 + `bind_report` |
| 门 weakening | `feedback_runtime` / `Dispatcher` 的 `relaxed_clearance` 全部移除 |
| 回归 | `pytest fluxpcb/tests` **16/16**；`inject_corruption.py` **25/25** |

### P1 Scope + 规则寄存器 + live ledger（已验收）

- `contract/register.py`：43 条声明规则（KiCad design settings + DRC 类型 +
  JLC 极限），稳定 `rule_id`；`R_om` 由 ID 集合计算，不再硬编码 5/40。
- `contract/scope.py`：每板 `scope_id` = hash(project_sha, dsn_sha,
  register_digest, executor, tools, claim_set, assumptions, family, phase)。
- `verifiers/live.py`：主路径写 `runs/<dir>/ledger.jsonl`；118/118 份，哈希链完整。
- 双轴判决：
  - `board_worst`（物理判决，claim = rule_congruence + project_identity +
    report_binding + drc_clean + unconnected_zero）：**104 pass / 14 fail**；
  - `worst`（就绪度，另含 `rule_scope` 与 assumptions）：**118/118 fail**。
- 全量 118 复跑：D3-A 91/99、D3-B 9/10、D3-C 4/9 = **104/118**，
  与 P1 前逐板 0 gained / 0 lost；52 次事务提交。
- `R_om = 0.93`（该语料每板 3/43 条规则被 RuleModel 表达；下一轮修正
  `jlc.description` 非规则键后为 42 条）。

**结论**：P0 关闭了 6 类 false-pass 向量；P1 让"pass"第一次携带
scope/assumption/rule-register，并诚实显示 **0 个无条件 pass**。
物理成绩未变（104/118），因为本阶段不改算法，只改测量与判决。


---

## 6. P2 执行结果（2026-11-22）

### Rule-Spec 对（10 声明 / 6 validated）

| 规则 | 声明式 | 可执行检查 | 状态 |
|---|---|---|---|
| clearance | gap ≥ limit | DRC `clearance` | **validated**（move 8/8） |
| track_width | width ≥ limit | DRC `track_width` | **validated**（thin 17/17） |
| shorting_items | 不同网络不连通 | DRC `shorting_items` | **validated**（short 8/8） |
| copper_edge_clearance | 铜到板边 ≥ limit | DRC `copper_edge_clearance` | **validated**（JLC oracle 21 条） |
| annular_width | 孔环 ≥ limit | DRC `annular_width` | **validated**（JLC oracle 20 条） |
| hole_clearance | 孔到铜 ≥ limit | DRC `hole_clearance` | **validated**（JLC oracle 1 条） |
| hole_to_hole | 孔到孔 ≥ limit | DRC `hole_to_hole` | mapped（未检出） |
| via_drill | 钻孔 ≥ min | DRC `drill_out_of_range` | mapped（语料未声明 annulus/drill 规则） |
| track_dangling / via_dangling | 无悬空端 | DRC 同名 | mapped（delete 证明的是开路，不是悬空） |

### Mutation 覆盖

| 算子 | 生成 | 越过声明规则 | 越界检出 | MS | 盲点 |
|---|---|---|---|---|---|
| thin | 24 | 17 | 17 | **1.00** | 0 |
| move | 24 | 8 | 8 | **1.00** | 0 |
| delete | 8 | 8（连通性） | 8 | 1.00 | 0 |
| short | 8 | 8（短路） | 8 | 1.00 | 0 |
| via_drill | 12 | 0 | 3 | — | 0（规则未声明） |
| shift_pad | 24 | 未分类 | 5 | — | 19 个 survivor 未裁决 |

### 第二 oracle：JLC 剖面（全量 118 板）

- 方法：同一块板在"JLC 剖面项目"下重判（minima = fab limit；
  annular/hole/edge 严重度设为 error；附 JLC `.kicad_dru`）。
- **AHR = 0.2981**：104 块项目规则 clean 的板中，**31 块**在 JLC 判据下失败。
- 检出类型：`copper_edge_clearance` 73 条、`annular_width` 31 条、
  `hole_clearance` 6 条、`clearance` 4 条。
- 探针：wrong-project **118/118** 被 `project_identity` 拒；stale-report
  **118/118** 被 `report_binding` 拒。
- `shift_pad` 已裁决：24 个变异中 crossed 0、killed 3、等价 21、盲点 0
  （孔距 ~1.55mm ≫ 0.2mm 阈值）。

### P2 续（定向探针 + 全量 redteam）

- 定向探针：`hole_to_hole` 8/8、`copper_edge_clearance` 8/8（用只收紧该规则的
  探针项目，移动单个对象刚好越界）。
- `shift_pad` 裁决：24 个变异中 crossed 0、killed 3、等价 21、盲点 0。
- 全量 118 板 redteam：AHR **0.2981**（31/104），类型
  copper_edge 73 / annular 31 / hole_clearance 6 / clearance 4；
  wrong-project 118/118、stale-report 118/118。

### 指标与目标

| 指标 | 值 | 目标 | 结论 |
|---|---|---|---|
| `R_om_dsn`（DSN 可表达） | 0.93 | — | 不变 |
| `R_om_check`（已验证 pair / 声明） | **0.837**（7/43） | ≤0.5 | **未达标**：还需约 15 个 validated pair |
| pair 层（10 声明） | **7 validated / 3 mapped / 0 blind** | 10 | 部分达标 |
| mutation MS（已声明数值类） | 1.00 | ≥0.95 | 达标 |
| AHR（JLC 第二裁判，118 板） | **0.2981** | ≤0.05 | **未达标**；缺口已定位 |

**结论**：P2 的最大产出不是把 `R_om` 压到 0.5，而是**证明 29% 的
"clean" 板在 fab 规则下并不 clean**——这些缺口（copper-edge、annular）
在项目 DRC 中根本不存在。`R_om_check` 仍高，因为 43 条声明规则里只有
6 条有 validated pair；要达到 0.5 需要再补 16 条（P2 续）。


---

## 7. P3 执行结果（2026-11-22）

### 交付

- `fluxpcb/harness/hook.py` + `policy.json` + `policies/strict_widen_only.json`：
  `HarnessPolicy`（max_actions / allowed_ops / require_claims /
  allow_gate_weakening）、`HarnessHook`（预算、算子白名单、SII、门变更授权）。
  缺失/非法 policy **fail-closed**。
- `fluxpcb/session.py`：`DesignSession`（measure → repair → record → verdict），
  verdict 输出 `board_worst` 与 `readiness` 两轴。
- `fluxpcb/verifiers/txn.py`：事务接入 hook；`after_action` 发现白名单外算子
  即回滚，`TxnReport.denied` 记录。
- `fluxpcb/meta/meta_loop.py`：`validate` 不再恒真——要求 `changes` 非空、
  `heldout_delta > 0`、`scope_narrowed == False`。
- `work/pcbworld/meta_ab.py`：同一批板跑 meta-off vs meta-on（严格 policy），
  记录动作分叉、denied、耗时、pass 与 scope 是否收窄。

### A/B 结果（6 块需要修复的板）

| 指标 | off | on |
|---|---|---|
| pass | 5/6 | 5/6 |
| 平均耗时 | 5.6s | 5.8s |
| commits | 5 | 5 |
| denied | — | 1 |
| 动作集分叉 | — | **1/6** |
| scope 收窄 | — | **0/6** |

**判决**：harness **在路径上**（0054 的 `plane_bridge` 被拒并回滚，动作集
分叉），但**没有结果增益**（5/6 vs 5/6）。按计划的 kill criterion，
C1/C2 的"改进"仍未被证明——它们是装饰性的，grounding map 应保持 inactive。
严格策略只证明了"门可以阻止动作"，没有证明"元层能让系统更好"。

### 诚实缺口

- `pipeline.py` 仍未改为 `DesignSession` + KiCad DRC 判决；本轮只交付了
  session 本身与 bench 路径的 hook。12 组件管线依旧是影子路径。
- A/B 样本 6 块、单次运行；没有 CI。计划要求 ≥3 seeds，未做。
- `meta_loop.improve` 仍按 avg_grade 阈值产出 changes，但 `validate` 会拒绝
  没有 held-out 证据的 diff；这保证"未验证的改动不生效"，但不产生改进。


---

## 8. P4 执行结果（2026-11-22）

### 8.1 Transactional reroute（已实现，负结果）

- `reroute_residues`：按 DRC 违规网络拆线重布；在**独立子进程**中执行
  （pcbnew SWIG 状态在一次 rip-up 后失效），结果拷回板子后由事务门判定。
- `run_bench` 在首批修复后仍有 width/clearance 残差时，追加一次
  reroute 事务；变差即回滚。
- 实测 4 块宽度残差板（0054/0400/0048/0599）：重布前后总数完全不变
  （9→9、9→9、3→3、65→65），**4/4 回滚**。
- 结论：这些残差不是"路径选错"，而是 Freerouting 的系统性 75% 收窄；
  用同一个布线器重布无法消除。需要不同的执行器（PNS）或不同的宽度策略。

### 8.2 T0 PNS spike（环境阻塞）

- `pcbnew` Python 不暴露 PNS（`dir(pcbnew)` 只有 `ROUTER_TRANSIENT`）。
- `kipy` / `kicad-python` 未安装；KiCad IPC API 需要**运行中的 KiCad 实例**，
  headless CLI 下不可达。
- 结论：本轮无法建立 T0 同余；记录为环境阻塞，不阻塞其他阶段。

### 8.3 H7 端到端（已跑完）

用加固后的 `bench_runner`（fail-closed DRC + 项目绑定 + 事务修复 +
盲加宽 + reroute 事务）跑 `h743-fc`（6L，TFBGA240）：

| 指标 | 结果 |
|---|---|
| 未连接 | **0**（历史最好；原 71 opens → 14 → 0） |
| clearance 错误 | **9** |
| 规则同余 | true |
| 耗时 | 951.6s |
| 状态 | fail（未达 0/0） |

9 条分类（`residue.classify_drc`）：`router_fault:clearance` 9，out-of-model 0。
- 2 条违反我们自己写的 `flux_policy_*` 豁免（0.1297 < 0.1321；0.1017 < 0.1464）：
  waiver 被后续几何改动破坏，属于组合问题。
- 7 条为 netclass `Default` 0.15 规则下实际 ~0.1017–0.1389：路由器按 ~0.10
  布线，但 DRC 判 0.15。这是 L1/L2 边界问题（DSN 类表同余，但 Freerouting
  对某些对象使用了更松的 pcb 级/焊盘类间距），不是简单的"路径选错"。

**结论**：P4 把 H7 从 14 opens 推到 **0 opens**，但 9 条 clearance 说明
"全连通"与"可加工"仍是两件事；下一步应查这 7 条的 DSN 对象归属
（pcb-level vs class-level），而不是放宽规则。
