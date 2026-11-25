# 七项做法接入 fluxpcb 的十二个组成部分（2026-11-25）

参考项目（low-power-iot-hardware-design）的七项做法，逐项落到 `FLUXPCB设计.md` 规划的组件位置上。
每项都写了：**落在哪个组件、代码在哪、怎么被验证、边界是什么**。

代码提交 `f40e173`（本仓库 `autonomy-loops-20261124` 分支）。

## 0. 一句话

原来验证器层是门面：五个 verifier 永远 pass，真正的物理计算在 `gates/` 的 26 个门里，
而门的结论是没有规则 ID 的裸 dict；`verifiers/ledger.py` 里那套 `rule_id / measured /
known_good / known_bad / qualify` 只接在 `session.py` 上。七项做法接完之后：

- 每条结论都带**规则 ID、位置、数值/限值、假设、严重度、状态（确认/估计/未知）和板状态指纹**；
- 验证器由门驱动，不再有"永远 pass"的桩；不能判就是 `unknown` 并列出缺什么；
- 每条规则有一条**必须翻转的变异**；好板零 P0，坏板 P0+P1 严格更多；
- 每步动作在证书里带 **decider / alternatives / needed_information / board_fp**；
- 制造档位是**带日期和来源的数据**，并回答"改哪个参数降一档"；
- 知识是**编号文档按需加载**，LLM 调用统一前置"不确定就说不确定"；
- 可视化是**证书的产物**：`flux pages` 出一板一页的离线页面 + 索引。

## 1. 结论带规则 ID、位置、假设；估计封顶 P1 —— 第 10 项

**代码**：`fluxpcb/findings.py`（叶子模块，不依赖包内任何东西）、
`fluxpcb/knowledge/rules_index.json`、`fluxpcb/knowledge/rules.py`。

`Finding` 的字段：`rule_id, rule_version, where{x_mm,y_mm,layer,refs,uuids},
measured, limit, units, severity(P0–P3), status(confirmed|estimate|unknown),
phase(pre_route|post_route), assumption, needed_information, source{solver,
version,at}, board_fp`。

**类型本身强制四条规则**（不是靠约定）：

| 规则 | 实现 | 测试 |
|---|---|---|
| 估计封顶 P1 | `__post_init__` 里 `cap()`，P0 的估计落成 P1 | `test_an_estimate_can_never_outrank_a_measurement` |
| 未测量不写 0 | `measured is None`；`unknown` 带数值直接抛错 | `test_an_unknown_carries_no_number...` |
| 未知要说缺什么 | `status='unknown'` 必须 `needed_information` 非空 | 同上 |
| 每条结论有假设 | `assumption` 必填（无假设写 `'none'`） | `test_a_measurement_needs_units...` |

**接入**：`gates.run_gate()` 末尾的 `finalise()` 把每个门的所有 findings 过一遍
`rules.normalise()`——门如果自己给了 `rule_id` 就保留，否则套用 `rules_index.json`
里该门的规则 ID、默认严重度、默认状态和假设，并打上板文件的 **sha256 指纹**与时间。
旧键（`issue/type/net/actual/required/location/items`）同时输出，`engine.py` 的重试反馈、
`stages` 的布线前预检、`suite._extract_violations` 都不用改。

**规则 ID 表**：26 个门 + 8 类物理检验各有条目，`rules_index.json` 的 "rules" 段给出
标题、来源与文档锚点（K7/K20/K22/K24/K25/K26、LITREVIEW C/D/E、具体工具文件）。
测试 `test_every_gate_has_a_rule_id_severity_status_and_assumption` 保证没有门漏登记。

## 2. 验证器干预验证 —— 第 10 项 + 测试

**代码**：`fluxpcb/verifiers/interventions.py`；板级测试
`fluxpcb/tests/test_boards_interventions.py`。

五条变异，每条对应一条规则，**必须翻转**：

