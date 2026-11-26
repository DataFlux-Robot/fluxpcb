# 证明携带的 PCB 设计：K1–K4（2026-11-26）

按 8 点要求落地的第一段：**需求 → 猜想**、**subagent → 定理**、**布线器 → 引理**、
**Lean 逻辑内核 + 并列的领域内核**、**无旁路 Step 门**。K1 只做"能跑、能拒、能复算"
的最小闭环；模块化并行与 0344 性能对比在 K2/K3。

## 一、演算

```
Conjecture(需求)  0344: 20 网全连通 ∧ 网表一致 ∧ 工艺合规
  Theorem(subagent)  一个模块/子任务的契约（K2 落地）
  Lemma(router)      route(pair)=some r → Connected r
  Lemma(fab)         clearance/width → 可加工（领域内核）
  Lemma(frame)       只触碰 T 的步骤保持 scope∩T=∅ 的结论
```

K1 实现的是其中的**逻辑骨架**：`Claim`（connected / netlist）、`Cert`（生成树）、
`ProofObject` 形态的报告、`frame/carried`、`Step` 门。`pred` 不属于 Lean 的
（clearance/width）在内核里标 `foreign`，由 fab 领域内核裁决。

## 二、内核

- **Lean 逻辑内核** `lean/`（Lake 项目，Lean 4.33，core+Std，**不引 Mathlib**）：
  `Board`（Item/Shape/State + 规范字符串 + FNV-1a 64 摘要）、`Geom`（整数 nm 的
  seg/circle/poly 精确相交）、`Claim`、`Check`（边检查 + 生成树可达）、`Frame`
  （未触碰项不变、carried scope 不相交）、`Kernel`（请求解析 → 报告 JSON）、`Main`。
  `lake build` 18 个 job 全绿、**零 sorry**。
- **fab 领域内核** `fluxpcb/kernels/fab.py`：clearance/width，整数交叉相乘判定，
  float 只用于展示见证。它与 Lean 是**并列**的：Lean 的 `ok` 忽略 `foreign`，
  Step 门要求两个内核都通过。
- **Step 门** `fluxpcb/proof/gate.py`：`commit_step(before, after, touched, claims,
  certs, carried)`。它自己计算该状态的**必备义务集**（每个多焊盘网的 CONN、
  NETLIST、FAB:CLEARANCE、FAB:WIDTH），调用方少交一个就拒绝——不存在"空证明通过"。
- **无旁路**：写板动作必须经 `commit_step`；旧流水线保留旧路径，但 proof 模式没有
  另一条写板通道。

## 三、双哈希（翻译漂移检查）

`state.canonical()` 用固定顺序拼出字符串（schema/board/layers/items 按 id 排序/
rules/netPads），Python 与 Lean 各自 FNV-1a 64，不一致即 `drift: true` 拒绝。
合成板上实测一致（13360978817481524574）。抽取器仍是信任桥（KiCad→规范 JSON），
但**任何被解析器丢掉的字段都会改变摘要**。

## 四、K1 验收

### 合成板（单测 `test_proof_gate.py`，8 条）
- 干净板：CONN/NETLIST proven，fab proven；
- 开路：`unknown`（pads not reached），不是 `refuted`——**搜索失败不等于证伪**；
- clearance 入侵者：`refuted`，见证是两条 item 与负间距；
- 摘要漂移：`drift` 检出；
- frame：未声明 touched 的改动被拒；
- carried：scope 与 touched 相交被拒；
- 绕过：不交义务直接 commit 被拒。

### 真实板 0344（D3-B，34×23.75 mm，2 层，431 个铜项、20 网）
| 场景 | 结果 | 用时 |
|---|---|---|
| 干净 | **23 proven**（20 CONN + NETLIST + 2 FAB），0 refuted | 7.3 s |
| 删一条 3V3 走线 | CONN:+3V3 `unknown`（2 个焊盘不可达） | 5.3 s |
| 加一条异网走线（0.1 mm） | FAB:CLEARANCE `refuted`，gap −275 µm | 5.1 s |
| 良性步骤（短走线平移 1 µm，声明 touched） | **接受**，19 条 carried 证明复用 | 5.3 s |
| 有害步骤（走线移到异网同 y） | **拒绝**：FAB:CLEARANCE refuted，gap −250 µm | — |
| frame 违反（改了不声明） | 拒绝 | — |
| carried scope 与 touched 相交 | 拒绝 | — |

## 五、K1 抓到的两个模型错误

