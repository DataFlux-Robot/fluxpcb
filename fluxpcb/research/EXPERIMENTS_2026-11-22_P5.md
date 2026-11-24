# 能力层实验注册 — 2026-11-22（P5）

依据：五路并行综述（元开发循环、LLM 规划+外部求解器、全局搜索/逃逸/层分配、
课程与技能库、多智能体设计团队）。

---

## 0. 五路综述的收敛判据

1. **元循环的诚实增益是 +2–8pp，不是 +20pp。** HarnessPro +2.0pp（主要降本）；
   GEPA 在其域内 +12–16；AlphaEvolve 是逐问题、非泛化 harness。选择必须用
   **per-instance Pareto + 从未用于选择的 held-out 审计**；小验证集会被
   winner's curse 放大（16 项验证集可虚增 13–20pp）。
2. **LLM 的价值在粗决策，不在几何。** 最强模式是"LLM 从有界菜单选一个
   类型化工件 → 确定性引擎执行 → 批评者回传局部失败证据"。PCBWorld 实测：
   交互式 0.65 / 计划-执行 0.19 / 无引擎 0.02 / Freerouting 0.80。
   PCBRouteAgent 的无-LLM 臂只有 53.5%（低于 Freerouting 80%），其 LLM 增益
   很大程度是在修一个弱执行器；必须用**规则化 guide 生成器**做对照，
   而不是 no-LLM。
3. **全局决策的正确顺序是：容量 → 层/逃逸 → 摆放 → 布序 → 详细布线。**
   容量不可行时，任何搜索都收敛到同一残差。B-Escape 14/14 vs Allegro 7/14；
   COALA 并发层分配 -32% overflow；0.8mm pitch 在 0.1/0.09 规则下
   **每通道 1 条线**，每个信号层大约多逃逸一圈球。
4. **课程应按能力带/后悔值，不按层数/密度。** LOFO（留一板族）是主协议；
   聚合分数会被 99 块小板支配（当前 91.9/90.0/44.4，gap 47.5pp）。
5. **技能库必须有准入与退休。** 未治理的库会漂移到低于无库基线；
   VaG 预提交门 72% vs 无门 62%；Library Drift +0.328 vs 无注入 +0.002。
6. **多智能体不是默认更好。** 强单 agent 同工具是严肃基线；manager 只有
   **能验证**时才改善质量；交接会侵蚀约束（must→maybe 76.7%，
   多跳压缩 97.2%）。专家只在"拥有可分离工件+验证器"时有效
   （power+SPICE、DFM+CAD、analog sizing）。

---

## 1. Tier 1（高风险/高回报）

### T1-1 H7/BGA 容量可行性门（先于任何搜索）

- **类别**：容量/逃逸拓扑
- **假设**：若在布线前计算"每通道线数 × 信号层数 vs 需逃逸球环数"，
  则 H7 的 0 opens/9 clearance 会被判定为**容量边界或布序问题**；
  若是容量不足，任何 Freerouting 重跑都会收敛到同一残差（已被 P4 的
  4/4 回滚印证）；若容量有余，则残差是布序/逃逸策略问题，可用
  B-Escape 式同时逃逸修复。
- **依据**：综述 C §3（B-Escape 14/14 vs Allegro 7/14；0.8mm pitch
  1 trace/channel；每信号层约一圈球）；综述 A/B（open-loop 搜索在
  不可行配置上无效）。
- **指标**：traces/channel、需逃逸环数、可用信号层、容量余量；
  H7 的 opens/clearance 是否可归因。
- **运行估计**：分析 10 分钟；若需改配置再验证 1–2 小时。
- **风险**：容量公式对 pad/antipad 近似；用板实测量校准。

### T1-2 有界类型化 guide-patch 规划器（LLM 或规则）

