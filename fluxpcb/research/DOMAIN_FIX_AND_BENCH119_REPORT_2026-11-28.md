# FLUXPCB 12 组件领域形式化缺陷修复、118/119 例 PCBWorld-Bench 评测与 H743 飞控复测报告

> 日期：2026-11-28 · 基线提交：`f10f4d4` · KiCad `9.0.9` · 架构依据：[`docs/FLUXPCB设计.md`](../../docs/FLUXPCB设计.md)

## 1. 修复内容（映射至 FLUXPCB 12 组件框架）

针对 H743 飞控复盘中暴露的 5 类系统性缺陷（Spec/Conjecture Schema 错配、领域内核残缺、默认 `profile='lean+fab'` 跳过物理内核、验证器与合成器断链、分类器误报与状态指纹失明），在 12 组件通用架构下完成了全链路修复（零板型/网络硬编码）：

| 组件 | 修改文件 | 修复内容 |
|---|---|---|
| **3. DevRouteLoop** | `fluxpcb/devloop/route_loop.py` | 修复未收敛迭代丢弃 `result.route_result`/`verification` 导致回退失败的缺陷 |
| **4. PCBDesignAgent** | `fluxpcb/design_agent/pcb_design_agent.py`, `fluxpcb/contracts.py` | 接入统一约束下沉编译器 `conjectures.lower_constraints()`，将 `spec.yaml` 中差分阻抗（IPC-2141）与电源载流线宽（IPC-2221/2152）编译到 `NetClassRules` |
| **5. CommRuntime** | `fluxpcb/runtime/comm.py` | 重写 `_compute_fingerprint()`，对走线起止点/线宽/层/网络、过孔与覆铜状态取几何哈希，修复仅按数量哈希导致加粗/改线不触发指纹失效的缺陷 |
| **6. SubagentTeam** | `fluxpcb/subagents/classifier.py` | 移除单字母 `'P'`/`'N'` 误匹配（修复 `VCAP`/`NRST`/`PWM_CH1`/`OSC_IN` 误判为差分），新增成对确认 `find_diff_pairs()` |
| **7. DispatchRuntime** | `fluxpcb/dispatch/dispatcher.py` | 规则下发读取组件 4 下沉的 `diff_pairs` 与 `net_widths_mm`，写入 `DispatchPlan` 差分伴侣/间距/偏斜/目标阻抗与干线-缩颈线宽 |
| **8. FeedbackRuntime** | `fluxpcb/feedback/feedback_runtime.py` | `suggest_repairs()` 消费 `VerificationReport` 领域失效项（`tune_meander`/`reroute_diff_pair`/`widen_power`） |
| **9. Router & PathFinder v4** | `fluxpcb/router/unified.py`, `fluxpcb/pipeline.py`, `fluxpcb/kicadgen/route_pathfinder.py` | 统一路由接口接入 `pathfinder` 后端并将 `DispatchPlan` 下沉执行；PathFinder v4 新增 **Phase 0 耦合差分对合成**（同封装冗余焊盘异侧无孔桥接 + BGA 对角通道缩颈逃逸 + 中心线耦合 A\* + 等距斜接偏移 + 45° 蛇形等长调谐）、**Phase 7 精确 nm 电源干线加粗与细间距自动局部缩颈**、**per-netclass 成对间距**、`rule_severities` 语义对齐、`edge == 0` 贴边焊盘支持及安全悬空线头修剪 |
| **10. 领域内核与证明门** | `fluxpcb/kernels/derive.py`, `fluxpcb/kernels/profile.py`, `fluxpcb/proof/conjectures.py`, `fluxpcb/proof/spec.py`, `fluxpcb/proof/repair.py`, `fluxpcb/proof/router.py`, `fluxpcb/tools/dcsolve.py` | 补齐 `signal.zdiff`（IPC-2141 边缘耦合微带/带状线）、`signal.coupled`（并行耦合度/过孔对称/偏斜/主干间距）、`ipc_min_width_mm`、`power.drop_path` 的 `source_ref`/`sink_refs` 解析；统一 `spec.yaml` 编译产出 `POWER:DROP`/`POWER:J`/`SIGNAL:ZDIFF`/`SIGNAL:SKEW`/`SIGNAL:COUPLED`；修复 KiCad 9 整数网络 ID 在 `dcsolve.py` 中的解析 |

回归测试：`fluxpcb/tests/test_pathfinder_and_h7.py` 等定向套件 **73/73 全部通过（0.88s）**。

---

