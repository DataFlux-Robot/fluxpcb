# FLUXPCB 工程交接文档（HANDOFF）

> 更新日期：2026-11-28 · 分支：`main` · 上游架构基准：[`docs/FLUXPCB设计.md`](docs/FLUXPCB%E8%AE%BE%E8%AE%A1.md)  
> 关联详报：[`fluxpcb/research/DOMAIN_FIX_AND_BENCH119_REPORT_2026-11-28.md`](fluxpcb/research/DOMAIN_FIX_AND_BENCH119_REPORT_2026-11-28.md) · [`fluxpcb/research/BENCH_118_RUNBOOK_2026-11-28.md`](fluxpcb/research/BENCH_118_RUNBOOK_2026-11-28.md) · [`fluxpcb/research/H7_PATHFINDER_REPORT_2026-11-27.md`](fluxpcb/research/H7_PATHFINDER_REPORT_2026-11-27.md)

---

## 1. 一句话现状

FLUXPCB 12 组件领域形式化设计框架已打通 **“声明式规范（`spec.yaml`）→ 约束下沉（Component 4/7）→ 领域感知合成（Component 9 PathFinder v4）→ Lean 4 + Fab + 全物理领域内核认证（Component 10 `profile='full'`）”** 端到端闭环（全程通用算法、零板型/网络硬编码）：

1. **H743 6 层 BGA 飞控**（[`projects/h743-fc/reports/h743-fc-jlc6l-69of69-v4.kicad_pcb`](projects/h743-fc/reports/h743-fc-jlc6l-69of69-v4.kicad_pcb)）：
   - 12 组件全部激活 `[1..12]`，墙钟 **54.8 s**，**69/69 全连通（Lean 4 + Fab 73/73 proven）**，独立 KiCad DRC **0 新增错误、0 开路、0 悬空、0 VIPPO**；
   - **`profile='full'` 全部 13/13 物理领域断言 `derived`（0 refuted, 0 disabled）**：
     - `USB_DP/DM`：同层 `F.Cu`、**0 过孔**、耦合度 **84.8%**、45° 蛇形等长 **skew = 0.117 mm**、`w = 0.305 mm`、**`Zdiff = 97.88 Ω`**（目标 90 Ω ±15%）；
     - `CAN1_H/L` & `CAN2_H/L`：同层 `F.Cu`、**0 过孔**、耦合度 83.8%/79.8%、skew 0.117/0.267 mm、**`Zdiff = 119.99 Ω`**（目标 120 Ω ±15%）；
     - 电源干线：`5V = 0.45 mm`（IR 压降 **70.4 mV / 1.41%**，电流密度 **31.75 A/mm²**）、`3V3 = 0.35 mm`（IR 压降 **86.4 mV / 2.62%**，电流密度 **40.82 A/mm²**）、`GND = 0.35 mm`、`VCAP/VDDA = 0.25 mm`（细间距自动缩颈）。
2. **官方 PCBWorld-Bench 118/119 例全量评测**（纯自研 PathFinder v4 + `DevRouteLoop.repair_board()`，零外部 Freerouting、零 LLM）：
   - 全部 119 块板 **4.1 分钟（245.6 s）完成，0 超时、0 仪器错误**；
   - **119/119 块板全部 0 DRC 错误、0 悬空线头**；
   - **119 板全集：110/119（92.4%）** — **D3-A 98/99 (99.0%)**、D3-B 7/10 (70.0%)、**D3-C 5/10 (50.0%)**（118 集不含 `0473` 为 **109/118 = 92.4%**），ERROR 与 STRICT 口径完全一致。
3. **回归测试**：定向核心套件 **73/73 passed（0.88 s）**。

---

## 2. 本轮 12 组件架构改造文件地图

