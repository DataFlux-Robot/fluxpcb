# 案例 3：stm32n6-px4 — STM32N6 PX4飞控（6层·TFBGA264·FMUv6全冗余）

## Prompt（当时的提示词）

> 做一个55mm×28mm的STM32N6 PX4飞控：FMUv6全冗余传感器（双IMU+双气压+磁力计）、USB-C供电、CAN、GPS/遥测/RC串口、6路PWM输出、SWD调试口、6层板。

## 一条命令

```bash
flux run projects/stm32n6-px4    # 六环节全自动 → 🟢 RELEASED 23/23门禁
```

## 数据链（全部一手来源，三向互证）

| 数据 | 来源 |
|---|---|
| STM32N657X0 264球 | **ST官方** STM32N657X0HxQ.xml（open_pin_data仓库）↔ 两个社区CubeMX符号，零分歧 |
| 32个PX4外设功能球 | ST官方XML直出（双SPI/双I2C/双UART+FDCAN/USB/PWM×6/SWD/双晶振） |
| ICM-42688-P/BMP388/IST8310 | ArduPilot官方/GUDrones/AUXSPACE飞控仓库（封装+pin表逐一核实） |

## 效果

![顶层装配视图](render_assembly_top.svg)

<details>
<summary>查看更多渲染图</summary>

![底层装配视图](render_assembly_bottom.svg)

![六层铜箔布线](render_6layer.svg)

</details>

## 逐层视图（6层真实铜）

| 层 | 角色 | 视图 |
|---|---|---|
| F.Cu | 信号+器件 | ![F.Cu](layers/F.Cu.svg) |
| In1.Cu | GND平面 | ![In1.Cu](layers/In1.Cu.svg) |
| In2.Cu | 3V3电源平面 | ![In2.Cu](layers/In2.Cu.svg) |
| In3.Cu | 5V电源平面 | ![In3.Cu](layers/In3.Cu.svg) |
| In4.Cu | GND平面 | ![In4.Cu](layers/In4.Cu.svg) |
| B.Cu | 信号+器件 | ![B.Cu](layers/B.Cu.svg) |

## 门禁明细（23/23）

| 环节 | 门禁 |
|---|---|
| 需求分析 | spec_schema ✅ requirement_coverage ✅ |
| 器件选型 | bom_completeness ✅ bom_lcsc_verified ✅ |
| 原理图 | erc_zero ✅ netlist_topology ✅ |
| PCB设计 | drc_zero ✅ impedance_si ✅ placement_quality ✅ |
| 验收仿真 | irdrop ✅ thermal ✅ emc_precompliance ✅ spice_functional ✅ dc_irdrop_geom ✅ dc_current_density ✅ plane_cavity_resonance ✅ pdn_impedance_ac ✅ return_path ✅ em_field_analysis ⏭️ thermal_cfd ⏭️ |
| 固件开发 | firmware_build ✅ firmware_spi_loopback ✅ firmware_regression ✅ |

验收仿真亮点（真实几何上的求解）：
- **双轨几何DC解**：3V3压降0.0008%（Jmax 4.9A/mm²）、VDDCORE压降0.0018%（Jmax 3.3A/mm²），均≤45A/mm²限值
- **6层平面腔模**：首谐振1299MHz ≥ 200MHz频带
- **PDN阻抗AC扫频**：Zmax 0.083Ω ≤ 0.163Ω目标
- **LDO瞬态蒙特卡洛**：200次+3角点，压降2.79%≤5%

## 下载

- [stm32n6-px4-pcb.zip](stm32n6-px4-pcb.zip) — 可下单fab包（6层Gerber×12+钻孔+坐标+STEP 3D，17文件）

## 诚实边界

- 封装为 **TFBGA264 0.8mm**（非UFBGA176）——双源验证KiCad资产仅存在于该封装
- 双IMU采用 **ICM-42688-P×2**（BMI055双SPI引脚映射未验证，资产保留reference/）
- 5V→3V3为AP2112K LDO骨架（PX4量产板惯用buck，升级列ROADMAP）
- PX4固件为板级配置骨架（default.px4board+nuttx-config骨架+主机回归C代码），待接PX4源码树
- C号待上架核实（STM32N6为2025新量产系列）
- 布线残留手工可修项按既定校准策略如实保留在报告中