| 变异 | 规则 | 判据 | 在 0023 上 |
|---|---|---|---|
| `mutation:thin` 把一条走线改到最小线宽一半 | DRC.track_width | kicad-cli DRC | 翻转 |
| `mutation:move` 把走线垂直推到离焊盘只剩 10 µm | DRC.clearance | DRC | 翻转 |
| `mutation:short` 在两个不同网的焊盘之间画一条线 | DRC.shorting | DRC | 翻转 |
| `mutation:via_drill` 把过孔钻孔改到 0.10 mm | IPC2221.min_drill | IPC-2221 检查器 + 带日期的档案 | 翻转 |
| `mutation:edge` 把铜拉到板边保留区内 | DRC.edge | DRC | **不生成**：0023 的工程把铜边间距设为 0，这条规则在该板上无法被违反 → 如实报告原因，不算通过 |

结果形状是 `contract/pairs.py` 早就设计好、却一直没有生产者的
`{generated, crossed, killed, blind_spot}`；`RulePairRegistry.statuses()`
可以直接吃（测试里验证了种子规则表声明的每个变异都存在）。

**校准测试**（参考项目的另一条要求）：好板 0023 的 P0 为 0；坏板
（0054/0400/0520/0571 + 三个封装控件）P0+P1 严格更多，且至少一块坏板 P0 ≥ 2。

## 3. 可视化是数据的产物 —— 第 12 项

**代码**：`fluxpcb/dashboard/board_page.py`（扩展）、`fluxpcb/cli.py`（`flux pages`）、
`fluxpcb/dashboard/server.py`（`/api/pages`、`/board_page`）。

- 单页：`python -m fluxpcb.dashboard.board_page <cert.json> [<board.kicad_pcb>]`。
  证书先校验（不是证书就报告并拒绝渲染），再生成**自包含离线 HTML**（内联 CSS/SVG，无外链、无 JS）。
- 新增区块：**开路清单**（未连接项、两个端点）、**挡路焊盘**、**试过的过孔位**、
  **待补信息**（含"谁需要它"）；原有：阶段表、每条失败数值、试过的替代方案、±5 mm 局部铜皮 SVG。
- 批量：`flux pages <cells目录> --boards <runs目录> -o <输出>`，出 `index.html` + 每板一页；
  bench 的格子记录里只有 `cert` 相对路径，会被解析到真正证书。
- 服务：`flux dashboard` 起的面板新增 `/api/pages`（列出生成的页面目录）与
  `/board_page?d=&f=`（路径安全地取一页）。

实测：`full1_run/cells` 的 12 格 → 12 页 + 索引（`pages_full1/index.html`）。

## 4. 每步动作自带归因 —— 第 3/4/7/8 项

**代码**：`verifiers/certs.py`、`verifiers/txn.py`、`repair_drc.py`、
`devloop/route_loop.py`、`dispatch/dispatcher.py`、`subagents/team.py`、`contracts.py`。

| 位置 | 现在带 |
|---|---|
| 算子证书 `cert_entry` | `decider`（默认 `rule:<算子名>`；LLM 则带 model/provider/prompt_digest）、`alternatives`、`needed_information`、`board_fp` |
| 事务每轮 `TxnReport.log` | `decider`、`board_fp`（进入时取一次，整轮可比） |
| `DevRouteLoop.run` 每轮 | 分类结果、每个网的 `planner/rationale/decider`、每个任务的 `specialist/repair_operator`、验证通过与"未知"清单、板指纹 |
| `DispatchPlan` | `decider`（规则路径 `dispatch_rules`，LLM 路径带模型与 prompt 摘要） |
| `NetTask` | `specialist`、`specialist_rationale`、`repair_operator`（**声明字段**，不再是非字段属性） |

**四个缺陷一并修**（它们让归因失真）：

1. `dispatcher._try_llm` 调 `complete(prompt)` 少一个参数 → `TypeError` 被静默吞掉，
   **LLM 下发从未运行过**。现在传 system+prompt，并把模型与 prompt 摘要写进 `decider`。