- **类别**：LLM 规划 + 外部执行器
- **假设**：若规划器只输出 `{net_order, corridor/via-region 许可,
  rip-up 范围, 预算}` 的类型化 patch，由 Freerouting 执行、DRC+连通判定，
  则 14 块失败板的 clean-pass 会提升；因为规划在几何之前提交全局结构，
  且 patch 可验证/可回滚。对照必须是**规则化 guide 生成器**，不是 no-LLM。
- **依据**：综述 B §2–§4（PCBRouteAgent guide patch +41pp within stack；
  DRC-Aid 局部修复 92.5% vs 83.3% 规则；LLM-Modulo 需要 sound critic）；
  综述 C §2（COALA 并发层分配）。
- **指标**：失败板 clean-pass、plan 分叉率、WL/vias、每板调用数/成本。
- **运行估计**：工程 3–5 天；计算 1 小时（14 板 × 2 臂 × 3 seeds）。
- **风险**：Freerouting 不暴露 net order/region 接口；需先做 DSN 层面的
  region/keepout 注入验证。

### T1-3 真实元开发循环（结构化候选 + 分级分数 + held-out 审计）

- **类别**：元开发循环（组件 1–3）
- **假设**：若元循环搜索 10–20 个**结构旋钮**（层计划、逃逸侧、net 分组、
  rip-up 策略、repair 策略），用分级 artifact 分数（DRC 类×数量、unrouted、
  WL、vias、层数）而不是二值 pass，并在训练 coreset 上 Pareto 选择、
  在留出板族上审计，则 worst-family clean-pass 提升 ≥3pp；因为当前循环
  是 no-op（P3 已证），而文献的诚实增益是 +2–8pp。
- **依据**：综述 A §1–§6（HarnessPro/GEPA/Pareto/held-out）；综述 D §2
  （能力带/LOFO）；综述 A §6（16 项验证集虚增 13–20pp）。
- **指标**：worst-family clean-pass、family gap、transfer ratio、
  每族贡献、成本；3 seeds + Wilson CI。
- **运行估计**：工程 3–4 天；计算 2 小时（20 板训练 × 10 候选 + 留出族）。
- **风险**：结构旋钮在 Freerouting 上可操作性有限；先做 DSN 可表达的子集。

### T1-4 验证准入的技能库（7 个算子 → 参数化宏）

- **类别**：技能库/跨族迁移
- **假设**：若 7 个修复算子变成带前置条件/参数/作用域的宏，准入需
  held-out 板 replay（严格降低加权违规、不新增违规类、第二 oracle 不回退）
  且通过边际增益门，退休按每族贡献，则 leave-large-out clean-pass
  高于无库基线；因为 Library Drift/VaG 显示有门 +0.3/72%，无门会漂移。
- **依据**：综述 D §3（VaG/Library Drift/SkillsBench）；综述 E §3
  （交接约束侵蚀 → 宏必须带前置条件与 scope）。
- **指标**：worst-family、每族 `ĉ`、库大小-通过率曲线、token/step 匹配。
- **运行估计**：工程 3 天；计算 1 小时。
- **风险**：7 个算子本身有限；先做"准入/退休"骨架，不追求数量。

---

## 2. Tier 2（有据、低成本）

| ID | 内容 | 依据 | 指标 |
|---|---|---|---|
| T2-1 | H7 容量报告（不布线） | 综述 C §3 | traces/channel、环数、余量 |
| T2-2 | LOFO 协议 + worst-family 指标 + Wilson CI | 综述 D §2 | family gap、transfer ratio |
| T2-3 | 结构化配置组合（10 个不同配置 ×1 次，而非同配置多 seed） | 综述 C §4.5 | 每评估增益 |
| T2-4 | 保真度阶梯（ratsnest→congestion→部分布线→DRC）+ retention 测量 | 综述 C §4.4 | 5–10× 少跑全 DRC |
| T2-5 | 约束账本（must/should、provenance、verification status） | 综述 E §4 | must 约束下游存活率 |
| T2-6 | 局部修复有界菜单（DRC-Aid 式） | 综述 B §1 | 修复成功率 vs 规则 |
| T2-7 | StrategyStore 改为能力画像键（非 family+layers 哈希） | 综述 D §3 | 跨族可检索率 |
| T2-8 | 子代理硬编码常量参数化（0.15/0.4/1.0 → 规则表） | 综述 E §3 | 常量计数=0 |

