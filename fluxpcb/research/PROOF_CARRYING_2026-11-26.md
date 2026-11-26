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

## 十三、118 个工况的完整测试（enforce，2026-11-26）

**设置**：118 个任务（`boards_full.json` 去掉 0473，与 118 任务清单一致），分成 6 片
（20/20/20/20/19/19）并行；同一 arm（`gate+ripup~full4r0`）、同一 bench 协议
（900 s/格，error DRC + 0 unconnected）；`FLUXPCB_PROOF=enforce` 且
`FLUXPCB_PROOF_PUBLISH=enforce`——每一步事务过 `preserve`，最终板过 `publish`。

| 项 | 结果 |
|---|---|
| 处理 | 118 / 118 |
| clean（0 error / 0 unconnected） | **116** |
| 被证明门拒绝 | **2**：`0496_kitspace_f-91w`、`0599_RGBMatrixPanelCPLD-PhotonBackpack`（publish 报未覆盖的 CONN 义务；同一 `claims_after` 为空是因为拒绝先于打分） |
| 与基线（full4 同一 118 板） | 基线 117/119 clean（含 0473）；去掉 0473 后正是 **116/118**，逐板相同 |
| clean 格的 publish 证明 | **116 / 116 全部 `ok`**；共 **1459** 条 claim proven |
| 每步 `proof_denied` | 0（只有两格的最终 publish 拒绝） |
| wall | 均值 **58.7 s**、中位 37.5 s、max 629.4 s；route 均值 19.4 s（基线均值 69.2 s，同一量级，单次重复，不作加速声明） |

**独立离线复核**：对 118 块保存的最终板重新按各自的网类规则跑 lean+fab，并写出一板
一张 `certificate.json`（`k6_full/offline/`）：

- 判定与 bench 行 **118 / 118 一致**（116 ok；2 个 dirty 板如实 unknown/refuted）；
- 证书合计 **1537 proven / 29 unknown / 3 refuted**；
- 耗时中位 **0.66 s**、p95 17.6 s、max 46.2 s，单核合计 304 s，6 进程墙钟 66 s。

### 分集合与对照

| 集合 | 本次（1 次重复，enforce） | 形式化前（3 次重复，按次平均） | PCBRouteAgent |
|---|---|---|---|
| D3-A | **99/99 = 100%** | 297/297 = 100% | 94.9% |
| D3-B | **10/10 = 100%** | 30/30 = 100% | 100% |
| D3-C | **7/9 = 77.8%**（118 清单不含 0473；含 0473 为 8/10 = 80%） | 24/30 格 = 每次 8/10 | 40% |

形式化前后**逐板判定相同**：同样的 0496/0599 不过，其余全过。对 PCBRouteAgent 的
比较是集合通过率（没有其逐板名单，不能做配对检验）：D3-A、D3-C 严格更高，D3-B 打平。
PCBRouteAgent 每板 1 次，本次也是 1 次，重复口径可比；形式化前的 3 次重复结论更强。

**开销**（受控对照，同板同 arm）：0316 46.6→71.9 s（+25.3），0376 35.6→64.3 s（+28.7）；
每次提交抽取+验证约 2–4 s，发布证明离线中位 0.66 s。本次 sweep wall 均值 58.7 s、
中位 37.5 s（基线均值 69.2 s；路由器方差，不作加速声明）。

**结论**：在“每一步工艺验证 + 最终全板发布证明”都打开的情况下，118 个工况的分数与
基线逐板一致；没有一块 clean 的板是“无证”的；两块本来就不 clean 的板现在先在证明门
被拒，而不是靠最终 DRC 才发现。证书与 bench 判据在离线复核中完全对齐。

**边界**：这是 1 次重复（基线是 3 次重复的并集）；118 不含 0473；两块 dirty 板的
`claims_after` 为空是 publish 异常先于打分的结果，不是 DRC 没跑。

## 十四、第一枪：Gerber 内核 + 工艺窗口（2026-11-26）

调参不是这里的工作。这一节把两个限定词换掉：**证在出厂文件上**（Gerber + Excellon，
不是 `.kicad_pcb`），**证在一个工艺窗口里**（不是名义点）。