1. **把椭圆焊盘当矩形**：0344 的 P2 焊盘是 `PAD_SHAPE_OVAL` 2.032×1.7272；我按
   包围盒画矩形，一条 VPP 走线从矩形角 0.037 mm 处经过 → fab 报 −150 µm 短路。
   KiCad 自己的 `Collide` 说没碰（椭圆的角在 0.86 mm 内）。改为用
   `pad.GetEffectivePolygon(layer)`（KiCad 自己的多边形化）后，同一块板 0 误报。
   这正是"从实际铜皮复核"的价值：**模型的角不是铜的角**。
2. **搜索失败与证明无效混为一谈**：生成树没覆盖全部焊盘时，原实现报 `invalid`
   （像证书错了），但这其实是"没找到见证"。改为 `unknown` + 缺失焊盘列表；
   `invalid` 只留给"边不成立"的坏证书。

## 六、边界（K1）

- **zone 未抽取**：118 块基准板 0 个 zone；H7 的铺铜在后续里程碑。有 zone 的板
  fab 义务标 `unknown`，不静默通过。
- **任意旋转焊盘**：用 KiCad 的多边形，误差由 KiCad 的 `maxError` 决定；不是我们
  自己的几何证明。
- **fab 只有 clearance/width**：annular/drill/edge/assembly/拼板在 K2。
- **物理量**（电流/热/信号）尚未接入：按设计它们将是 `derived`（模型+负控），
  **永不** `proven`。
- **未测**：118 全量（K4）、0344 的 S0–S3 性能对比（K3）、模块化并行（K2）。
- 0344 干净验证 7.3 s（目标 <5 s）：焊盘多边形点数（36）让 Python 证书构建变慢，
  K4 前要加网格索引。

## 七、下一步

- **K2**：`ModuleContract/ModuleProof/Merge`，两个 subagent 并行设计 0344 的
  电源/处理器模块；模块库骨架。
- **K3**：0344 的 S0–S3 性能对比（整板重布 vs 局部重证 vs 并行 vs 拼接）。
- **K4**：118×3 的 lean+fab 对账。

## 八、K2：模块契约、并行证明与 Merge

### 切分（由契约从位号自动推导）
| 模块 | 位号 | 内部网 | 边界网 |
|---|---|---|---|
| **power** | U2, F1, D6, C2, C3, C4, R5, R6 | 0 | VPP, +5V, +3V3, GND, ADC 采样 |
| **mcu** | U1, P1, P2, P3, D1–D4, R1–R4, R7–R11, SW1, W1–W9 | 15（UART/GPIO/CH_PD/LED/按键） | 同 5 条 |

契约只写位号，`derive()` 从焊盘的 `ref` 推内部/边界：**内部网 = 所有焊盘都在模块内**；边界网 = 两边都有。推导结果与手写预期一致（不是手填的）。

### 并行证明（两个 agent 同时跑）
| 模块 | 证明内容 | 结果 | 用时 |
|---|---|---|---|
| power | 5 条 PADS + 2 条 FAB | 7 proven | 0.29 s |
| mcu | 15 条 CONN + 5 条 PADS + 2 条 FAB | 22 proven | 1.52 s |

两个 agent 各自只读板子、写自己的 `mod_*.json`；Lean 内核 + fab 内核各自裁决。

### Merge（组合义务）
- 边界：两侧 PADS 必须**不重不漏**地等于整板该网的焊盘；
- 共享网（VPP/+5V/+3V3/GND/ADC）在整板上证一次（CONN）；
- 整板净表 + 全局 fab（clearance/width）。
- 结果：**33 proven**（20 CONN + 10 PADS + NETLIST + 2 FAB），3.85 s；边界计数 VPP 4、+3V3 10、+5V 7、GND 17、ADC 3。
- 负控：从 mcu 证明里删掉一个 +3V3 焊盘 → Merge 拒绝：`+3V3: boundary pads != board pads (missing 1)`。

### 模块库骨架
`modules/power_ap7361_3v3/module.json`、`modules/esp12e_core/module.json`：功能、边界/内部角色、
claim 类型、已证实例（0344 的契约与用时）。新板"选模块 → 实例化契约 → 只证差异"。

### 诚实边界
K2 证明的是**已有布线结果**的模块化分解与并行**证明**，不是并行**布线**。真正的并行设计
（从 placed 板各自布模块再 Merge）是 K3/S2；K2 先把上下接口与 Merge 义务固定下来。

## 九、K3：0344 局部改动的性能对比（2026-11-26）

