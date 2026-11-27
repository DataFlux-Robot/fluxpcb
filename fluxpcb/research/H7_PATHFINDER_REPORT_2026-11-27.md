# H743-FC 性能诊断与 6 层 PathFinder 改造报告（2026-11-27）

衔接 [HANDOFF_H7_2026-11-27.md](./HANDOFF_H7_2026-11-27.md)，针对 `projects/h743-fc`（STM32H743XI TFBGA240，50×50 mm 6 层飞控）此前布通率低、Freerouting 耗时 ~37.6 分钟且在全拆重布时崩溃的问题，完成根因定位、算法与生成器升级、DeepSeek V4 Pro `MAX` 推理集成及三重验证（Lean 4 `flux-kernel` + 精确整数 `fab.py` + `kicad-cli pcb drc`）。

---

## 1. 四个核心根因

1. **0.80 mm BGA × 0.150 mm 冻结规则在几何上无非 VIPPO 解**
   - `U1` 焊盘 `0.40 mm`、球距 `0.80 mm`；旧规则 `clearance=0.150 mm, track_min=0.127 mm, drill_min=0.250 mm, via_min=0.500 mm`。
   - 球间走线余量 `(0.80 - 0.40 - 0.127) / 2 = 0.1365 mm < 0.150 mm`（0 条通道）；4 球中心狗骨过孔余量 `0.5657 - 0.20 - 0.25 = 0.1157 mm < 0.150 mm`（0 个合法过孔位）。
   - 改为嘉立创 6 层免费标准工艺（`clearance=0.127 mm, track_min=0.127 mm, via=0.40/0.20 mm, annular=0.100 mm`）后，球间余量 `0.1365 >= 0.127 mm`、狗骨过孔余量 `0.1657 >= 0.127 mm`，**无需 VIPPO 即可 100% 逃逸**。
2. **字母序引脚分配造成 BGA 右上 5×7 死锁**
   - 原 `mcu_db.allocate()` 按字典序分配，约 40/59 个信号挤在 `r00–r04` 右上角。
   - 新增 `mcu_db.allocate_spatial()`（SPFA 最小费用最大流二分匹配）：57/57 全分配无冲突，Ring-0 外圈信号 `14 → 19`，总连线距离缩短 `11.4%`（`1239.9 → 1098.6 mm`），四面逃逸均衡（N:17 / E:12 / W:13 / S:16），仅需 12 颗信号过孔即可 58/58 全部非 VIPPO 逃逸。
3. **1.27 mm 排针 6 层隔墙与 0.50 mm LGA 逃逸盲区**
   - `J1–J9, J12–J15` 排针焊盘间距 `0.270 mm` 贯穿全 6 层（全层不可穿线）；`U2–U6, J10` 细间距 LGA 需连续 0.6–2.0 mm 外向逃逸射线才能跨过相邻焊盘膨胀区。
4. **生成器与布局实 Bug**
   - `pcb.py`：复用 `.kicad_mod` 产生重复 pad UUID（`U4/U5` 10 个重复）；未替换旧式 `fp_text` 导致 `U3` 变成 `REF**`；烘焙 `rot=90` 未同步旋转 `F.CrtYd` 图形导致 `J10/J12/J13/J14` Courtyard 错位碰撞；`_zone()` 坐标 typo `(xy w -0.2)`；`stitch_vias()` 写出字符串 net 名而非整型 net id。
   - `fcfull.py`：原布局存在 14 处 Courtyard 重叠与 7 处越界；`BMP388.kicad_mod` 缺失 `F.CrtYd`。
   - `proof/step.py`：`audit()` 漏 `process/robust` 参数；`routing.py` Linux 下未探测 `/usr/bin/python3` 的 `pcbnew`。

---

## 2. 三重验证结果对比

所有候选板均经 **Lean 4 `flux-kernel`** + **精确整数 `fab.py`** + **独立 `kicad-cli pcb drc`** 验证（原板 USB-C `J10` 4 处铣槽焊盘的既有 `annular_width` 报错未做任何放宽）：

| 方案 / 场景 | 规则（clearance / track / via） | 耗时 | 开路网（前→后） | 缺失焊盘（前→后） | Lean 4 证明 | Fab 核（CLR/W/DRL/EDGE） | KiCad DRC 新增违规 |
|---|---|---:|---:|---:|---:|---|---:|
| **基线：Freerouting Z1**（HANDOFF） | 0.150 / 0.127 / 0.50-0.25 | ~37.6 min | 45 | 53 | — | — | — |
| **原版 6 铺铜板** `proof.cli repair --drc-guard`（`pathfinder_keep`） | 0.150 / 0.127 / 0.50-0.25 | 8.6 min（2轮含DRC） | **44 → 16** | **222 → 21** (-90.5%) | 55 proven / 0 refuted | 全通过 | **0 新增** |
| **原版无铺铜板** `pathfinder_keep` | 0.150 / 0.127 / 0.50-0.25 | **29.7 s** | **44 → 16** | **227 → 22** (-90.3%) | 53/69 / 0 refuted | 全通过 | **0 新增** |
| **原版无铺铜板** `pathfinder_full`（原布局/原引脚/原规则） | 0.150 / 0.127 / 0.50-0.25（VIPPO） | **78.2 s** | **68 → 5** | **227 → 7** (-96.9%) | **64/69** / 0 refuted | 全通过 | **0 新增** |
| **原版 `h7_relax`** `pathfinder_full` | 0.090 / 0.150 / 0.50-0.25 | **79.4 s** | **68 → 13**（FR 为 14） | 20 | 56/69 / 0 refuted | 全通过 | **0 新增** |
| **升级版 H743（空间引脚 + 零重叠布局 + JLC 6L 标准工艺，0 VIPPO）** | **0.127 / 0.127 / 0.40-0.20（`allow_vippo=False`）** | **53.9 s** | **68 → 0** | **227 → 0** (-100%) | **69/69 (100%)** / 0 refuted | **全通过** | **0 新增**（0 重叠、0 越界） |