### 14.1 Gerber 内核（`proof/gerber.py`）

- 解析 KiCad 的 RS-274X 子集：C/R/O 光圈、D01/D02/D03、G36/G37 区域、G02/G03 圆弧
  （按 5° 折线化）；Excellon 的 T 定义与 X/Y 孔；光圈宏（`%AM`）**尚不支持**，遇到
  即 `unknown`，不冒充通过。
- 坐标系：由 Edge.Cuts 的 Gerber 与 state 轮廓的 bbox 求 `x'=x+tx, y'=ty−y`；state
  轮廓是内接多边形（比 Gerber 小 2–3 µm），残差在 10 µm 内时帧就是 0——这一点由
  0008 的 2.5 µm 假偏移暴露并修正。
- **双向包含**：每个 state 铜项必须在图上、每个图上铜要素必须是 state 项（线段 1 µm，
  多边形 bbox 10 µm，容忍 KiCad 的椭圆/圆角多边形化）。no-net 铜、NPTH、槽孔都按
  真实语义建模：no-net 铜以匿名网进入 state（否则被当作“同网”豁免），NPTH 是孔不是
  铜，槽孔用两轴算环宽。
- 网继承：图上要素按几何匹配拿回 state 项的 net/ref，然后**用同一组 fab 谓词**在图上
  重跑（clearance/width/annular/drill/hole/edge），不需要重新相信网表。
- **负控（0344）**：把 F.Cu 图整体 X 移 50 µm。board 核 23 proven 照过；Gerber 核
  **349 extra / 349 missing**、clearance `gap −500 µm`，拒绝。这是不可伪造的判据：
  翻译链上的偏移，模型看不见，工件看得见。

### 14.2 工艺窗口（`proof/process.py`）

每个谓词的扰动按物理机制分配，而不是统一加一个“安全系数”：

| 谓词 | 扰动 | 余量 |
|---|---|---|
| clearance | 蚀刻使两侧边缘后退（间隙变大） | `gap + 2·etch − rule` |
| width | 蚀刻使线宽变窄 | `w − 2·etch − rule` |
| annular | 孔偏 + 层间套准 + 蚀刻 | `ann − rule − drill − reg − etch` |
| hole_to_hole | 两个孔各自偏移 | `gap − rule − 2·drill` |
| edge | 板框铣切位置误差 | `gap − rule − rout` |

`jlc-1oz`：套准 75 µm、孔偏 50 µm、蚀刻 10 µm/边、铣切 50 µm；`tight` 是另一档。
窗口是**声明模型**，证书里带 provenance，可换成实测能力数据而不改任何谓词。

0344（`--process jlc --robust jlc-1oz`）：名义 27 proven 全过，余量
**clearance +21.6 µm、width +10 µm、annular −110 µm、hole_to_hole +186 µm、edge −10.6 µm**
→ 鲁棒拒绝。Gerber 核在同一窗口下给出**完全相同**的余量（图上工件与模型一致）。
0316：width −20 µm、annular −10 µm；0376：width −20 µm。这不是“板子不合法”，
而是“在声明的工艺窗口里不合法”——两者必须分开说。

### 14.3 118 块出厂文件

对 118 块保存的结果板导出 Gerber+Excellon（kicad-cli），逐块跑 Gerber 核 + JLC 工艺窗口：

| 判定 | 数量 | 含义 |
|---|---|---|
| 全过 | **17** | 双向包含 + 板规则 + JLC 工艺 + 鲁棒余量 |
| 仅鲁棒余量 < 0 | **49** | 工件与模型一致，但在 jlc-1oz 窗口下不鲁棒（多数是 0.2 mm 线宽被蚀刻扣掉 20 µm） |
| 仅 JLC 工艺限值 | **27** | 板规则允许、JLC 最小能力不允许（环宽/边距） |
| 双向包含失败 | **8** | 图的几何与 state 不能一一对应（多为旋转/异形 pad 的绘图子集） |
| 板规则 fab | **1** | 0599（本来就不 clean） |
| 光圈宏不支持 | **16** | 解析器 `unknown`，不冒充通过 |