**改动**：把电源模块的稳压器 U2 平移 1.5 mm，删掉 5 条电源网（VPP/+5V/+3V3/GND/ADC）的
181 段铜，保留 15 条信号网的 147 段铜。改动后的板在布线前：5 条电源网开路、15 条信号网
已证、fab 干净（移动本身不违规）。

| 场景 | 现有方法（auto_route 整板） | 证明携带方法 |
|---|---|---|
| S1 局部改动 | **>16.5 min 后杀掉**：板子被撕成 187 段、**20 开路 / 27 短路**、78 轮 action 仍无收敛 | **55.5 s**（增量 FR 28.8 + 26.7 s）：328 段、**0 开路**、1 处 0.174 mm 间距 |
| 验证 | 未到可验证状态 | 全板 22 proven / 1 unknown；fab 拒绝那 1 处间距：`gap 174 µm < 200 µm`，**Step 门拒绝提交** |
| 证明复用 | — | 15 条信号网证明 carried（scope 与 touched 216 项不相交） |
| S2 并行证明 | 串行整板验证 7.3 s | power 0.29 s + mcu 1.52 s（并行墙钟 1.52 s）+ Merge 3.85 s ≈ **5.4 s**（1.35×） |
| S3 拼接 | 不支持 | S1 就是一次拼接：边界 5 网不重不漏通过；拒绝原因只来自 fab 间距，不是组合义务 |

### 结论（诚实）

1. **现有 `auto_route` 在"已有布线 + 局部改动"的板上是破坏性的**：它做整板 session，
   把好的信号铜也撕掉，16.5 分钟给出 20 开路 / 27 短路。证明层没有让它变快，因为它
   本来就不做"局部"。
2. **证明携带方法把局部改动变成 55 秒的可验证步骤**：只对声明的 5 条网增量布线，其余
   15 条网的证明直接复用（carried），然后用 fab 内核复核。它没有放过那 1 处 0.174 mm
   间距——**Step 门拒绝提交**，并给出两条 item 的见证。这是"无旁路"的意义：宁愿拒绝，
   不落一块不可加工的板。
3. **并行证明的收益在这个尺度上只有 1.35×**（Merge 要在整板上重证共享网）；模块越大、
   越多，收益越大。真正的并行**布线**还需要作用域路由器（下一步）。
4. 修掉一个真 bug：`routing.py` 里 `_clear_fb` 是 `_closed_loop` 的局部变量、
   `auto_route` 直接引用 → 每次布线到最后 `NameError`（H7 的 54 分钟没跑到这里）。

### 下一步

- 作用域路由器：只对被触碰的网做 A*/FR，其余铜 fix-mark（`route_with_freerouting`
  已经支持 incremental，K3 用它拿到 55 s；把它接进 Step 门作为 router lemma）。
- 那 1 处 0.174 mm：用 direct_link/加宽闭合，再走一次 Step 门（门已经给出精确的两项）。
- K4：118×3 的 lean+fab 对账。

## 十、K4：框架收口、两个真 bug、118 抽 2 个中档任务的实测（2026-11-26）

### 10.1 框架（代码）

| 模块 | 作用 |
|---|---|
| `kernels/derive.py` | 领域推导：`power_drop`（R=ρL/(wt)）、`thermal_tj`（Tj=Ta+Pθ）、`z0_microstrip`（IPC-2141）。结果只能是 `derived`/`unknown`，evidence 带模型 id、版本、输入、输入哈希；缺输入 → `unknown` + `needed_information`。 |
| `kernels/profile.py` | profile：`lean+fab`（118/日常）与 `full`（+power/thermal/signal）。关掉的核 → `disabled`（带原因），不静默丢弃；超限 → `refuted`。 |
| `proof/conjectures.py` | 需求 → 猜想：rail 的 `length_mm/width_mm` 从**当前状态的实际铜**读（总长、最窄宽），所以同一条猜想在未布线时是 `unknown`、布线后变 `derived`；无法形式化的需求进 `unformalized` 列表。 |
| `proof/router.py` | router lemma：在工作副本里只撕开声明网的铜，增量 FR 把其余铜 fix-mark（默认最多 2 轮，无开路即停），然后**一个 Step**：`touched` = 删除项 ∪ 新增项，`carried` = scope 与 touched 不相交且此前已证的 claim，最后过 `commit_step`。被撕开的工作副本不是设计状态，永不提交。 |
| `proof/framework.py` + `fluxpcb.proof.cli certify` | 每板一张 `fluxpcb-cert-1` 证书：digest、profile、规则及来源、每条 claim 的 status/witness、route Step、耗时。 |
| `gate.py` | `verify(profile=, domain_claims=)`：disabled 跳过但记录；`unknown`/`refuted`/超限都使 Step 被拒。 |