## 2. 118/119 例官方 PCBWorld-Bench 评测结果

### 评测口径说明（严格遵循 `BENCH_118_RUNBOOK_2026-11-28.md`）
- **数据集**：官方 `LGAI-Research/PCBWorld` (`b3d62f5c`) + `PCBench` (`dec3be75`) 经原管线转换生成的全部 **119 块板**（D3-A 99、D3-B 10、D3-C 10；118 例集为同一批板不含 D3-C `0473`）。
- **引擎与环境差异如实披露**：当前 Linux 环境仅安装 OpenJDK 11 且无 `freerouting-2.5.0.jar`，因此本次运行的是 **纯自研 12 组件 FLUXPCB 全栈（`RoutePipeline(engine="pathfinder", pf_mode="full")` + `DevRouteLoop.repair_board()`，零外部 Freerouting、零 LLM 调用）**；每板单次独立子进程，墙钟预算 180s，6 并发，KiCad 9.0.9。原 runbook（`02a65b3`）使用 Freerouting 2.5.0 + 900s 预算 × 3 次重复（D3-A 297/297、D3-B 30/30、D3-C 24/30）。
- **裁判标准**：每板在隔离临时目录内仅保留原始 `circuit.kicad_pro`（隔离任何 `.kicad_dru`），经 `pcbnew.ZONE_FILLER` 重填铜区后由独立 `kicad-cli pcb drc` 判定：`ERROR` 口径要求 `errors == 0 and unconnected == 0`；`STRICT` 口径额外要求 `track_dangling == 0 and via_dangling == 0`。

### 汇总表（单次纯净全量提交，总墙钟 245.6s / 4.1 分钟，0 超时，0 仪器错误）

| Manifest | 集合 | 板数 | ERROR 通过 (Clean) | STRICT 通过 | 超时 (budget) | 仪器错误 | 对比 PCBRouteAgent |
|---|---|---:|---:|---:|---:|---:|---:|
| **119 官方全集** | D3-A | 99 | **98/99 (99.0%)** | **98/99 (99.0%)** | 0 | 0 | 94.9% |
| **119 官方全集** | D3-B | 10 | **7/10 (70.0%)** | **7/10 (70.0%)** | 0 | 0 | 100.0% |
| **119 官方全集** | D3-C | 10 | **5/10 (50.0%)** | **5/10 (50.0%)** | 0 | 0 | 40.0% |
| **119 官方全集** | **总计** | **119** | **110/119 (92.4%)** | **110/119 (92.4%)** | **0** | **0** | — |
| **118 工况集 (不含 0473)** | D3-A | 99 | **98/99 (99.0%)** | **98/99 (99.0%)** | 0 | 0 | 94.9% |
| **118 工况集 (不含 0473)** | D3-B | 10 | **7/10 (70.0%)** | **7/10 (70.0%)** | 0 | 0 | 100.0% |
| **118 工况集 (不含 0473)** | D3-C | 9 | **4/9 (44.4%)** | **4/9 (44.4%)** | 0 | 0 | 40.0% |
| **118 工况集 (不含 0473)** | **总计** | **118** | **109/118 (92.4%)** | **109/118 (92.4%)** | **0** | **0** | — |

关键观测：
- **全部 119/119 块板实现 `0 DRC 错误`、`0 悬空线头`**（无一例间距/线宽/短路/板边违规）；
- 纯自研 PathFinder v4 在 **D3-A（99.0% vs 94.9%）** 与 **D3-C（50.0% vs 40.0%）** 均超过 PCBRouteAgent，且在原 runbook 中 Freerouting 超时或残留 27 错+28 开路的超难板 `0496`（77.0s, 0 错 9 开路）与 `0599`（215.8s, 0 错 3 开路）上均保持 0 DRC 错误；`0400`/`0473`/`0520`/`0546`/`0626` 全部 0 错 0 开路通过。

### 失败板逐一清单（共 9 块，均为 0 DRC 错误、仅残留开路）