耗时：中位 **2.0 s**/板，p95 25.9 s，max 104.7 s；118 块 4 进程墙钟 **224 s**。

### 14.4 抽取器被这一枪打出的三个问题（已修）

1. **no-net 铜被静默丢弃**：0344 有 18 个无网 pad 的铜不在 state 里，clearance 对它们
   完全不检查。现在以匿名网进入 state（`~<uuid>`），不参与连通/网表，但参与 fab；
   K5 复跑仍 **355/355**。
2. **NPTH 被当成铜**：0376 的 4 个安装孔让 Gerber 双向包含报 8 个 missing；现在 NPTH
   是孔（参与 drill/hole），不是铜（不参与 clearance/annular/edge）。
3. **槽孔只用一个轴**：0316 的 USB 座 1.15×0.5 mm 槽孔被当成 1.15 圆孔算环宽，
   得到 −75 µm 的假违规；现在两轴分别算，环宽 200 µm。

### 14.5 边界

- 光圈宏（16 块）与 8 块的双向包含失败是解析器覆盖问题，不是“证过了”；证书里是
  `unknown`/拒绝，不冒充。
- 工艺窗口是声明模型，不是 JLC 的承诺；换实测数据只改 `BOXES`，不改谓词。
- Gerber 的网继承来自 state 的几何匹配；网表本身的正确性仍是 spec→netlist 那一层的事
  （下一枪）。
- 判据仍是 1 次重复；鲁棒判定与 bench 判据是两件事，分开报告。

### 14.6 第五、七枪的基础（已落地，未强制）

- **Pareto 比较**（`proof/compare.py`）：两份证书按共享谓词的余量向量比较，
  `a_dominates` / `b_dominates` / `incomparable` / `equal_on_common`，**没有权重**；
  `fluxpcb.proof.cli compare --a --b`。谓词集合不同时只比交集并列出差异。
- **反驳即义务**（`proof/repair.py`）：gate 报告里的每条未证 claim、以及每个负余量，
  都转成 `{claim, kernel, status, targets, delta_nm}`；`measure = (义务数, 负余量元组)`
  是良基字典序，`terminates(before, after)` 就是一次严格下降。publish/certify 的
  返回已有 `obligations`/`measure`；repair 规划器与循环强制是下一步，不在这一枪里
  假装已经完成。

## 十五、第二枪：spec → netlist → layout 的两级细化（2026-11-26）

`NETLIST` 一直是“板子的网 == 给定的网表”。这句话对一张**可能错的网表**也成立——循环。
这一节把顶层补上：spec 是 conjecture，netlist 是它的定理，layout 是 netlist 的定理。

### 15.1 三个箭头，三个检查器

| 层 | 文件 | 检查器 | 失败的样子 |
|---|---|---|---|
| spec → netlist | `proof/spec.py` | 领域核（本模块）：parts 存在、rail 的 source/sink 位号在该网、禁止网对不共享焊盘、接口网存在 | 一块每根铜都连通、DRC 全过的板，仍然“不符合意图” |
| netlist → layout | `proof/claims.py` + Lean | `NETLIST`/`CONN` 证明板子实现了网表 | 开路/错连 |
| layout → 工件 | `proof/gerber.py` | 双向包含 + 同组 fab 谓词 | 导出偏移/丢 pad |

`framework.certify` 的证书现在带 `refinement` 块：`spec_ok`、`layout_ok`、`theorem`
（spec 摘要 + state 摘要 + 义务 id 的哈希）、`unformalized`（无法形式化的需求逐条列出）。

### 15.2 0344 的真实 spec

`projects/audit/0344/spec.yaml` 写的是**意图**，不是布局：VPP 由 P1 输入、F1 熔断；+5V 由
F1 供 U2/D6/C3/R5；+3V3 由 U2 供 U1/C2/C4/R2/R3/R9/R10；VPP 与 +5V、+5V 与 +3V3 禁止
同网；UART 的 TXD/RXD 必须存在。结果：

