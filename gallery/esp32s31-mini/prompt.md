# 案例 2：esp32s31-mini — 一句话需求 → 自动布局+自动布线 → 16/16门禁RELEASED

## Prompt（当时的提示词）

> ESP32-S31迷你开发板：USB-C供电 + 双排针（2×8P 2.54mm）+ 1个RESET按键 + 1个电源LED，板子尽量小（约52×30mm），2层板。

## 一条命令

```bash
flux run projects/esp32s31-mini    # 6环节全自动, 16门禁, 🟢 RELEASED
```

## 效果

| 输出 | 内容 |
|---|---|
| 原理图 | `.kicad_sch`，ERC 零违例（未用GPIO显式no-connect） |
| PCB | 52×30mm 2层板，**规则式自动布局**（功能分区+1.27网格+去耦就近）+ **流水线内自动布线**（Freerouting批量 + 碰撞感知L形收尾 + 违例清洗），DRC零违例 |
| 验收仿真 | IR-drop / 热 / PDN / **ngspice LDO瞬态蒙特卡洛(20次)** 全绿 |
| 固件 | ESP-IDF工程 + 纯逻辑共享源码**主机真实编译执行回归** |
| BOM | 17个真实LCSC C号（partslib快照核实，2项新料如实标注人工核实） |
| fab包 | Gerber×8 + 钻孔 + 坐标 + **STEP 3D** |

![装配图-顶面](render_assembly_top.svg)

## 门禁明细（16/16）

| 环节 | 门禁 |
|---|---|
| 需求分析 | spec_schema ✅ requirement_coverage ✅ |
| 器件选型 | bom_completeness ✅ bom_lcsc_verified ✅ |
| 原理图 | erc_zero ✅ netlist_topology ✅ |
| PCB设计 | drc_zero ✅ impedance_si ✅ placement_quality ✅ |
| 验收仿真 | irdrop ✅ thermal ✅ emc_precompliance ✅ spice_functional ✅ |
| 固件开发 | firmware_build ✅ firmware_spi_loopback ✅ firmware_regression ✅ |

## 下载

- [esp32s31-mini-pcb.zip](esp32s31-mini-pcb.zip) — 可直接下单的fab包（Gerber+钻孔+坐标+STEP）

## 诚实边界

- 模块与蓝色LED为2025新料，LCSC编号未公开索引——BOM如实标注"下单前人工核实"
- Freerouting布线后残留的手工可修项按校准策略（unconnected=ignore）如实保留在报告中
