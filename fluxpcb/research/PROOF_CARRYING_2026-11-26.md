# 证明携带的 PCB 设计：K1 最小闭环（2026-11-26）

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