- spec 28 条义务全过，`layout` 23 proven，`refinement.ok=true`；2 条需求进
  `unformalized`（叠层、外壳），不假装证过。
- **负控**：把 spec 改成“P3 必须在 VPP 上”。布局仍然 23 proven、每根铜都合法；
  refinement 失败：`RAIL:VPP:sink:P3: P3 has no pad on VPP`。这正是循环被打破的地方：
  合法布线可以完美实现一张错误的网表，spec 层把它抓住。

### 15.3 类型化网

`interfaces[].type` 现在进入 spec 义务（接口必须存在），并继续由 `conjectures.from_spec`
生成 domain claim：`single_ended`/`stripline` → Z0，`diff` 对 → Z0 + skew，rail →
最坏路径压降/电流密度。类型不是注释，它决定该网要证什么。

### 15.4 边界

- spec 仍是每个项目手写的；从原理图自动抽取 contract 是下一层（M4 的 `find` 只做检索）。
- spec 检查是**领域核**（peer kernel），不是 Lean：它是组合/成员关系，不需要几何内核；
  证书记录 kernel/version/digest，和其他领域结论同级。
- `unformalized` 逐条可见；C2 的 `refinement.ok=false` 会让整张证书 `ok=false`，
  不会用 layout 的 23 proven 掩盖意图层的失败。

## 十六、第三枪之一：装配核（courtyard）（2026-11-26）

一块板可以每根铜都合法、每张 Gerber 都一致，却**装不出来**。`proof/assembly.py` 是
装配侧的领域核：

- 读 F.CrtYd/B.CrtYd 的 courtyard 多边形；没有 courtyard 的封装记 `unknown`，
  **不用 body bbox 冒充**（相邻器件的焊盘交错是常态，bbox 会造假违规）。
- 只在**同一面**上比较 courtyard 相交；NPTH 安装孔不算被装配的器件。
- 每个 courtyard 必须落在板框内。
- 证书带 `n_footprints`、`overlaps`、`outside`、`unknown`、theorem digest；
  `certify --assembly` 记录，`--assembly-strict` 在有任何 unknown 时拒绝。

0344：**21 个封装有 courtyard，0 相交、0 出界；17 个封装没有 courtyard**（U1/P1/W1–W9 等
来自没有 courtyard 的库封装）。所以这颗板能证的是“已有的 21 个 courtyard 不冲突”，
不能证“整板可装配”——`complete=false`，strict 模式会拒。这正是把“unknown”与“pass”
分开的意义：不是 34/38 通过，而是 21 条已证 + 17 条未知。

边界：courtyard 只覆盖 2D 占位；高度/间距、paste 面积比、拼板可分离性与 ICT 探针可达
仍未建模，是装配核的下一层。

## 十七、基础补强：下降测度、库假设、匿名网（2026-11-26）

- **下降测度进日志**：`step.preserve` 返回 `obligations`/`measure`；`transactional_repair`
  把它附在 **commit 条目**的 `proof_measure` 字段（义务数 + 负余量元组）。这是第三枪的
  “终止论证”的可观测形式——还没强制，但每次提交都能被审计是否严格下降。
  （曾把 measure 记成独立日志条目，`repair_stepwise` 取第一条日志的 action，读到的是
  measure 而不是算子 → 单测立刻抓到；已改为附在 commit 条目上。）
- **库假设（theorem schema 的适用条件）**：entry 增加 `hypotheses`（nets/parts/
  min_pads/clearance_min_nm）；`instantiate` 先检查假设，类外直接拒绝。
  power 条目要求 VPP/+5V/+3V3/GND、U2/F1、U2≥4 pad、clearance≥150 µm；
  core 条目要求 +3V3/GND、U1、U1≥20 pad。0344 上两个实例 **fresh**（7 / 22 proven）。
- **匿名网不进模块边界**：no-net 铜以 `~uuid` 进入 state 后，`modules.derive` 曾把它们
  当成 boundary 网，core 条目一度出现 19 个 PADS claim（5 真边界 + 14 匿名）。现在
  匿名网不参与 boundary/internal；core 回到 22 proven，边界仍是 5 网。