### 10.2 抽中的两个中档任务（D3-B）

抽签：`random.Random(20261126).sample(sorted(d3b − 0344), 2)`，池 9 个，抽到
**0316_kicad-workshop_fancyboard** 与 **0376_cat-trainer_teensy_base_pcb**。同一 arm
（`gate+ripup~full4r0`）、同一 bench 协议（900 s/格，DRC error + 0 unconnected）：

| 板 | 布通 | wall | route | DRC error | unconnected | 证书 |
|---|---|---|---|---|---|---|
| 0316 | 是 | 46.6 s | 19.3 s | 0 | 0 | 25 proven（0.2032 mm 网类间距 / 0.2 mm 线宽）2.7 s |
| 0376 | 是 | 35.6 s | 10.5 s | 0（repair 阶段修掉 14 个 track_width） | 0 | 29 proven（0.1524 mm 网类）3.8 s |

两证书 `ok=true`，`foreign: 2` 是 fab 谓词在 Lean 侧的如实标注（不声称 proven）。

### 10.3 两个真 bug（由这 2 个板子暴露）

1. **接触判据的半宽约定错误（可靠性 bug，已修）**。Lean `Geom.lean` 与 Python
   `proof/geom.py` 的 `segSeg/segPoly/circlePoly/circleSeg` 把“全宽之和”当半径：
   两条 0.2 mm 宽、中心线相距 0.25 mm 的同网走线（铜间隙 0.05 mm）会被判为相接，
   于是 `CONN` 可能被伪证。修复：新增 `ptSegTouch`（`4·num ≤ s²·den`，s = 全宽之和），
   四个判据统一走它；`circleCircle` 本就正确。回归：
   `test_two_tracks_with_a_copper_gap_are_not_connected`、
   `test_kernel_rejects_a_certificate_edge_across_a_copper_gap`。
   **0344 的 clean 23 证明、模块 7/22、Merge 33 在修复后全部复现**（见 10.4）。
2. **fab 规则硬编码**。之前一律 0.2/0.15 mm；0376 的网类是 0.1524 mm，硬编码 0.2
   会把 KiCad 认可的 0.1526 mm 间距伪证为违规。现在 `state.board_rules` 从
   `.kicad_pro` 的 `net_settings.classes` 取 `max(min_clearance, min(class))`
   （单条全局规则只能取“任意一对的最小要求”，更严会伪证），证书记录 provenance。

### 10.4 0344 在修复后的内核上复跑

| 项 | 结果 |
|---|---|
| clean 全板 | 23 proven，3.4 s，digest 13554797222594226126 |
| 模块 | power 7 proven（0.15 s）、mcu 22 proven（1.46 s） |
| Merge | 33 proven，5.06 s；边界 5 网 +3V3 10 / +5V 7 / GND 17 / ADC 3 / VPP 4 |
| 作用域重布 4 条电源网 | 23 proven，89 s；touched 315（撕 154、增 161），**16 条信号网证明 carried** |
| K3 的 S1 五网（U2 平移） | 两轮 FR 后 **23 proven，51.1 s**；touched 173，carried 16，**0 开路、无间距违规** |

`full` profile 演示（同一 0344，负载是**声明假设** 3V3 0.3 A / 5 V 0.2 A，不是从设计读出的）：
`POWER:DROP:3V3` 46.0 mV @0.3 A、`POWER:DROP:5V` 12.5 mV @0.2 A，均 `derived`；
两条无法形式化的需求（叠层、外壳）进 `unformalized`，证书 `ok`。领域结论只到 `derived`，
evidence 带模型、版本、输入与哈希。

K3 里那处 0.174 mm 间距在新内核、新 FR 采样下没有复现；同一场景现在给出的是
“两轮 FR → 51 s 全证”。这不是把旧数字抹掉，而是同一实验在新内核上的独立结果；
旧结果（以及它被 Step 门拒绝这件事）留在第九节。

### 10.5 边界

- `full` profile 的领域结论仍是 `derived`：模型 + 声明输入 + 哈希，不是定理；
  抽中的两板没有声明电流/热参数，所以用 `lean+fab` 出证书（这正是 118 的 profile）。