| 组件（对应 `docs/FLUXPCB设计.md`） | 关键文件 | 本轮改动说明 |
|---|---|---|
| **3. DevRouteLoop** | [`fluxpcb/devloop/route_loop.py`](fluxpcb/devloop/route_loop.py) | 修复 `run()` 在未收敛轮次丢弃 `result.route_result`/`verification` 导致外层触发无效回退的缺陷 |
| **4. PCBDesignAgent & 契约** | [`fluxpcb/design_agent/pcb_design_agent.py`](fluxpcb/design_agent/pcb_design_agent.py)<br>[`fluxpcb/contracts.py`](fluxpcb/contracts.py) | 接入 `conjectures.lower_constraints()`；`NetClassRules` 扩展 `diff_pairs`/`net_widths_mm`；`DispatchPlan` 扩展 `neck_width_mm`/`diff_partner`/`diff_gap_mm`/`max_skew_mm`/`target_z_ohm` |
| **5. CommRuntime** | [`fluxpcb/runtime/comm.py`](fluxpcb/runtime/comm.py) | `_compute_fingerprint()` 改为对走线几何（端点/线宽/层/网络）、过孔与覆铜状态哈希，修复仅按数量哈希对加粗/改线失明的问题 |
| **6. SubagentTeam** | [`fluxpcb/subagents/classifier.py`](fluxpcb/subagents/classifier.py) | 去除单字母 `'P'`/`'N'` 前缀误匹配（修复 `VCAP`/`NRST`/`PWM_CH1`/`OSC_IN` 误判差分），新增成对校验 `find_diff_pairs()` |
| **7. DispatchRuntime** | [`fluxpcb/dispatch/dispatcher.py`](fluxpcb/dispatch/dispatcher.py) | `_rule_dispatch()` 读取下沉后的 `diff_pairs` 与 `net_widths_mm`，生成带差分与干线-缩颈约束的 `DispatchPlan` |
| **8. FeedbackRuntime** | [`fluxpcb/feedback/feedback_runtime.py`](fluxpcb/feedback/feedback_runtime.py) | `suggest_repairs()` 消费 `VerificationReport` 领域失效并产出 `tune_meander`/`reroute_diff_pair`/`widen_power` 修复指令 |
| **9. Router & PathFinder v4** | [`fluxpcb/router/unified.py`](fluxpcb/router/unified.py)<br>[`fluxpcb/pipeline.py`](fluxpcb/pipeline.py)<br>[`fluxpcb/kicadgen/route_pathfinder.py`](fluxpcb/kicadgen/route_pathfinder.py) | `Router` 接入 `pathfinder` 后端并将 `DispatchPlan` 下沉执行；`pipeline.py` 移除 Windows 硬编码路径并自动发现同目录 `spec.yaml`；PathFinder v4 新增：<br>• **Phase 0 耦合差分对合成**（同封装冗余焊盘异侧无孔桥接、BGA 2/3 段对角通道逃逸、中心线耦合 A\*、等距斜接偏移、45° 蛇形等长调谐）<br>• **Phase 7 精确 nm 电源干线加粗 + 细间距自动局部缩颈**<br>• **`.kicad_pro` 全规则对齐**（per-netclass 成对间距、仅以 DRC 最小规则为线宽/过孔底线、+5 µm 圆角焊盘弦容差、`rule_severities` 对齐、`edge == 0` 贴边焊盘支持、安全悬空线头修剪） |
| **10. 领域内核与证明门** | [`fluxpcb/kernels/derive.py`](fluxpcb/kernels/derive.py)<br>[`fluxpcb/kernels/profile.py`](fluxpcb/kernels/profile.py)<br>[`fluxpcb/proof/conjectures.py`](fluxpcb/proof/conjectures.py)<br>[`fluxpcb/proof/spec.py`](fluxpcb/proof/spec.py)<br>[`fluxpcb/proof/repair.py`](fluxpcb/proof/repair.py)<br>[`fluxpcb/proof/router.py`](fluxpcb/proof/router.py)<br>[`fluxpcb/tools/dcsolve.py`](fluxpcb/tools/dcsolve.py) | 新增 `zdiff_edge_coupled`（IPC-2141 边缘耦合微带/带状线）、`diff_coupling`（主干并行耦合度/过孔对称/偏斜/中位间距）、`ipc_min_width_mm`；`power_drop_path` 支持 `source_ref`/`sink_refs`；`conjectures.from_spec` + `lower_constraints` 统一支持项目 `spec.yaml` Schema；修复 `dcsolve.py` 解析 KiCad 9 整数网络 ID |
| **测试与报告产物** | [`fluxpcb/tests/test_pathfinder_and_h7.py`](fluxpcb/tests/test_pathfinder_and_h7.py)<br>[`fluxpcb/research/DOMAIN_FIX_AND_BENCH119_REPORT_2026-11-28.md`](fluxpcb/research/DOMAIN_FIX_AND_BENCH119_REPORT_2026-11-28.md)<br>[`projects/h743-fc/reports/`](projects/h743-fc/reports/) | 扩展领域下沉、耦合内核、分类器无误报、状态指纹宽度敏感性与 per-netclass 精确间距单测；保存 H743 v4 板件与前后对比渲染图 |