2. `thermal_cfd` 只要装了 OpenFOAM/Elmer 就 PASS。现在：没跑过就 `unknown` + 待补信息；
   跑了（`sim/thermal_cfd.json`）才给结论。
3. subagent 的归因挂在 `task._specialist_action` 这种非字段属性上，`contracts.to_json` 一序列化就丢。
   现在是 dataclass 字段。
4. `RoutePipeline` 给元循环喂 `packets=None`、给元元循环喂没有 `as_dict` 的 `Trajectory` →
   元层永远 `no_data`。现在把每轮的记录整理成 packet 传进去。

## 5. 带日期和来源的制造档案 —— 第 11 项

**代码**：`fluxpcb/mfg/fab_profile.py`、`fluxpcb/mfg/data/jlc_2026-11-25.json`。

档案内容：`profile_id`、`retrieved: 2026-11-25`、`sources[{url,quote,checked}]`、
`assumption`（1 oz 外层/0.5 oz 内层/2–4 层/无阻抗控制；HDI、厚铜不覆盖）、
八项参数的三档（`limit_mm / recommended_mm / cost_trigger_mm`）、`tiers`、`decides_tier`。

两个问题各有函数回答：

```
tier_for('clearance', 0.08)        -> {'tier': 'not_manufacturable', 'limit_mm': 0.09, ...}
drop_tier_advice({'clearance': 0.1}) -> [{'action': 'raise clearance from 0.1 to 0.26 mm',
                                          'gain': 'advanced -> standard',
                                          'source': 'https://jlc.com/portal/vtechnology.html',
                                          'retrieved': '2026-11-25'}]
```

`known(param) == False` 的参数返回 `tier='unknown'`，**不默认任何值**；`check_profile()`
在档案缺日期/缺来源/推荐值低于限值时报警。工程可用 `<project>/fab_profile.json` 覆盖。

**接入**：
- `mfg/inspector._check_dfm` 从档案取 `min_track_width / min_clearance`（原来是两个硬编码 0.09），
  违规里带上 `profile` 与 `retrieved`；
- `kicadgen/fab_waiver.FAB_MIN_MM` 从档案取（读不到才退回 0.1）；
- `ipc2221_dfm`：**实现了 `_check_rule`**（走线最小宽度、钻孔最小/最大、孔到孔用 1 mm 分箱、
  板厚、铜到板边），没有测量值的规则进入 `report.unevaluated` 并说明原因，不再 `return None`
  当通过；`check_jlc` 改为**先套限值再检查**（旧版是检查完才改规则，覆盖从未生效）。

## 6. 知识组织、“不确定就说不确定” —— 第 1/2 项 + 提示

**代码**：`fluxpcb/knowledge/docs.py`、`fluxpcb/knowledge/principles.py`、`fluxpcb/llm.py`、
`fluxpcb/knowledge/index.py`、`fluxpcb/kernel/harness.py`。

- **编号文档注册表**（运行时从文档本身生成，不会与正文脱节）：KNOWHOW 的 **39 个 K 小节**
  （重复编号自动变 `K14.2`）、**5 篇文献综述 L1–L5**、**9 个 skill `S_<名字>`**、
  **4 份版本化提示词 P01–P04**、研究记录 R01–R07。每条带"什么时候读"。
  `docs.load('K25')` 按需取那一段；`docs.index_text(12)` 是给提示用的紧凑表。
- **原则统一前置**：`knowledge/principles.py` 的 `PRINCIPLES`（不确定就说不确定、没有来源的数字
  写"未测量"、估计标假设、结论引规则号、只答被问到的）在 `llm.LLMAdapter.complete()` 里
  统一加到每次调用的 system 前——一处覆盖所有调用点，新调用点不会忘。
- **规划器**：`kernel/harness.research_context()` 的返回多一个 `docs_index`，
  提示里可以写 `docs:<id>` 按需展开，而不是把全文塞进上下文。
- **检索语料**：`knowledge/index.default_corpus()` 补上了 `LITREVIEW_2026-11-25/*.md` 与
  `.agents/skills/*/SKILL.md`（以前这两层目录不在语料里）。

