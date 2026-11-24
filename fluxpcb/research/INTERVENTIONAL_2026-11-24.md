# 并行多方案 + 1/2 反馈完善 3/4 — 2026-11-24

依据：TextGrad (arXiv 2406.07496)、HarnessPro (arXiv 2610.26770, pzs19/HarnessPro)、
Kaonai "Online Autoresearch"、以及"Interventional Distinguishability"：
并行多个尽可能不同的方案才能被证伪；一个条件无法区分，两个一维，三个多维。

---

## 0. 出发点：三轮"迭代提升"不成立

R1→R2→R3 的 101→106→109 来自一个 harness 缺陷（禁用某个算子会否决**整批**修复），
不是 1/2 在完善 3/4（见 `ROUNDS_20261124.md` 的更正）。本轮先让测量可信，
再让 1/2 在可信的测量上工作。

## 1. 三个来源各自可迁移的部分

| 来源 | 可迁移的核心 | 落到哪里 |
|---|---|---|
| TextGrad | 变量在计算图里；梯度 = LLM(变量当前值, 使用处轨迹, 下游批评)；每轮 ≤n 次 LLM 调用；验证集不升就不更新。诚实增益不大（GPQA 51→55；三个 prompt 任务里两个与 DSPy 持平） | 变量 = 类型化算子参数（21 个，带 schema）；损失 = 真实 DRC；提议必须编译进 schema、只改一个变量 |
| HarnessPro | 诊断轨迹 → 演进 → 在**合成测试**上评估 → 性能-成本 Pareto；规划器看到同类候选的 diff；10 任务 coreset 训练、其余验证、测试集对规划器隐藏 | 组件 1 的诊断与提议；组件 2 的封存对照与 Pareto；coreset/留出划分 |
| Online Autoresearch | 离线信号会误导归因 → 只用真实奖励；**每个实验只改一个主变量**；Memory 接口持久化；on-policy：训练分布 = 部署分布 | 奖励 = 免豁免 DRC；单变量候选；`memory.jsonl`（哈希链）；候选只在基础臂失败的板上评估 |
| Interventional Distinguishability | 并行、最大差异的方案才能被证伪 | 12 条因子臂 + 逐板可区分性矩阵 + 轴主效应 |

两者的张力（最大差异利于"发现哪里不同"，单变量利于"归因给谁"）：同一套因子实验的两个读法——
臂是最大差异的角点，轴主效应是单变量对照。

## 2. 实现

| 位置 | 职责 |
|---|---|
| `meta/arm.py` | `Arm`：规划模式 × loop 模式 × 路由设置 × 允许的算子 × 类型化参数，内容寻址 |
| `verifiers/params.py` | 21 个类型化变量与 schema（默认值 = 原手写常数） |
| `work/pcbworld/arms_run.py` | 并行评测器：每个 (板, 臂) 一个子进程；按 arm digest + 代码指纹判新鲜；免豁免评分；仪表检查 |
| `meta/distinguish.py` + `work/pcbworld/distinguish.py` | 噪声底、逐板可区分性、轴主效应、通道划分 |
| `meta/feedback.py` | `FailurePacket`：从逐轮算子轨迹读取证据 |
| `meta/diagnose.py` | 失败模式 → 责任变量（仪表有问题则拒绝诊断） |
| `meta/proposer.py` | TextGrad 一步：LLM 提议 + 规则回退，每条提议只改一个变量、必须通过 schema |
| `meta/memory.py` | 追加式哈希链记忆：约束、已验证结论、已试过的 delta |
| `meta/strategy_store.py` | 逐板选择器（只看布线前特征） |
| `meta/devloop.py` | 组件 1：diagnose → evolve → select（on-policy） |
| `meta/meta_loop.py` | 组件 2：`confirm` 门 + 对组件 1 的预算调整 |
| `work/pcbworld/campaign.py` | 战役驱动：gen0 → 多代 → 留出确认 + 对照 + AHR |
| `work/pcbworld/controls.py` | 封存的已知坏对照（焊盘移入邻网焊盘，带哈希） |
| `meta/codefp.py` | 代码指纹；"实验者模块"被排除，并由测试保证执行路径不导入它们 |

测试：`pytest fluxpcb/tests` → 102 passed。

## 3. 仪表发现（都是先被证据暴露、再修的）