---

## 3. 校准自检（Tier 1）

| 检查 | 回答 |
|---|---|
| 专家会真正惊讶吗？ | T1-1 会：若 H7 的残差被证明是容量不可行，则"0 opens/9 clearance"的解释完全不同，且所有搜索方法都无解。T1-2 的"guide patch vs 规则化 guide"对照是文献里缺失的实验。T1-3/T1-4 是成熟方法的正确应用，不宣称新颖。 |
| SOTA 启发还是稳妥迭代？ | T1-1 来自 B-Escape/COALA 的容量与并发层分配；T1-2 来自 PCBRouteAgent/LLM-Modulo/DRC-Aid；T1-3 来自 HarnessPro/GEPA/Pareto；T1-4 来自 VaG/Library Drift。均非在给定基线上调参。 |
| 是否有 Tier 1 其实是超参调整？ | 无。T1-1 是可行性判定；T1-2 改决策接口；T1-3 搜索结构配置；T1-4 加准入/退休。没有一项调 max_passes/clearance/线宽。 |
| 降级项 | ①"增大 max_passes/多 seed"——P4 已证伪；②"LLM 直接出坐标"——OmniLayout 79.8% overlap；③"NSGA-II 全摆放搜索"——代理与 DRC 相关性差；④"多智能体默认更好"——同预算下常输给强单 agent。 |

---

## 4. 执行结果

### T1-1 H7 容量门 — 已完成（决定性结果）

`work/pcbworld/h7_capacity.py` 读 H7 板与项目规则，在布线前计算容量：

| 量 | 值 |
|---|---|
| BGA | U1，265 pads |
| pitch | **0.8 mm** |
| pad 尺寸 | **0.4 × 0.4 mm** |
| pad 间隙 | **0.4 mm** |
| 规则 | min_track **0.127**，min_clearance **0.15** |
| 每通道可容纳 | `floor(0.4 / (0.127+0.30)) = **0**` |
| 需逃逸圈数 | **9**（ring 0–8） |
| 铜层 / 估算信号层 | 6 / **4** |
| 容量（环） | **4**（按 1 trace/channel 计；实际为 0） |
| 余量 | **−5 环** |
| 判定 | **capacity_infeasible_at_this_stackup** |

**结论（改变了 H7 的解释）**：H7 的 0 opens / 9 clearance 不是"路由器路径
选错"，而是**当前配置下唯一能连通的方式**——0.4mm 间隙装不下
0.127mm 线 + 两侧 0.15mm 间距（需 0.427mm）。路由器把间距压到 ~0.10mm
才连上，于是产生 9 条 clearance。**任何 Freerouting 重跑/搜索都会收敛到
同一残差**（与 P4 的 4/4 回滚一致）。

**可行的修复方向（按代价）**：
1. pad 0.4 → **0.35mm**：间隙 0.45 > 0.427，可容 1 trace/通道；
2. 规则改为 0.1mm 线 / 0.1mm 间距（若 fab 允许）：0.3 < 0.4；
3. HDI / via-in-pad / 微孔：改变逃逸原语；
4. 增加信号层：9 环需 ~9 个信号层（不现实），或去球/降密度。
**不是**调 max_passes、换 seed、或让 LLM 重新规划。

### T1-2/T1-3/T1-4 — 已登记，未执行

- T1-2 guide-patch：需先验证 DSN 的 region/keepout 注入；工程 3–5 天。
- T1-3 真实元循环：需结构化候选 + 分级分数 + held-out；工程 3–4 天。
- T1-4 技能库：需准入/退休骨架；工程 3 天。