---

## 3. 当前基线指标（全部可复现）

### 3.1 H743 飞控修复前后对比（v3 → v4）

| 指标 | 修复前 (v3) | 修复后 (v4) | 形式化状态 |
|---|---:|---:|---|
| 连通性 & Lean 4 证明 | 69/69 | **69/69（73/73 Lean+Fab proven）** | **PROVEN** |
| 独立 KiCad DRC 新增错误 | 0 | **0**（仅库自带 J10 屏蔽槽 4 项 `annular_width`） | **PASS** |
| 过孔数 | 246 | **228**（-18，0 VIPPO） | 合法 |
| `USB_DP/USB_DM` | 19.3% / 2 vs 0 孔 / skew 1.30 mm / 148.3 Ω | **84.8% / 0 孔 / skew 0.117 mm / 97.88 Ω (`w=0.305 mm`)** | `ZDIFF/SKEW/COUPLED` **3/3 derived** |
| `CAN1_H/CAN1_L` | 62.8% / 1 vs 2 孔 / skew 3.94 mm / 147.4 Ω | **83.8% / 0 孔 / skew 0.117 mm / 119.99 Ω** | **3/3 derived** |
| `CAN2_H/CAN2_L` | 0.0% / 0 孔 / skew 2.57 mm / 135.6 Ω | **79.8% / 0 孔 / skew 0.267 mm / 119.99 Ω** | **3/3 derived** |
| `5V` 干线 / IR 压降 / `J` | 0.129 mm / 180.1 mV (3.60%) / 110.7 A/mm² | **0.45 mm / 70.4 mV (1.41%) / 31.75 A/mm²** | `DROP` & `J` **derived** |
| `3V3` 干线 / IR 压降 / `J` | 0.129 mm / 169.0 mV (5.12%) / 110.7 A/mm² | **0.35 mm / 86.4 mV (2.62%) / 40.82 A/mm²** | `DROP` & `J` **derived** |
| `GND` / `VCAP` / `VDDA` | 均 0.129 mm | **0.35 / 0.25 / 0.25 mm**（细间距局部缩颈） | 0 新增 DRC |
| `profile='full'` 领域总计 | 多项未覆盖/失败 | **13/13 `derived`（0 refuted, 0 disabled）** | 官方 `certify --profile full` 签发 |

可视化对比图：
- USB 差分对与蛇形等长放大对比：[`projects/h743-fc/reports/renders/h743_v4_usb_diffpair_compare.png`](projects/h743-fc/reports/renders/h743_v4_usb_diffpair_compare.png)
- 电源干线加粗与差分对全板对比：[`projects/h743-fc/reports/renders/h743_v4_power_and_diff_compare.png`](projects/h743-fc/reports/renders/h743_v4_power_and_diff_compare.png)
- 6 层铜皮总览 & 3D：[`h743_v4_6layer_grid.png`](projects/h743-fc/reports/renders/h743_v4_6layer_grid.png) · [`h743_v4_3d_top.png`](projects/h743-fc/reports/renders/h743_v4_3d_top.png) · [`h743_v4_3d_bottom.png`](projects/h743-fc/reports/renders/h743_v4_3d_bottom.png)

### 3.2 官方 118/119 例 PCBWorld-Bench 评测（纯内置 PathFinder v4）

| 路线 / 环境 | Manifest | D3-A | D3-B | D3-C | 总计 | 备注 |
|---|---|---:|---:|---:|---:|---|
| **本次纯自研 PathFinder v4 + `repair_board`**（KiCad 9.0.9，180s×1，6 并发，4.1 min） | **119 全集** | **98/99 (99.0%)** | **7/10 (70.0%)** | **5/10 (50.0%)** | **110/119 (92.4%)** | **119/119 均 0 DRC 错误、0 悬空**，ERROR=STRICT，0 超时 |
| 同上 | **118 集**（不含 `0473`） | **98/99 (99.0%)** | **7/10 (70.0%)** | **4/9 (44.4%)** | **109/118 (92.4%)** | 同上 |
| 原 runbook Freerouting 2.5.0（`02a65b3`，KiCad 10，JDK 25，900s×3，71 min） | 119 全集 | 297/297 (100%) | 30/30 (100%) | 24/30 (80.0%) | 351/357 (98.3%) | 依赖外部 FR 2.5.0 JAR + JDK 25；`0496` 超时、`0599` 27错+28开路 |
| 论文基线 PCBRouteAgent | 119 | 94.9% | 100.0% | 40.0% | — | 单次，时间上限未披露 |