| 缺陷 | 证据 | 后果 |
|---|---|---|
| policy 在 dispatch 之后整批否决 | R1 有 13 块板被否决 | 轮间差值被误读为迭代增益 |
| **重布阶段从未执行** | 子进程 `No module named 'fluxpcb'`，随后 `shutil` 未导入 | R1–R3 的 reroute 阶段全是 no-op，却被记成"回滚：无改进" |
| 预布被 strip 抹掉 | cert 报 29 条预布走线，strip 后为 0 | "plan 被消费"是假的 |
| 预布障碍图不刷新 | 0023 预布板 2 个短路 | 后画的网撞上前面的铜 |
| 过孔键名写错 | 读 `via_diameter`，表里是 `via_dia` → 回退 0.8mm | 生成 DSN 里不存在的焊盘栈，Freerouting 解析失败 |
| geomir 用焊盘-焊盘间距检查走线-焊盘 | 0.0375 vs 0.2mm | 算子放行的候选被 DRC 拒绝，整批回滚 |
| 动作出错被记成回滚 | 隔离重布启动失败 → "无改进" | 掩盖了第 2 条 |
| 预算超时被当成仪表故障 | 两块重板"no cert" | 会被当成无效证据 |
| 中途改代码使指纹分裂 | 46 个 cell 指纹不一致 | 被 `adopt-cert-fp` 认领/重跑；之后冻结被哈希的文件 |

关于 `strip` 的一个事实：它删除全部走线，所以原来的 `reroute_nets` 实际是整板重布；
真正的部分撕开需要"strip 只撕指定网络 + 其余铜锁定"（`(type fix)`，Freerouting 实测尊重）。

## 4. gen0：12 条因子臂 × 23 块板（+ A/A 重复 + 1 条单变量对照）

322 个 cell，仪表问题 0。coreset：9 块当前失败 + 7 块依赖修复才通过 + 7 块无需修复的护栏。

| 臂 | 干净 | 平均耗时 |
|---|---|---|
| gate+ripup / direct+ripup / preroute+batch | 16/23 | 71 / 82 / 78 s |
| direct/gate/preroute × batch/morph, preroute+ripup | 15/23 | 59–85 s |
| preroute+route_only | 8/23 | 52 s |
| direct/gate + route_only | 7/23 | 41–44 s |

- **A/A 噪声底：0/23**（同一臂跑两次没有板翻转）。
- 最优单臂 16，任一臂 oracle 17 → 逐板选择器余量 1。

轴主效应（逐板配对，只有输的数量不超过噪声底才算效应）：

| 轴 | 对比 | 赢 | 输 | 可区分 |
|---|---|---|---|---|
| 规划 | direct → gate | 0 | 0 | 否（这个 coreset 上没有容量不可行的板） |
| 规划 | direct → preroute | 5 | 4 | 否（真实干预，净效果约零） |
| loop | batch → ripup | 2 | 0 | **是**（0048 在两种规划下都赢） |
| loop | batch → morph | 0 | 1 | 否 |
| loop | batch → route_only | 0 | 24 | 否（修复显然有用） |
| 对照 | 严格焊盘间距 vs 宽松 | 宽松输 1 | | 支持 3 节里的手工修复 |

通道：6 块板没有任何臂能清干净（0051、0054、0400、0496、0520、0599）→ 需要新能力；
11 块可区分；6 块全臂通过。

**并行方案找到的东西**：
- `0571` 只有 `preroute+batch` 一条臂能清干净（12 选 1）；
- `0048` 只有带撕开重布的臂（或 preroute）能清干净；
两块板在 R1–R3 的手工流程里都是失败的。

已知缺陷（未修）：`preroute` 臂在 0496 上路由失败——同一项目两次导出 DSN，定义的过孔焊盘栈不同。

## 5. 组件 1/2 的战役

见 `arms/gen0/CAMPAIGN.md`（由 `campaign.py` 生成）。

## 6. 哪些是手工的、哪些是自主的

- **手工**：12 条臂的定义、参数 schema、上面第 3 节的全部修复、"失败模式 → 责任变量"的规则表。
- **自主**：在臂之间选择、提出单变量改动、在 on-policy 板上评估、拒绝/准入、以及最终是否承认。
- **未声称**：LLM 生成新算子代码；叠层/阻抗/摆放规划（本基准输入是已摆放的板）。