- `register` 按 (board, board_fp) 替换实例，旧板路径上的陈旧实例不再堆积。

## 十八、信任三角：独立文本解析器（2026-11-26）

extractor（pcbnew → state）一直是唯一的信任桥。`proof/kicad_text.py` 是第二条腿：
自己读 `.kicad_pcb` 的 S-expression，按 uuid 重建线段/圆弧/过孔/焊盘，再与 pcbnew 的
state 逐项对账。

- 变换：footprint `(at x y rot)` + pad `(at dx dy)` → `anchor = fp + R(fp_rot)·(dx,dy)`；
  有 `(drill (offset ox oy))` 时铜中心 = `anchor + R(pad_rot)·(ox,oy)`（Gerber 闪点证实）。
- 比较：uuid 存在性、net、layer、端点/中心（1 µm）、线宽/孔径、drill（含槽孔两轴）、npth。
  焊盘不比多边形（pcbnew 负责多边形化），只比中心/网/孔/层。
- 圆弧单独标记：state 把弧存成弦，文本能看出弧；`--trust-strict` 在有圆弧时拒绝。
- CLI `certify --trust` 把结果写进证书 `trust` 块；`--trust-strict` 把 chord 近似当拒绝。

**118 块板实测：118/118 逐项一致**（0 mismatch / 0 missing / 0 extra；这批板没有圆弧）。
过程中文本解析器自己先错了两处，被对账抓住并修正：

1. 焊盘类型读错下标（`(pad "1" thru_hole oval …)` 的 type 是第 2 个 token，不是第 3 个），
   把 20 块板的 NPTH 当成通孔铜；
2. `(drill (offset 0 0.2))` 的铜中心没加偏置，0020 有 15 个焊盘差 0.2 mm——查 Gerber
   闪点后确认 state 是对的（铜在 101.4，不在 anchor 101.6），文本解析器修正。

所以这条腿的价值是双向的：它验证 state，也验证自己；两次错误都是“工具噪声”，不是
“模型通过”。边界：只覆盖 KiCad 10 语法；zone 不解析；焊盘不比多边形；圆弧在 state 里
仍是弦（strict 会拒）。

## 十九、第四枪：schematic → PCB netlist（源头细化）（2026-11-26）

`proof/schematic.py` 把顶层从“手写 spec”换成**原理图本身**：kicad-cli 导出 pin 级网表，
PCB 侧读 (ref, pad 号) → net，证明两者的**分区等价**——不比网名，因为改名正是布局允许
做的事。

义务：

| 义务 | 含义 |
|---|---|
| `SCH:<net>` | 原理图一个网的所有 pin 都在 PCB 上，且落在**同一个** PCB 网（无开路/无再分裂） |
| `PCB:<net>` | 一个 PCB 网没有把两个原理图网并在一起（无短路） |
| `PIN:<r>:<p>` | 原理图 pin 在板上有对应 pad 号（否则是未放置） |
| `COMP:<ref>` / `VALUE:<ref>` | 器件在位；值一致（大小写/空格归一） |
| `FOOTPRINT:<ref>` | 原理图符号有 footprint 指派 |
| `EXTRA:<ref>` | 板上有带网 pad、但原理图没有的未标注封装 |

判定分两层：`partition_ok`（铜的分区是否实现原理图）与 `identity_ok`（BOM/位号/封装
是否可审计）；`ok = 两者都过`。

### H7 飞控实测（33 器件 / 219 网 / 68 个多 pin 网 vs PCB 33 位号 / 74 网 / 484 pad）

- `partition_failed = 29`：U3 的 5 个 pin 在 PCB 上按位号找不到（U3 是 `ICM-45686`，
  原理图符号没有 footprint）；`J10.S1` 屏蔽 pin 没有对应 pad。
- `identity_failed = 34`：**33 个符号全部没有 footprint 指派**；板上有一个未标注封装
  `REF**`（value `LGA14_2P5X3X0P91_IVS`，fp `ICM-42688-P`，14 pad，其中 8 个带网）
  ——**原理图要 ICM-45686，板上放的是 ICM-42688-P**，且位号未标注。