已保存完整 69/69 通板文件用于 KiCad 复核：`projects/h743-fc/reports/h743-fc-jlc6l-69of69.kicad_pcb`。

---

## 3. 渲染图集（69/69 全连通 JLC 6L 非 VIPPO 实产板）

### 3D 顶面 / 底面渲染

| 顶面（Top） | 底面（Bottom） |
|---|---|
| ![3D Top](../../projects/h743-fc/reports/renders/h743_3d_top.png) | ![3D Bottom](../../projects/h743-fc/reports/renders/h743_3d_bottom.png) |

### 优化前后 6 层全览对比（Before vs After）

![Before vs After](../../projects/h743-fc/reports/renders/h743_before_vs_after.png)

### 六层铜皮 2×3 总览与逐层统计

| 层序号 | KiCad 层名 | 线段数 | 总走线长度 (mm) |
|---|---|---:|---:|
| Layer 1 | `F.Cu` | 967 | 1009.4 |
| Layer 2 | `In1.Cu` | 267 | 376.0 |
| Layer 3 | `In2.Cu` | 184 | 355.3 |
| Layer 4 | `In3.Cu` | 133 | 256.0 |
| Layer 5 | `In4.Cu` | 76 | 156.5 |
| Layer 6 | `B.Cu` | 88 | 172.3 |
| **合计** | **6 层全板** | **1,715** | **2,325.5（211 颗 0.40/0.20 mm 非 VIPPO 通孔）** |

![6-Layer Grid](../../projects/h743-fc/reports/renders/h743_6layer_grid.png)

### 逐层高清大图（L1–L6 + 全层合成 + BGA 狗骨特写）

| L1 `F.Cu` | L2 `In1.Cu` |
|---|---|
| ![L1 F.Cu](../../projects/h743-fc/reports/renders/h743_L1_FCu.png) | ![L2 In1.Cu](../../projects/h743-fc/reports/renders/h743_L2_In1Cu.png) |

| L3 `In2.Cu` | L4 `In3.Cu` |
|---|---|
| ![L3 In2.Cu](../../projects/h743-fc/reports/renders/h743_L3_In2Cu.png) | ![L4 In3.Cu](../../projects/h743-fc/reports/renders/h743_L4_In3Cu.png) |

| L5 `In4.Cu` | L6 `B.Cu` |
|---|---|
| ![L5 In4.Cu](../../projects/h743-fc/reports/renders/h743_L5_In4Cu.png) | ![L6 B.Cu](../../projects/h743-fc/reports/renders/h743_L6_BCu.png) |

| 6 层全层合成 | `U1` BGA 0.80 mm 非 VIPPO 狗骨逃逸特写 |
|---|---|
| ![All 6 Layers](../../projects/h743-fc/reports/renders/h743_all_6layers.png) | ![BGA Zoom](../../projects/h743-fc/reports/renders/h743_bga_zoom.png) |

---

## 4. 代码改动清单

- `fluxpcb/kicadgen/route_pathfinder.py`：6 层 Fanout-Aware PathFinder 精确布线器（信号/电源分离、ExactBoard 精确纳米验证、局部阻挡网原子驱逐重布、单层口袋 BFS 逃逸过孔救援、Phase 6 精确物理光栅化终轮清扫）。
- `fluxpcb/kicadgen/mcu_db.py`：新增 `allocate_spatial()`（SPFA MCMF 空间引脚分配）与 `ball_positions()`，保留原有默认签名向后兼容。
- `fluxpcb/kicadgen/fcfull.py`：零重叠洁净布局、接入空间引脚分配、`JLC_6L_BGA_RULES` 常量与跨平台 KiCad 封装探测。
- `fluxpcb/kicadgen/pcb.py` & `reference/fculib/BMP388.kicad_mod`：修复 UUID/tstamp 再生、旧式 `fp_text` 替换、旋转同步旋转 Courtyard/丝印、zone 内缩、整型过孔网络号及 BMP388 Courtyard。
- `fluxpcb/proof/{router,repair_loop,cli,step}.py` & `fluxpcb/routing.py`：接入 `pathfinder_keep`/`pathfinder`/`pathfinder_full` 策略，修复 `step.audit()` 签名与 Linux `pcbnew` 探测。
- `fluxpcb/llm.py`：新增 `FLUXNT_LLM_REASONING_EFFORT`（支持 `deepseek-v4-pro` 的 `max` 推理强度 + 自动 SSE 流式读取与 `reasoning_content` 透传）。
- `fluxpcb/tests/test_pathfinder_and_h7.py`：覆盖 BGA 几何可行性、路由策略接线、MCMF 空间引脚、生成器修复及 DeepSeek `MAX` 推理 SSE 解析的回归测试（定向套件 70/70 全通过）。