### T2 指标（从现有 118 板结果直接计算）

- family gap = 91.9% − 44.4% = **47.5pp**；
- transfer ratio（large/small）= −1 / +9 = **−0.11**（负迁移）；
- 4/9 的 Wilson 95% CI ≈ **[19%, 73%]**——9 块大板无法支撑族级显著性；
- 聚合 88.1% 由 99 块小板支配，任何"平均提升"都可能是小板提升。


---

## 5. C3 执行进展（当日续）

### 5.1 发射前连通性证书（已生效）

`connect_unconnected_geomir` 在画铜之前做三件事：

1. **端点吸附**：plan 的首/末端点强制搬到两个 anchor 坐标；
2. **重验证**：吸附后每条同层线段再跑 `geomir.verify_polyline`，失败即不发射；
3. **连通证书**：全部添加后 `routegrid.pair_connected(board, {nc,a,b})`，
   不连通则逐项 `board.Remove` 回滚。

效果：

| 板 | 之前 | 现在 | 含义 |
|---|---|---|---|
| 0086 | 0/1 → 0/0 | 0/1 → **0/0** | 通用连接器首次真正闭合 |
| 0052 | 0/1 → **3/1**（加铜但未接通） | 0/1 → **0/1**（回滚） | 不再伪造进展 |
| 0077 | 0/1 → 0/1 | 0/1 → 0/1 | 两跳仍 unsupported |

这是"证书先于发射"的第一个可复用模板：**没有连通证书的铜不允许落板**。

### 5.2 `nudge_clearance`（新增算子）

按形态（clearance + 有 track uuid）把违规线段沿法向以 10/20/40/80/160/320µm
平移，用同一 `verify_polyline` 判定候选，取第一个合法位移；端点随动，
若断开由事务 DRC 拒绝提交（不会以开路换间距）。`parse_violations` 现在为
clearance/width/short 保留 items 与 track uuid。

首轮观测（run 目录旧板，非最终管线）：

| 板 | 修前 | 修后 | 算子 |
|---|---|---|---|
| 0520 | 50/0 | **2/0** | widen（nudge 无候选） |
| 0048 | 2/1 | 1/1 | widen + connect |
| 0054 | 8/1 | 4/1 | widen + plane_bridge(stub) + connect |

结论：清除了"宽度归一"一类残差，但 0048/0054 的开路属于**焊盘出口拓扑**
（pad_exit），nudge 与 connect 都不适用；需要下一算子。

### 5.3 端点三态（规划前解析）+ 真实过孔几何

5.1 的证书只保证"画了且连通"，不保证"规划选对层"。四块开路板的
`unconnected_items` 描述暴露了四个实现缺陷，均已修复：

| # | 缺陷 | 后果 | 修复 |
|---|---|---|---|
| 1 | `item_layer` 只认 `F.Cu`/`B.Cu`/`Top`，不认本地化 `(Bottom)`；`PTH` 被当成"无层" | 0048 跨层未识别；0052 的 PTH 锚点被过滤 | `item_layers`：Bottom/Top/PTH（PTH = 全铜层） |
| 2 | 跨层过孔放在**焊盘中心**（0048/0077） | 细间距/焊盘中心不可打过孔 | 规划前选层：公共层直连；无公共层则**过孔放走线端** |
| 3 | `geomir.via_ok(x,y,nc,segs,rects)` 误传单层列表 | 每次 `via_ok` 抛异常 → 候选全被静默拒绝 | 传完整 per-layer 列表 + `via_r=via_dia/2` |
| 4 | 过孔几何硬编码 0.6/0.3mm | 板最小 0.6858mm → 每次闭合都新增 `via_diameter` | 从 `.kicad_pro` 的 `via_diameter`/`via_drill` 与 `m_ViasMinSize` 取最大值 |