本次纯内置 PathFinder v4 的全部 9 块未通过板（均为 `errors=0, dangling=0`，仅余开路）：
- D3-A（1 块）：`0054` (u=1)
- D3-B（3 块）：`0260` (u=2)、`0316` (u=1)、`0376` (u=1)
- D3-C（5 块）：`0423` (u=1)、`0448` (u=1)、`0496` (u=9)、`0571` (u=1)、`0599` (u=3)

---

## 4. 一键复现与开发环境说明

### 4.1 环境前提
- Python：必须使用绑定 KiCad `pcbnew` 的解释器（Ubuntu/Linux 为 `/usr/bin/python3`，KiCad 9.0.9；Windows 为 KiCad 内置 Python）。
- Lean 4 证明内核：`lean/.lake/build/bin/flux-kernel`（如需重编：`cd lean && lake build`）。
- KiCad CLI 注意事项：**KiCad 9.0.9 的 `kicad-cli pcb drc` 不支持 `--refill-zones` 参数**（KiCad 10 才支持）；在 KiCad 9 上跑 DRC 前需先经 `pcbnew.ZONE_FILLER(b).Fill(b.Zones())` 重填铜区再保存（且保留原 `.kicad_pro` 字节不变，参见 `repair_drc.save_preserving_project`）。
- DeepSeek V4 Pro MAX 推理（可选 LLM 通道）：设置环境变量 `FLUXNT_LLM_PROVIDER=openai`、`FLUXNT_LLM_MODEL=deepseek-v4-pro`、`FLUXNT_LLM_BASEURL=https://api.deepseek.com/v1`、`FLUXNT_LLM_API_KEY_ENV=DEEPSEEK_API_KEY`、`FLUXNT_LLM_REASONING_EFFORT=max`、`DEEPSEEK_API_KEY=<你的key>`（**严禁将真实 key 写入仓库**）。

### 4.2 常用验证命令

```bash
# 1) 核心回归单测（73/73，~1s）
/usr/bin/python3 -m pytest -q \
  fluxpcb/tests/test_pathfinder_and_h7.py \
  fluxpcb/tests/test_proof_gate.py \
  fluxpcb/tests/test_repair_loop.py \
  fluxpcb/tests/test_kicad_text.py \
  tests/test_fc.py -k "not test_placement_validates_clean"

# 2) 用 12 组件全链路运行 H743 飞控（自动加载同目录 spec.yaml）
/usr/bin/python3 -c '
from fluxpcb.pipeline import RoutePipeline
pipe = RoutePipeline(config={"engine": "pathfinder", "pf_mode": "full", "pf_max_iters": 18, "allow_vippo": False, "max_iter": 1})
print(pipe.route("projects/h743-fc/reports/h743-fc-jlc6l-69of69-v4.kicad_pcb", timeout_s=180))
'

# 3) 官方 Lean 4 + Fab + 全物理领域内核认证（profile=full）
/usr/bin/python3 -m fluxpcb.proof.cli certify \
  --board projects/h743-fc/reports/h743-fc-jlc6l-69of69-v4.kicad_pcb \
  --spec projects/h743-fc/spec.yaml \
  --profile full \
  --out /tmp/h743_v4_cert.json

# 4) 独立 KiCad DRC 复核
kicad-cli pcb drc --format json --severity-error \
  -o /tmp/h743_v4_drc.json \
  projects/h743-fc/reports/h743-fc-jlc6l-69of69-v4.kicad_pcb
```

> 若在有 JDK 25 + `freerouting-2.5.0.jar` 的机器上复跑 Freerouting arm 118/119 例对照，请直接按 [`fluxpcb/research/BENCH_118_RUNBOOK_2026-11-28.md`](fluxpcb/research/BENCH_118_RUNBOOK_2026-11-28.md) 第 3 节执行（务必设 `FLUXPCB_JAVA_XMX=2g`）。

---

## 5. 已知边界与历史遗留（避免重复踩坑）

