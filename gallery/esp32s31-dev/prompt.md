# 案例 1:ESP32-S31 开发板

## Prompt

> 只绘制一个树莓派Zero大小的esp32 s31开发板

## 效果展示

### 顶层装配视图(铜箔+丝印+阻焊+元件边框)

![顶层装配视图](render_assembly_top.svg)

### 底层装配视图

![底层装配视图](render_assembly_bottom.svg)

### 顶层铜箔(布线)

![顶层铜箔](render_copper_top.svg)

### 顶层丝印(元件标注)

![顶层丝印](render_silk_top.svg)

## 设计规格

| 参数 | 值 |
|---|---|
| 板尺寸 | 65 × 30 mm(树莓派Zero尺寸) |
| 层数 | 2层(F.Cu + B.Cu,双面GND铺铜) |
| 主控 | ESP32-S31-WROOM-3U(WiFi6 + BT5.4 + RISC-V双核320MHz) |
| USB | USB-C 16P(5V供电 + 原生USB数据 + CC下拉) |
| 电源 | AP2112K-3.3 LDO(SOT-23-5,600mA) |
| 按键 | RESET + BOOT(C&K KMR2) |
| LED | PWR(红) + USER(蓝,IO9) |
| 排针 | 2×14 GPIO + 1×4 烧录口 |

## 流水线验证结果

```
✅ spec_schema         7条需求结构合法
✅ requirement_coverage 全部映射设计元素
✅ bom_completeness    19行BOM完整
✅ erc_zero            KiCad ERC = 0 违例
✅ netlist_topology    4项连接断言通过
✅ drc_zero            KiCad DRC = 0 违例
✅ impedance_si        USB 90Ω差分对达标
✅ irdrop              3V3压降 ≤ 3%
✅ thermal             结温 28.3℃ ≤ 85℃
✅ emc_precompliance   PDN目标阻抗通过
✅ firmware_build      固件构建通过
✅ firmware_spi_loopback SPI回环通过
```

## 下载

- [完整PCB项目](esp32s31-dev-pcb.zip)(原理图 + 板文件 + Gerber + BOM,144KB)