另修复：规划失败时**预先添加的过孔会泄漏**在板上（先改后验）；现在过孔在
plan 验证之后才发射，并计入 `created`，任何失败（含连通证书失败）整体回滚。

实测（run 目录板，同一事务）：

| 板 | 修前 | 修后 | 备注 |
|---|---|---|---|
| 0052 | 0/1 | **0/0** | PTH 锚点 + 公共层 B.Cu 规划 + 焊盘中心过孔 |
| 0077 | 0/1 | **0/0** | 过孔在走线端，F.Cu 到焊盘 |
| 0086 | 0/1 | **0/0** | 证书保持 |
| 0048 | 2/1 | **1/0** | 开路闭合；余 1 条 1.8µm 间距（修前已在） |
| 0054 | 8/1 | 4/1 | 同层走线近失，拥堵逃逸仍 unsupported |

**结论**：开路残差不是"路由器不会连"，而是**层/锚点语义**错误：
本地化层名、PTH 多层语义、过孔位置与几何。四类修复都是形态级的，
无板名/坐标。0054 代表下一类：端点附近被邻网铜封死时的**逃逸规划**
（direct_link ④ 的环形槽搜索），仍需实现。

### 5.4 0054：逃逸可行性证书（不是"再试一次"）

在 5.3 的连接器上又加了三层通用回退：

1. **角点局部搜索**：把被拒候选的内部顶点按 8 方向 × 0.02–0.3mm 抖动再验证；
2. **双过孔逃逸**：两端同层且走廊被封时，在两端 0.3–1.5mm 环上找 via_ok
   槽，焊盘层短逃逸 → 换层主路径 → 反向逃逸拼回；
3. **拼接处过孔否决**：执行器对每个 plan 内换层点做 `via_ok`（全层），
   不合法即整条回滚。

0054 仍然 `failed 1`。诊断（可复现的可行性证书）：

- 端点 A (148.5,107.05) 周围 **0/60** 个 via 槽合法（0.3–1.5mm × 12 方向，
  via 半径 0.3429）；端点 B 有 12/60；
- 直线/L/Z 全部被 **net 2** 的走线挡住（最近差 8µm），角点抖动无解；
- 因此：单层无通道、双层无从 A 端下行。这不是规划器参数问题，是
  **A 端被邻网铜封死**；需要 ① 该层迷宫式 A*（当前 candidate_polylines
  只有 2–3 段），或 ② rip-up net 2 后重布。

据此把 0054 标为 `INFEASIBLE(needs_ripup_or_maze)`，不再消耗搜索预算。
下一算子：`short_rip_reroute`（0496），它同时提供 ② 的通用机制。

### 5.5 `short_rip_reroute` 与残差分类的实测修正

新增算子：对 `shorting_items` 中带 track uuid 的违规，**只 rip 走线、不动焊盘**，
在 rip 后的铜图上用同一 geomir 规划器按原宽度重布同层替换路径；任一
线段验证失败就用原几何重建（算子内回滚），再交板级事务裁决。

旧 run 目录复测（nudge 已接入）：

| 板 | 修前（错误/开路） | 修后 | 主要类 |
|---|---|---|---|
| 0496 | 64 / 14 | 48 / 13 | clearance 53、lib_footprint 37、short 11、via_dangling 8 |
| 0571 | 3 / 0 | **1 / 0** | lib_footprint 77、silk 73，路由类仅 3 clearance |

两个分类修正：

1. **0496 不是"9 对短路"**：真实分布是 11 short + 14 open + 53 clearance，
   且 37 个 `lib_footprint_issues` 属封装库校验，不是布线缺陷——把后者
   计入"路由失败"会污染归因；
2. **0571 不是 pad-local 规则问题**：路由类只有 3 条 clearance，其余是
   丝印/封装库类。nudge 已把 3 条降到 1 条。

结论：后续验收必须**按违规类分轴**统计（routing vs library vs silk），
否则 100% 目标会被不可路由的类永久挡住。