- 布局层本身（在重复 id 守卫之前）已报 47 条 CONN unknown、2 refuted、2 invalid：
  这块 H7 并不是“只差工艺”。
- **重复 UUID**：U4 与 U5 的 10 个 pad 共享 uuid（坐标差正好是 U5→U4 的平移）。
  `state.extract` 现在**拒绝**这种 state：`itemById` 会二选一，任何关于这些项的证明都
  不可信。信任三角同样报 duplicate。

结论：H7 板不是“benchmark 强、复杂板弱”的问题，而是**源头与布局的身份没有闭合**：
无 footprint 指派、IMU 型号不一致且位号未标注、10 个重复 UUID。要可加工/可审计，先修
这些，再谈 DRC/布线质量。

边界：网表导出依赖 kicad-cli（受信工具）；符号无 footprint 时无法验 identity；netlist
version E 的字段解析只覆盖 comps/nets/nodes；重复 UUID 现在被拒绝而不是“取第一个”。

### 19.1 K5 抓到的一次 canonical 回归

给 NPTH 加 `|npth` 时，Lean 的 `Item` 解析了 `npth`，但 `canonical` 的字符串拼接**漏了
这一项**：board state 的 Python 摘要含 `|npth`，Lean 重算的不含。K5 复跑立刻从 355/355
掉到 289/66（66 格 `translation drift`），全部是有 NPTH 的板。补上拼接后 `lake build`
重跑，**355/355** 恢复。这是 K5 作为“内核回归测试”的价值：它不只看 bench 判据，还看
Python/Lean 两个 canonical 是否逐字节一致。

## 二十、H7 修复实验：把“效果一般”拆成可修的项（2026-11-26）

在 `z_lab/fp/h7fix/` 的副本上做了三件事（fixture 不进仓库）：

1. 对 10 个重复 pad UUID 调 `ResetUuid()`；
2. 把未标注的 `REF**` 改名为 `U3`；
3. 不改原理图；用 `--alias U3=REF**`（声明式别名，写进 theorem）验证分区。

修复后的状态可以提取（1147 项，digest 有效），信任三角 **0 mismatch**。

| 层 | 结果 |
|---|---|
| schematic partition | **只剩 1 个缺陷**：`J10.S1` 屏蔽 pin 没有对应 pad（`SCH:GND` + `PIN:J10:S1`） |
| schematic identity | 33 个符号仍无 footprint 指派；`VALUE:U3`：原理图 `ICM-45686` vs 板上 FPID `ICM-42688-P` —— **不同型号的 IMU** |
| layout | **23 proven / 50 unknown / 1 refuted**：47 条 CONN 未证（不是“全连通”），1 条真实间距违规 |
| fab 细节 | `FAB:CLEARANCE` 101.7 µm < 150 µm（`ground_copper` 上 3V3 走线 vs I2C1_SCL 过孔）；`FAB:ANNULAR` 因 **J10 的铣槽超出 pad bbox** 记 `unknown`（不是环宽违规） |

这一轮又修了三个模型问题：

- **声明式别名**：板材位号未标注时，`--alias U3=REF**` 把原理图 ref 绑到板 ref，别名进
  theorem digest；identity 仍按原理图判定，不用别名掩盖“符号无 footprint”。
- **声明式 no-connect**：KiCad 的 `unconnected-(U1-NC-PadE1)` 这类单 pin 网不再算
  “未放置 pin”（H7 有 146 个 NC pin，之前会把分区失败数从 2 抬到 292）。
- **铣槽的环形判定**：槽孔两轴先按 pad 方向旋到板轴；若槽本身超出 pad bbox（J10 的
  1.7 mm 槽在 1.0 mm 宽的 pad 上），annular 不是正确的谓词，记 `unknown` 而不是
  `refuted`。

**结论**：H7 不是“再跑一次布线”的问题。要先修身份（footprint/型号/位号/UUID），
再修 47 条未证连通与 1 条真实间距，才谈可加工。框架现在把这张清单逐条给出，并且
拒绝把“模型看不见”当成“通过”。

