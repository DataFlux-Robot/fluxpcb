# 四份参考的评估与采纳（2026-11-25）

对象：`multigram/hardware-design-to-order`、`donghuixin/low-power-iot-hardware-design`、
`C:\Users\zhang\Downloads\FLUXPCB设计.md`（我们的 12 组件北极星）、
`Open-Power-Electronics/OpenPE`。

结论：前两个是**技能/清单**（不含 EDA 引擎），OpenPE 是**同类桌面应用**（参考而非
依赖），设计文档是目标本身。对我们有增量的是四个被代码审计确认的缺口，本轮全部落地。

## 1. 定位

| 来源 | 是什么 | 许可 | 对我们的用法 |
|---|---|---|---|
| multigram/hardware-design-to-order | 纯 Markdown 的 agent skill（190 KB，`SKILL.md` 20 KB，"不内置通用 EDA 执行引擎"） | CC BY-NC-SA 4.0（**非商用**） | 只取**做法**，不搬文本 |
| donghuixin/low-power-iot-hardware-design | MIT 的 skill + 可运行脚本（`power_budget.py`、`hw_calc.py`、`fab_rules.py`、`fab-profiles/jlcpcb.json`、`make_demo_pcb.py`） | MIT | 公式与阈值可直接移植，保留出处 |
| FLUXPCB设计.md | 我们自己的 12 组件规划（175 行） | 自有 | 组件映射与验收 |
| OpenPE | Electron/Next.js 的 AI 电源电子工作台（v0.1.0 Beta，48 commits，18 stars）；自带 KiCad 10 + ngspice + Gmsh/Elmer | 代码 Apache-2.0；skill 内容 CC BY 4.0；KiCad 库 CC BY-SA | 取**独立公式与检查清单**；不依赖、不 vendor |

三处许可注意：multigram 是 NC；OpenPE 的 skill 文本是 CC BY 4.0（要署名）；
KiCad 库是 CC BY-SA（再分发要同许可）。代码里的引用已写在模块 docstring。

## 2. 采纳了什么（本轮）

### P1 功耗预算（lphd → 组件 10）
`tools/power_budget.py` + `gates/power.py`（`power_budget`）。公式来自
`references/06-power-budget.md`：`I_avg = I_sleep + Σ(I·t·n)/T`；`续航 = 容量×usable/I_avg`；
`V_min = V_eol − I_peak(R_batt+R_path)`；`C = I_step·dt/dV`。spec 输入在
`power.budget`（嵌套在既有 `power` 块里，不动 rails 契约）。无预算 → BLOCKED，
`flux.yaml` 里标 optional（USB 供电的板不因没有电池模型而失败）；一旦声明预算，
低于目标就是 FAIL（P1 `POWER.life`）。

### P2 冻结清单 + 装配集（multigram → 组件 11）
- `mfg/assembly.py`：BOM 行分成 smt/tht/manual/customer/dnp/unknown；CPL 只含 SMT；
  `panels × pieces_per_panel = finished`，`finished / pieces_per_set = sets`。未知类
  单独列出，不静默塞进 CPL。
- `mfg/release_manifest.py`：`fab/release_manifest.json`（schema `fluxpcb-release-1`）
  收拢源哈希、工具版本（kicad-cli/python/freerouting/ngspice）、每个文件的 sha256、
  gerber/drill/pos/bom/cpl/step 清单、`fab_package.json` 里的豁免与 truthnet、未决项。
- `engine._export_fab` 现在导出 bom.csv/cpl.csv/board.step 并盖 manifest；
  `RunReport.release_check()` 增加 `manifest_ok`：**缺清单不算发布**。

### P3 放置/EMC 检查 + 部分电感（lphd VF + OpenPE → 组件 10）
- `kicadgen/placement_checks.py`：去耦电容到"它服务的引脚"≤3 mm、布线长度
  ≤1.3×焊盘 MST+2 mm、C_in→IC→C_out 环路 ≤20 mm²、信号过孔到最近地过孔 ≤3 mm。
  四项**全部 advisory P2**，接入 `placement_quality` 但只在
  `placement_strict` 时升 P1；原有"重叠/连接器"仍是阻断项。0376 实测：C1 6.4 mm、
  C3 3.5 mm、P_SERVO1 走线 32.9 vs 32.4、U2 环路 23.6 mm²，四条建议，PASS/FAIL 不变。
- `tools/copper_graph.py`：`segment_inductance_nh`（OpenPE 自项公式，
  `0.1·2(L·asinh(L/r) − L²/(hypot(L,r)+r))`，r=线宽/2）+ `route_inductance_nh`
  （各段自项 + 0.4 nH/mm 每过孔）。审计每轨输出 `l_nh` 与 `PHYS.route_inductance`
  （estimate）：0376 上 +9V 10.7 nH、+5V 64.0、+3.3V 72.5、GND 108.2 nH。

### P4 工艺包补齐（组件 11）
- 拼板**真的生成 `.kicad_pcb`**：`panelization._items/_dup/_clear/_frame` +
  `generate_kicad_panel`（4 份拷贝 + 边框 + 3 fiducial + 4 NPTH 工艺孔，zone 重填）。
  0023 上 2×2 拼板产出 54 KB 文件，重载 16 footprints/24 tracks。`inspector._panelize`
  调用它（`panel_copies>1` 才做，单up 不算拼板）。
- STEP 进包；IPC-7351 `mfg/ipc7351.py` 对 0201/0402/0603/0805/1206/1210/SOT-23/
  SOT-223/SOIC/QFN 比 nominal density B（容差 25%），未知封装计入 skipped；
  `fabrication_netlist` 接进 `flux.yaml` 的 `pcb_layout`（truthnet 已存在）。