## 7. 八类物理检验 —— 第 10 项

| 检验 | 原状态 | 现在 |
|---|---|---|
| 电磁（场） | `em_field_analysis` 真调 openEMS，缺则 BLOCKED | 接进 `EMCVerifier`；`execute` 模式才跑，否则 `unknown` + 待补信息 |
| 热（散热） | `thermal` 用 FDM（合成几何）；`thermal_cfd` 装了就 PASS | FDM 标为 estimate；CFD 必须实跑（`sim/thermal_cfd.json`），否则 `unknown` |
| 电源完整性 | `pdn_impedance_ac`（ngspice）真算 | 接进 `PowerIntegrityVerifier`，数值/限值进 `Finding` |
| 电流与回流 | `dc_current_density`/`dc_irdrop_geom` 真算；`return_path` 解析式无几何 | 前两者 P0 确认；回流标 estimate（假设写明"无开槽几何"） |
| 温度 | `thermal` 的 `t_max_c` vs `tj_limit_c` | 拆成 `THERMAL.tj` 规则 ID |
| 平面谐振 | `plane_cavity_resonance` 解析腔模 | `CAVITY.mode_overlap`，假设写明"矩形腔、均匀 er" |
| **电磁受扰图** | **缺** | 新增 `tools/susceptibility.py` + 门 `em_susceptibility`：每网环路面积 × 1/d² 耦合、相对侵入权重，输出每网指数与最差配对（+ JSON 供页面）；**estimate、P2、封顶**；openEMS 可升级 |
| **布线前估计** | **缺** | 新增门 `pre_route_estimate`：每信号层网数、阻抗目标缺失、对档案的工艺裕量；`phase=pre_route`、**estimate、封顶 P1**、只作建议不挡门 |

**执行策略**（用户要求："完整搭建并接入，但跑基准时不执行；画 H7 时再问"）：
`verifiers/policy.py` 的 `PhysicsPolicy` 三档 `off|estimate|execute`，默认 `estimate`
（只跑代理，秒级）。`execute` 必须显式确认（`FLUXPCB_PHYSICS=execute` +
`FLUXPCB_PHYSICS_CONFIRM=1`，或 `PhysicsPolicy.execute_confirmed()`），否则自动降级并在
`reason` 里写明。**画 H7 飞控时会先问你**再开 `execute`。

## 8. 验证

| 内容 | 结果 |
|---|---|
| `fluxpcb/tests` 全量（含板级） | **277 passed** |
| 新增单测 | 词汇/规则表/档案/文档/策略 19 条；归因 7 条；板级干预 3 条 |
| 基准路径不变量 | `test_the_repair_path_imports_none_of_the_new_physics`：导入 `repair_drc/operators/conflict_reroute/txn` 后 `sys.modules` 不出现 gates / verifiers.suite / from_gates / tools.susceptibility / mfg —— **基准不因这次改动而变化** |
| `flux pages` | 用确认 #1 的 12 个格子生成 12 页 + 索引 |
| `tests/`（顶层，门禁/引擎） | 见下（端到端流水线较慢，单独跑） |

## 9. 边界（不夸大）

- **受扰图与布线前估计是代理**，标 `estimate`、封顶 P1/P2，不冒充场解；升级路径写明。
- **干预验证只覆盖有变异可做的规则**；`mutation:edge` 在 0023 上无法生成（该板不约束边间距），
  如实报"无法生成"，不算通过。其余规则在 `rules_index.json` 里没有 `evidence` 字段，
  就是没验证过。
- **`execute` 依赖外部求解器**；缺工具是 `unknown`，不是通过。
- **第五节的其余内容**（结论审计手册、八类检验接真求解器、嘉立创价格档、subagent 网络）
  没有在这次做完；这次做的是七项做法本身的接入。
- 基准路径未改：119×3 的对比表仍是 `d90e1a5` 的结果，本次改动只影响 `flux run` / `RoutePipeline`
  与页面，不影响布线修复。
