# 案例 5：h743-fc — Pixhawk级全外设H743飞控（50×50·6层·真连通）

## Prompt（当时的提示词）

> 彻底完善fluxpcb，修复这个bug，然后修改方案引入：包含 1,8个pwm输出 2,8个UART输出 3,一个I2C输出 4,5个GND输出 5,包含BMI088,ICM45686 6,tf卡操作 7,2个CAN 8,2个ADC 9,一个typec 10,1个SWD debug

## 一条命令

```bash
flux run projects/h743-fc    # → 见reports/latest.md（routing_completeness为硬门禁）
```

## 本案例与之前四板的本质区别

前四板的"RELEASED"建立在 `unconnected_items=ignore` 标定之上——DRC报告0未连接，快路径跳过布线，
实际ratsnest开路45-82条。本轮：

1. **routing_completeness门禁生效**（修复了指向不存在路径的bug），直读pcbnew大鼠nest，无法被标定稀释；
2. **布线链以ratsnest为唯一真值**：conn_audit岛检测（位置键并查集绕开SWIG包装器id不稳定）+ Prim链
   = 实测 derived_pairs 与 ratsnest 精确相等；
3. **功率缝合孔**：F.Cu功率焊盘距内层平面只差一个过孔（3V3曾碎成25岛）；
4. **隔层逃逸**：BGA球心过孔合法（0.8-0.2=0.6≥0.55）、LGA细距焊盘必须偏移过孔、THT免过孔——
   每端点独立选锚；
5. **修复两个静默杀手**：cleanup 2.5mm清除半径（越修越差）与collect_obstacles无层过滤
   （F焊盘阻挡B层布线，所有隔层策略静默失败）。

## 数据链

| 数据 | 来源 |
|---|---|
| STM32H743XI TFBGA240（265球@0.8mm） | **ST官方** STM32H743XIHx.xml |
| 56个外设功能球（8PWM+8UART+SDMMC1+双CAN+双ADC+SPI×2+I2C×2+USB/SWD/OSC） | 深度2驱逐式分配器（UART5_TX的PB6被I2C1_SCL占用→其D5被TIM4_CH3占用→TIM4_CH3另有空球） |
| BMI088 pin表 | ArduPilot NxtPX4符号（SCK=8/SDI=9/CSB1=14/SDO1=15，PS→GND=SPI模式） |
| ICM-45686 | TDK同族14-LGA，与ICM-42688-P引脚兼容 |
| 3x08三排PWM排针 | 生成封装（KiCad库无3xNN） |

## 外设清单（10项全覆盖）

| 需求 | 落地 |
|---|---|
| 8×PWM | TIM1_CH1-4 + TIM4_CH1-4 → J11 3x08 2.54三排（PWM/GND/5V） |
| 8×UART | USART1/2/3/6+UART4/5/7/8 → J1-J8（GPS/TELEM1/TELEM2/RC等） |
| I2C | I2C1（双BMP388地址错开）+I2C2（IST8310+GPS口直通） |
| 5×GND | J15专用GND排针 |
| 双IMU | BMI088（SPI1，双CS：CSB1=accel CSB2=IMU1_CS2）+ ICM-45686（SPI2） |
| TF卡 | SDMMC1 4-bit → J17 microSD推推座 |
| 2×CAN | FDCAN1/FDCAN2 + SN65HVD230×2 → J12/J13 |
| 2×ADC | ADC1_INP10/INP11 → J14 |
| USB-C | OTG_FS → J10 |
| SWD | 2x05 1.27 → J9 |

## 逐层视图（6层真实铜）

| 层 | 角色 | 视图 |
|---|---|---|
| F.Cu | 信号+器件 | ![F.Cu](layers/F.Cu.svg) |
| In1.Cu | GND平面 | ![In1.Cu](layers/In1.Cu.svg) |
| In2.Cu | 3V3电源平面 | ![In2.Cu](layers/In2.Cu.svg) |
| In3.Cu | 5V电源平面 | ![In3.Cu](layers/In3.Cu.svg) |
| In4.Cu | GND平面 | ![In4.Cu](layers/In4.Cu.svg) |
| B.Cu | 信号+器件 | ![B.Cu](layers/B.Cu.svg) |

## 门禁明细（诚实报告：23/25）

| 门禁 | 状态 | 说明 |
|---|---|---|
| 需求/选型/原理图（spec_schema, coverage, bom×2, erc, netlist） | ✅ 6/6 | 74网络全通过 |
| 阻抗/布局/平面完整性 | ✅ 3/3 | 6层真实铺铜验证 |
| 验收仿真 9门 | ✅ 9/9 | DC几何/电流密度/腔模/PDN-AC/回流路径全绿 |
| 固件 3门 | ✅ 3/3 | 主机回归绿 |
| **drc_zero** | ✅ | **KiCad DRC 0违例** |
| **routing_completeness** | ⛔ | **ratsnest开路49条**（259→49，闭合81%） |

**布线链（2026-10-23重构轮）**：Freerouting真修复（480s超时是假象；1800s预算下
886s完成128网路由；headless SES/DSN导出双坏，唯一可用出口是.scr Fusion脚本→
scr2kicad注入）+ 单进程route_session会话收尾（ZONE_FILLER就地铺铜0.7s、
进程内ratsnest即时验证0.03s/对、0.16mm栅格加权A*、0.5/0.25 via-in-pad球阵逃逸、
有界rip-up）+ 1次DRC。

**为什么不是RELEASED**：routing_completeness是硬门禁——ratsnest必须=0。剩余49条
是Freerouting也布不出的硬尾巴（BGA球正下方FR自身B层走线挡住via-in-pad落点）。
**这是当前系统的真实能力边界，如实报告。** 同链战绩：esp32s31-dev 45→1(98%)、
esp32s31-mini 27→2(93%)，两板DRC全零。

对比：修复前同样的门禁会显示"✅ 0 unconnected"——因为severity=ignore让DRC闭嘴。
四块旧板的真相：esp32s31-dev 45条、esp32s31-mini 22条（27→22本轮改善）、
stm32n6-px4 82条、stm32h743-px4 79条开路，现在全部被门禁如实暴露。

## 下载

- [h743-fc-pcb.zip](h743-fc-pcb.zip) — 6层Gerber+钻孔+坐标（16文件）