1. **USB-C `J10` 屏蔽槽封装问题**：`projects/h743-fc` 使用的 USB-C 封装 4 个 `SH` 槽孔焊盘其铣槽略大于焊盘包围盒，导致 KiCad DRC 固定报 4 项 `annular_width` 且 `kernels/fab.py` 报 `FAB:ANNULAR: unknown UNMODELLED`。这属于封装库本体，不是布线器引入的违规。
2. **内层完整铺铜平面尚未进形式化状态**：`fluxpcb/proof/state.py` 目前未提取 `zone` 覆铜多边形（带岛/反焊盘），因此当前 H743 v4 以加粗电源走线（0.45/0.35 mm）保证 100% Lean+Fab 可证；若进一步在 `In1.Cu = GND`、`In4.Cu = 3V3` 铺完整参考平面，需同步完成 `state.py` + Lean + `fab.py` 的 zone 几何抽取。
3. **`certify` 默认 profile 仍为 `lean+fab`**：为兼容历史测试，未改默认参数；实际工程/CI 传入 `--spec` 时建议默认启用 `--profile full`，防止物理领域内核被静默跳过。
4. **`--assembly` 对矩形 `Edge.Cuts` 的外框检查**：`h743_clean_jlc` 的 `Edge.Cuts` 为单个 `gr_rect`，`assembly` 校验对某些封装庭院包围盒有误报（`courtyard_overlap` 实为 0），待统一外框多边形提取。
5. **仓库历史既有 6 项无关单测失败**（合并本轮代码前 `main` 上已存在）：`test_geomir.py::test_legal_near_pair_not_counted`、`test_k13.py::test_unknown_family_fails`、`test_k14.py::test_pathfinder_core`、`test_kernel.py::test_connectivity_derivation_sites`、`test_knowhow.py::test_refine_*`、`test_fc.py::test_placement_validates_clean`、`test_contracts.py::test_cupstack_order_f_first_b_last`。

---

## 6. 下一阶段开发优先级建议

### P0（立刻见效）
1. **PathFinder v4 多随机种子 + `DevRouteLoop` 迭代重试**：本次 119 板中 `0316`/`0376`/`0423`/`0448`/`0571` 均仅余 `u = 1` 条开路（且 `0316`/`0423`/`0448` 在另一轮迭代中均已 0 错 0 开路通过）。在 [`fluxpcb/kicadgen/route_pathfinder.py`](fluxpcb/kicadgen/route_pathfinder.py) 末尾当 `0 < open_after <= 3` 且尚有预算时自动以不同 `rng` 种子/逆序网络重跑 1 轮（或在 `DevRouteLoop.run()` 启用 `max_iter=2` 局部重布），预期可将纯内置 PathFinder v4 推至 **D3-A 99/99、D3-B 9–10/10、D3-C 7–8/10（总计 ~97%）**，彻底摆脱对外部 Freerouting 的依赖。
2. **修复 `J10` USB-C 屏蔽槽封装 + `proof/cli.py` 传入 `--spec` 时自动切 `--profile full`**：使 H743 达到真正意义上的 `cert.ok == True`（0 unknown、0 DRC error）。

### P1（工程完整度与高速信号/电源质量跃升）
3. **Zone 铺铜几何进 `state.py` / `fab.py` / Lean 4**：提取铺铜多边形与反焊盘，支持 H743 `In1.Cu = GND` / `In4.Cu = 3V3` 实心参考平面形式化验证（同时把 `3V3` IR 压降从 86 mV 进一步压至 < 15 mV、让 `F.Cu` USB 90 Ω 差分对获得连续无割裂参考地平面）。
4. **差分对合成增强**：支持多跳拓扑（串阻/ESD 保护器件经由 `U1 → R_series / TVS → J10` 串联差分对）、支持对称换层过孔对（+ 伴随回流地孔 stitching vias）以及自动避让高速时钟（`SPI1_SCK`/`SDMMC1_CK` 3W 间距约束）。
5. **在 JDK 25 环境复跑 118/119 例 Freerouting arm 回归**：确认本次修改对 `gate_ripup~full4` 基线零退化。

### P2（制造与闭环自治）
6. **Component 8 反馈闭环接入真实 `profile='full'` 领域验证结果**：当前 `DevRouteLoop` 内的 `VerifierSuite` 与 `proof/gate.py` 领域内核并存，建议将 `kernels/profile.py` 接入 `VerifierSuite` 主通道，做到“验证器发现 `SIGNAL:SKEW` 或 `POWER:DROP` 违例 → `FeedbackRuntime` 生成定点修复参数 → `DispatchRuntime` 下发 → `Router` 局部重调谐”。
7. **Component 11 一键投板包（Gerber + Drill + BOM + CPL + JLC DFM 自检）**：将 H743 v4 生成完整嘉立创 6 层盘中孔/标准通孔工艺生产文件包并验证。