边界：h7fix 是副本，原理图仍无 footprint 字段；别名是声明不是推断；47 条 unknown 是
“未证”而不是“已断”，需要 DRC/可视复核确认。

## 二十一、第五枪：光圈宏（Gerber aperture macros）（2026-11-26）

16 块板的 Gerber 用光圈宏，之前解析器直接报 `unsupported`。现在 `proof/gerber.py`
识别 KiCad 自己会写的三个宏，并把每个宏闪点合成**一个多边形**（保持与 state 的一对一
双向包含）：

| 宏 | 合成 |
|---|---|
| `RoundRect` | 四角坐标 + 半径 → 外扩 `r` 的圆角矩形多边形 |
| `Outline4P` | 四个角点 + `$9` 旋转 → 四边形 |
| `RotRect` | 长/宽/旋转 → 旋转矩形 |

其他宏仍然 `unsupported`（不伪造形状）。落地时踩了两个单位坑，都被测试钉住：
参数在 `%ADD` 里是 mm，闪点坐标是 nm（先混用导致 bbox 到 1e14）；解析器工作在**绘图帧**
（board +y 在 Gerber 里是负），单测最初按板帧写错了期望。

**118 块出厂文件复跑**（宏支持后）：

| 判定 | 数量 | 变化 |
|---|---|---|
| 解析错误 | **0** | 16 → 0 |
| 全过（包含 + 板规则 + JLC + 鲁棒） | **21** | 17 → 21 |
| 仅鲁棒余量 | 54 | 49 → 54（宏板现在能算余量） |
| 仅 JLC 工艺限值 | 28 | 27 → 28 |
| 双向包含失败 | 13 | 8 → 13（3 块宏板从“解析错误”变成“形状对不上”，另 2 块新暴露） |
| 板规则 fab | 2 | 1 → 2 |

双向包含的 13 块是下一枪的靶子，已分类：

- **弧/自定义焊盘**：如 0092 有 197 条 extra 线段、只缺 2 个 pad——Gerber 把弧或自定义
  pad 画成多段描边，而 state 里是一段/一个多边形；
- **宏形状对不上**：0009/0093 的宏 pad 中心出现 0.1 mm 小圆（孔），而 state 是 1.0×3.7
  的连接器焊盘——需要把“孔与铜”的绘制语义和宏的旋转/角点顺序一起核准。

边界：宏支持只覆盖 KiCad 的三个；用宏光圈**画线**（而非闪点）仍不支持；解析错误变成
“可检查但被拒”本身是进步——它不再把“不知道”混成“通过”。

## 二十二、双向包含的 13 块：诊断与一次“拒绝作弊”（2026-11-26）

宏支持后仍有 13 块双向包含失败。诊断（`extra` 的几何类型、`missing` 的项）分成三类：

| 类 | 例子 | 现象 |
|---|---|---|
| 描边/自定义焊盘 | 0098（490 extra）、0026、0027、0288、0011、0092 | Gerber 把 pad 的铜画成多段 **D01 描边**，state 是一个多边形；段在 pad 内/边上 |
| 小圆/孔标记 | 0065、0066、0078、0084 | 0.1 mm 小圆落在 pad 中心，实为**孔**画在铜层 |
| B.Cu 缺铜 | 0009、0093 | 连接器屏蔽 pad 在图上只剩一个孔圆，B.Cu 无铜 |

试过一条捷径：把“完全落在某个 board item 内部”的 Gerber 图元**并入**该 item。它把
0009/0093 的 extra 降到 0，但**被否决**：一条终止在 pad 内部的异物走线（stub）也会被
吞掉，这是把“看不见”伪装成“通过”。保持不隐藏铜的原则，回退了这条捷径。

保留的是**语义上无争议**的那一半：

- **孔标记不是铜**：未匹配的小圆若圆心与 Excellon 孔重合且半径 ≤ 孔半径，计入
  `hole_marks` 并从铜项里剔除（孔由钻孔文件负责）。