- 作用域路由器仍受 FR 的随机性影响：同一五网 scope 的单轮运行曾留下 1 个开路
  （门正确地拒绝），两轮即闭合。
- 拼接（S3）与并行布线（S2 的真正版本）仍未做；K2/K3 只固定了接口和义务。


## 十一、K5：355 格离线对账（2026-11-26）

范围：`full4_run` 的 3 次重复 × 119 板；后两次运行缺 `0496_kitspace_f-91w` 一行
（该板第一次就 fail，没有重跑），所以共 **355 格**。每格用保存的结果板、按该板自己的
`.kicad_pro` 网类规则（`state.board_rules`），跑 lean+fab，再与 bench 行的
`claims_after`（error DRC + unconnected）比对。

| 项 | 结果 |
|---|---|
| 判定一致 | **355 / 355** |
| 一致且 clean | 351 格（bench 0 error / 0 unconnected，我们 8–82 条 claim 全证） |
| 一致且不 clean | 4 格：`0496` 一次（42 error / 13 unconnected）、`0599` 三次（36 track_width / 29 unconnected）；我们都给出 CONN unknown + fab refuted |
| 每轮 | 117 clean / 117 ok，逐轮完全对齐 |
| 验证耗时 | 中位 **0.52 s**，p95 17.0 s，max 57.2 s（0626，82 条 claim）；单核合计 861 s，6 进程墙钟 165 s |
| 横跨重复的几何方差 | 119 块板中 **118 块三次 digest 不同**（路由器非确定性），只有 1 块三次完全相同——所以“三次并集”是必要的，对账必须按保存的具体几何做 |

对账中发现并修掉第三个 fab 过严问题：**同一封装内的 pad 对不做间距检查**（KiCad 的
规则；标准的跳线/细间距封装就是这样画的）。`0054_induction-hob_temperature-sensor`
的 JMP1 两个跳线垫相距 0.10 mm，修复前 3 格全部“我们拒绝、bench clean”；加入
`_same_footprint` 豁免后 355/355。回归：
`test_same_footprint_pads_are_exempt_from_clearance`（同封装 0.1 mm → proven，
跨封装同几何 → refuted）。

### 诚实边界

- “一致”是**连通 + 网表 + 间距 + 线宽**四个谓词与 bench 判据的一致，不是与全部
  KiCad DRC 类型的一致：annular、hole、edge、courtyard 等不在证书里，4 个 dirty 格里
  我们的 refuted 也只是其中一个 fab 谓词，没有复刻 bench 的 42/36 条。
- 抽取器仍是信任桥：zones 未提取（本批板子为 0）；同封装豁免是按 KiCad 语义加的，
  若某个封装故意用小于规则的 pad 间距且需要检查，应显式声明。

## 十二、M1–M5：从“proof 模式”到运行时的领域形式化（2026-11-26）

这一轮把四个洞补上：门本身、bench/生产写板路径、猜想–定理–引理与库、领域核。
提交：`64af594`（M1）、`377e1ab`（M2）、`b8a29c0`（M3/M4）、`0eaee72`（M5）。

### 12.1 门硬化（M1）

| 改动 | 效果 |
|---|---|
| `carried` 计入已证 | Lean 对命中 carried、scope 与 claim 一致且不碰 `touched` 的 claim 记 `carried`（不要求证书）；`framework.certify` 可回放 store 里的新鲜证明 |
| `obligations` | 猜想要求的 id 集合进 Lean；`coverage_ok` + `uncovered` 由内核检查，不再只在 Python |
| pred 钉死 | `required_claims` 返回 id→pred；id 对、pred 拼错（曾被当 `foreign` 放行）现在直接拒绝；重复 id 拒绝 |
| before 状态 | 重算 `before.digest`（`drift_before`） |
| frame 上下文 | rules / layers / ref→pad 映射必须相等；state 升 `fluxpcb-state-2`，canonical 增加 `f|ref:pads`（改名封装会作废 carried） |
| 两级门 | `step.preserve`：每步跑全部 fab 谓词，已证不得丢、已干净不得变脏；`step.publish`：全板全证 |
| proof store | 语句哈希 + scope 逐项哈希 + 上下文哈希；`framework.maintain` 只重证 scope 变化的 claim（0344：23 carried / 0 changed / 0.9 s） |

### 12.2 bench/生产接线（M2）