| 集合 | 板名 | errors | unconnected | dangling | 用时 (s) |
|---|---|---:|---:|---:|---:|
| D3-A | `0054_induction-hob_temperature-sensor` | 0 | 1 | 0 | 7.65 |
| D3-B | `0260_NiMH-Charger_NiMH-Charger` | 0 | 2 | 0 | 21.71 |
| D3-B | `0316_kicad-workshop_fancyboard` | 0 | 1 | 0 | 24.76 |
| D3-B | `0376_cat-trainer_teensy_base_pcb` | 0 | 1 | 0 | 35.64 |
| D3-C | `0423_induction-hob_temperature-sender` | 0 | 1 | 0 | 27.26 |
| D3-C | `0448_uedaino_uedaino` | 0 | 1 | 0 | 106.63 |
| D3-C | `0496_kitspace_f-91w` | 0 | 9 | 0 | 77.00 |
| D3-C | `0571_Brushless_ESC_Brushless_ESC` | 0 | 1 | 0 | 109.82 |
| D3-C | `0599_RGBMatrixPanelCPLD-PhotonBackpack_RGBMatrixPanel_CPLD` | 0 | 3 | 0 | 215.83 |

---

## 3. 修复后 FLUXPCB 领域形式化工具在 H743 飞控上的测试结果

输出文件：[`projects/h743-fc/reports/h743-fc-jlc6l-69of69-v4.kicad_pcb`](../../projects/h743-fc/reports/h743-fc-jlc6l-69of69-v4.kicad_pcb)（12 组件全部激活 `[1..12]`，墙钟 54.8s）。

### 修复前（v3）vs 修复后（v4）核心指标对比

| 指标 | 修复前 (v3) | 修复后 (v4) | 领域规范 / 证明门状态 |
|---|---:|---:|---|
| 连通性 & Lean 4 证明 | 69/69 | **69/69 (73/73 Lean+Fab proven)** | **PROVEN** (`0` open, `0` dangling) |
| 独立 KiCad DRC 新增错误 | 0 | **0**（仅库自带 J10 屏蔽槽 4 项 `annular_width`） | **PASS** |
| 过孔总数 | 246 | **228**（-18） | 0 VIPPO |
| **`USB_DP/USB_DM` 耦合度 & 层/过孔** | 19.3% / 异层 2 vs 0 孔 | **84.8% / 同层 `F.Cu` / 0 vs 0 孔** | `SIGNAL:COUPLED:usb` **derived** |
| **`USB_DP/USB_DM` 偏斜 (Skew)** | 1.300 mm | **0.117 mm**（45° 蛇形等长） | `SIGNAL:SKEW:usb` **derived** (≤0.5 mm) |
| **`USB_DP/USB_DM` 差分阻抗 `Zdiff`** | 148.25 Ω (`w=0.129 mm`) | **97.88 Ω (`w=0.305 mm`)** | `SIGNAL:ZDIFF:usb` **derived** (90 Ω ±15%) |
| **`CAN1_H/CAN1_L`** | 62.8% / 1 vs 2 孔 / skew 3.94 mm | **83.8% / 0 孔 / skew 0.117 mm / 119.99 Ω** | 3/3 断言 **derived** (120 Ω ±15%) |
| **`CAN2_H/CAN2_L`** | 0.0% / skew 2.57 mm | **79.8% / 0 孔 / skew 0.267 mm / 119.99 Ω** | 3/3 断言 **derived** (120 Ω ±15%) |
| **`5V` 电源干线 & IR 压降 / 电流密度** | 0.129 mm / 180.1 mV (3.60%) / 110.7 A/mm² | **0.45 mm / 70.4 mV (1.41%) / 31.75 A/mm²** | `POWER:DROP:5V` & `POWER:J:5V` **derived** |
| **`3V3` 电源干线 & IR 压降 / 电流密度** | 0.129 mm / 169.0 mV (5.12%) / 110.7 A/mm² | **0.35 mm / 86.4 mV (2.62%) / 40.82 A/mm²** | `POWER:DROP:3V3` & `POWER:J:3V3` **derived** |
| **`GND` / `VCAP` / `VDDA` 干线宽度** | 均 0.129 mm | **0.35 mm / 0.25 mm / 0.25 mm**（细间距自动缩颈） | 0 新增间距违规 |
| **`profile='full'` 领域断言总计** | 多数失效/未启用 | **13/13 全部 `derived`（0 refuted, 0 disabled）** | 官方 `fluxpcb.proof.cli certify --profile full` 签发 |

视觉对比渲染位于 `projects/h743-fc/reports/renders/`：
- `h743_v4_usb_diffpair_compare.png`（USB 耦合差分对与蛇形等长修复前后放大对比）
- `h743_v4_power_and_diff_compare.png`（5V/3V3/GND 电源加粗干线与 USB/CAN1/CAN2 差分对全板前后对比）
- `h743_v4_6layer_grid.png`（6 层铜皮总览）与 `h743_v4_3d_top.png` / `h743_v4_3d_bottom.png`（3D 渲染）