- 修掉一个真 bug：Excellon 坐标也在**绘图帧**，之前只对 hole-only item 做了变换，
  与 flash 的匹配用的是未变换的孔坐标——于是未匹配 flash 永远拿不到 drill，孔标记
  也永远识别不出来。现在孔坐标一次变换到板帧，flash/孔标记/hole-only item 共用。

**结果**：118 块仍然是 21 全过 / 54 鲁棒 / 28 工艺 / 13 双向包含 / 2 板规则——
这次没有用启发式去“消掉”失败数。13 块的正解是给 state 增加**弧/描边形状**（带宽度
的折线）并让 Lean/fab 认识它，而不是在匹配层放宽。

边界：孔标记的判定依赖钻孔文件与闪点同帧；描边焊盘要等 state 模型扩展。

## 二十三、第六枪：覆盖率等价 + 铜图形（2026-11-26）

逐项双向包含对“描边/自定义 pad/弧”太脆。这一枪把“出厂文件等于模型”升级为**带声明
分辨率的集合等价**，并补上了 state 漏掉的一类铜：**铜图形**。

### 23.1 覆盖率等价（`proof/raster.py`）

- 同一网格（默认 10 µm，大板按 6000 格/轴粗化）栅格化**板铜**与**图铜**；每层要求
  `图 ⊆ 膨胀1(板)` 且 `板 ⊆ 膨胀1(图)`——1 格容差只吸收边界舍入。
- **负控**：整体平移 50 µm → 失败；平移 1 格（10 µm）→ 通过；板跨 >250 mm → `unknown`
  （不是通过）。
- `gerber.check` 改为 **双向包含 OR 覆盖率等价**。覆盖率成立时：clearance 由板证明
  **转移**（铜集相等），余量从**板状态**计算（图侧未匹配图元没有网），其余 width/
  annular/drill/hole/edge 仍在图上跑。证书新增 `coverage`（分辨率、逐层缺口）。

### 23.2 铜图形（state 完整性）

B.Cu/F.Cu 上的图形（logo、异形标记）是铜，KiCad 会画、DRC 会查，之前的 state **完全
没有**——fab 核看不见它们。现在 `state.extract` 读**板图形**与**封装图形**：

- 填充图形 → 多边形；描边 → 沿边/圆周的带宽度线段（弧按 5° 折线）；
- 新增 `graphic` 标记（`canonical` 的 `|gr`，Lean `Item.graphic`）：**是铜，但不是走线**
  ——clearance/edge 照查，width（track 规则）跳过；
- 0098 的 3 个铜图形展开为 480 条边框段，state 从 104 项变 584 项；0026 与 0520 因此
  从“双向包含失败”变为**覆盖率等价通过**（0026 落到鲁棒类，0520 落到鲁棒/工艺类）。

### 23.3 匹配加速与一次回归

- 匹配器加了空间索引（2 mm 网格、候选外扩 20 µm）；0520 的检查从 >10 分钟降到 85 s。
- 索引第一版只按 `layers[0]` 建键，多层的 via/pad 在第二层查不到 → 118 块只剩 **3 全过**。
  改为**每一层都建键**后恢复；这次回归由全量 sweep 立刻暴露。

### 23.4 结果（118 块出厂文件）

| 判定 | 数量 |
|---|---|
| 全过 | 21 |
| 仅鲁棒余量 | 55 |
| 仅 JLC 工艺限值 | 28 |
| 双向包含失败 | **12**（13→12） |
| 板规则 fab | 2 |
| 解析错误 | 0 |

剩余 12 块已分类：0009/0093 是**图本身缺铜**（屏蔽 pad 只剩 0.1 mm 圆点，K275 没画
pad 铜）；0098/0092 是板/图 pad 形状不一致；0065/0066/0078/0084 是少量残差；
0021/0027/0288/0011 仍是描边/自定义几何的边角。它们不是“再放宽匹配”能解决的，要么
修绘图/板数据，要么给 state 增加自定义 pad 的原语。

### 23.5 验证

- **K5 355/355**（铜图形进入 state 后仍与 DRC 判据逐格一致；耗时 281 s）。
- 全套件（下一步复跑）。
- 测试：覆盖率 50 µm 负控/1 格容差、孔标记、宏形状/闪点。