- `verifiers/txn.py` 的每个提交在写日志前过 `step.preserve`；拒绝则恢复快照并记 `proof_denied`。
- `repair_drc.py:trim_dangling` 每轮同样过门，结束时 `publish`（全板）；`FLUXPCB_PROOF_PUBLISH=enforce` 下失败即抛，格子不能“无证 clean”。
- `router/freerouting.py` 的产出带 audit 证书并预热状态缓存；`stages` 写 `proof_layout.json`；`engine._export_fab` 先发布再出包，证书进 fab 包。
- 模式：`FLUXPCB_PROOF=off|audit|enforce`（默认 audit）。

实测（`enforce`，同一 arm/协议）：

| 板 | audit wall | enforce wall | 结果 | 发布证明 |
|---|---|---|---|---|
| 0316 | 46.6 s | **71.9 s** | 0 error / 0 unconnected | 25 proven |
| 0376 | 35.6 s | **64.3 s** | 0 / 0 | 29 proven |
| 0344 | — | **256.4 s** | 0 / 0 | 23 proven |

没有触发一次 `proof_denied`（K5 预测为零）。代价是每次提交抽取+验证约 2–4 s；
小板上相对开销明显（+25 s），0344 这种 4 分钟级板子上是摊销的。900 s/格预算内。

### 12.3 猜想–定理–引理与库（M3/M4）

- `Conjecture{claims, profile}` / `Theorem{claims, deps, lemmas, contract, digest}` /
  `Lemma` 注册表（frame、connectivity、netlist、pads、fab.*、router.route_sound、
  merge.boundary、power/thermal/signal）/ `Tactic`（router、repair operator）。
- `discharge` 检查覆盖、依赖无环、未知引理，并在全板上做一次带 carried 的验证。
- `modules.prove` 产出 theorem；`modules.merge` 校验每个 theorem 对当前模块状态的
  digest/board_fp（陈旧即拒），并把模块内网 carried：0344 merge **18 proven + 15
  carried**，不再重算那 15 条网的证书。
- 库 `schema fluxpcb-module-2`：角色→位号、期望网、引理、依赖、实例（board_fp、
  state digest、规则、proof digest、秒数）。API：`instantiate`（拒绝板上不存在的
  位号）、`prove_instance`、`check`（fresh/stale，可重证）、`find`（按期望网、位号
  前缀、pad 数打分）、`graph`（DAG+环）。CLI `library list|check|find|prove`。
  两个旧条目已迁移；0344 的 power（7 proven）与 mcu（22 proven）实例 `fresh`。

### 12.4 领域核（M5）

- 抽取器（schema 2 扩展）：via 的 drill/span、pad 的 drill、板框多边形、zone 计数
  进 canonical/digest/frame。
- fab 新增整数谓词：**annular、drill 范围、hole-to-hole、copper-to-edge**；zone 未
  建模时 clearance/edge 报 `unknown`（不再静默）。项目规则只在对应 DRC 严重度为
  error 时加入；JLC 工艺档（`--process`）另加 `PROC:ANNULAR/HOLE/EDGE/DRILL`。
- power：`power.drop_path` 在 rail 自己的铜图上做 Dijkstra（段电阻 + 过孔桶电阻），
  报最坏路径压降、路径长度、最窄宽、电流密度。0344 声明 0.3 A/0.2 A：
  **3V3 13.9 mV、5V 9.6 mV**（最坏路径，不是总长）。
- thermal：过孔阵列 θ 与 θ_ja 并联（4 孔 0.3 mm）；0344 U2 0.5 W、θ_ja 60 K/W →
  **37.7 °C**（无线时 55 °C）。
- signal：带状线 Z0、差分对长度差 `signal.skew`；spec 增加 path/kind/pairs。
- 0344 `--profile full --process jlc`：23 proven + 3 derived + 4 工艺谓词，`ok`。

### 12.5 K5 复跑（新 schema）

355 格（3 次重复 × 119 板，后两次缺 0496）在新抽取器/规则/谓词下重新对账：
**355/355 一致**（351 clean 且全证；4 个 dirty 格我们同样拒绝）。中位 0.53 s/格。

### 12.6 边界

- zones 仍未提取：有 zone 的板子 clearance/edge 是 `unknown`，publish 会拒——
  如实，不是通过。
- 工艺档只在显式声明时加入（bench 不声明，保持与 DRC 判据一致）。
- 领域结论仍只到 `derived`（模型+输入+哈希）；库里 `find` 是启发式候选，内核复核。
- 每步门的开销在 2–4 s/次提交；小板相对更贵，大板摊销。
- `carried` 的可靠性依赖 frame：rules/layers/footprints 任一变化都会作废全部 carried。