## 3. 没采纳什么

- OpenPE 的 Electron 壳、它的 DRC（没有电流密度/载流）、它的 SPICE（只支持 buck）、
  Elmer 热（太重）——我们已有更轻的等价物。
- multigram 的**文本**（NC 许可）与"不内置引擎"的定位——我们要的是引擎。
- lphd 的续航只在有电池输入时才有意义：USB 供电项目保持 BLOCKED→SKIPPED，
  不假装知道。

## 4. 验证

- 新增 `tests/test_power_budget.py`（7）、`test_release_manifest.py`（5）、
  `test_placement_checks.py`（8）、`test_manufacturing_package.py`（4）。
- 冒烟：无预算→BLOCKED；预算不达标→FAIL（P1 POWER.life 16.7 vs 30 天）；
  0376 的 placement 四条建议 + 2 条既有重叠（后者仍阻断）；0376 审计出现 `l_nh`。
- 拼板文件经 pcbnew 重载验证；STEP 在 `_export_fab` 里（无 kicad-cli 时记 warning）。
- 基准路径不动（`run_bench` 不引用 gates/dsolve）；现有 28 门口径只在
  `placement_quality` 增加 advisory、在 `release_check` 增加清单项。

## 5. 边界

- 功耗预算：声明的电池/占空比/末期电压，无温度与老化模型；输出 estimate。
- 电感：仅自项，无互感、无回流路径电感（回路由 `RETURN.*` 另报）。
- IPC-7351 只覆盖常见封装；拼板只做矩形阵列、拷贝间**参考标号重复**（生产拼板
  需改名，摘要里写明 `refs_repeat`）。
- 部分电感与 VF 阈值来自参考项目，出处写在模块 docstring 与规则索引的 `source`。

## 6. 有效性验证：118 个任务的随机 3 块（2026-11-25）

从基准的 118 任务清单（`autorouter-bench/pcbworld/manifests/boards_full.json`，
无 0473）用种子 **20261125** 抽 3 块：

| 板 | 集合 | 规模 | 为什么有意义 |
|---|---|---|---|
| 0376_cat-trainer_teensy_base_pcb | D3-B | 26 网 / 98 焊盘，全 THT | 中板；SMT 为 0，正好测 CPL 空集 |
| 0626_TOBS_HybridChargeController | D3-C | **91 网 / 358 焊盘**，89 SMD | 最密板；CPL/IPC/拼板的压力测试 |
| 0002_rufs_simple_kicad_schema_and_pcb_v1 | D3-A | 3 网 / 6 焊盘 | 最小板；全 THT、无已知封装 |

做法：为每块板合成一个最小项目（`pcb/<board>.kicad_pcb` + `.kicad_pro`、
从焊盘钻径生成的 `bom.json`、固定测试预算的 `spec.yaml`），跑四条新链。

| 板 | 导出 | 清单 | CPL | 功耗 | 放置 | 拼板 | IPC-7351 | 用时 |
|---|---|---|---|---|---|---|---|---|
| 0376 | ok，17 文件，0 warning | 18 件，kicad-cli 10.0.6 | 0（24 THT + 4 DNP） | PASS 33.3/30 天 | FAIL（2 阻断=既有重叠；4 建议） | 4-up，重载 60 fp | 0 检 / 28 跳过 | 3.6 s |
| 0626 | ok，17 文件，0 warning | 18 件 | **89**（89 SMT，14 THT 不入） | PASS | FAIL（12 阻断=既有重叠/连接器；**18 建议**） | 4-up，重载 412 fp | 11 检 / **0** 超差 / 92 跳过 | 3.6 s |
| 0002 | ok，17 文件，0 warning | 18 件 | 0（3 THT） | PASS | PASS（0/0） | 4-up，重载 16 fp | 0 检 / 3 跳过 | 2.5 s |

四条链在三块板上都成立：导出含 `bom.csv/cpl.csv/positions.csv/board.step` 与
`release_manifest.json`；CPL 只含 SMT（0376/0002 正确为空）；拼板都是 4 份拷贝；
功耗门在固定测试预算下给出同一答案（33.3 天）；放置的新检查只在有几何可查时出建议
（0376 去耦 6.4/3.5 mm、环路 23.6 mm²；0626 参考过孔 49/54 个远于 3 mm），且**不改变
阻断判定**（阻断仍来自既有的封装重叠/连接器规则）。

### 验证抓到并修掉的两个缺陷

1. **拼板装箱把间隙算了两遍**：`_guillotine_pack` 在已偏移一个 gap 的自由矩形上又要求
   `w+gap <= rw`，导致 87×87 mm 的 0626 在 2×2 面板里只放 1 块、0376 只放 2 块。改为
   `w <= rw and h <= rh`（间隙由偏移保证）后，三块都 4-up。
2. **IPC-7351 把每个库封装都判超差**：① 用单一标称值而不是 IPC 的 A–C 区间，SOIC-8-N
   的 1.14 mm 焊盘对 1.55 mm 标称永远 −26%；② 焊盘长/宽没按长边/短边排序，旋转封装
   被读反（0.508×1.143 当成"长 0.508"）；③ 手焊封装（`*_HandSoldering`）的焊盘本就更
   大。改为**区间 + 长/短边排序 + 手焊跳过**后，0626 从 78/78 全红变成 11 检 0 超差。
   两处另有 pcbnew 返回 `UTF8` 而非 `str` 的强制转换（`ipc7351.check_board`、
   `copper_graph.extract` 的 pad value），也一并修掉。

复现：`z_lab/fp/verify_pkg_3.py`（仓库外），结果 `z_lab/fp/pkg_verify_3/results.json`。
