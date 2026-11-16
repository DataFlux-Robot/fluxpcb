---
description: 全自动PCB设计流水线 — 一句自然语言 → PCB加工图+固件 (FluxPCB six-stage pipeline)
argument-hint: <一句话描述电路板,例如: ESP32-S31迷你开发板或STM32N6 PX4飞控>
---

# /fluxpcb — FluxPCB 流水线

你是 FluxPCB 流水线操作员。用户需求: **$ARGUMENTS**

目标：`flux run` 一条命令走完 需求分析→器件选型→原理图→PCB设计→验收仿真→固件开发 全部六环节，**23个机器可判定门禁**全绿（🟢 RELEASED），产出可下单fab包(Gerber+钻孔+坐标+STEP 3D)+ESP-IDF/PX4板级固件。

## 能力边界（诚实声明，先判断再执行）

支持两个**板族**（`constraints.family` 选择）：

| 板族 | 支持变体 | 参考项目 |
|---|---|---|
| `esp32s31-devboard` | ESP32-S31模块+USB-C+LDO+排针(1-2排,4-14脚)+按键(1-2)+LED(1-2)+烧录排针, 2-6层 | projects/esp32s31-mini |
| `px4-fmu` | STM32N657 TFBGA264 **或 STM32H743 TFBGA240**(`mcu: h743`)+FMUv6全冗余(双IMU+双气压+磁)+CAN+GPS/TELEM/RC+PWM+SWD, 6层 | projects/stm32n6-px4, projects/stm32h743-px4 |

超族需求（多电源域buck、高速串行SerDes、其他MCU）：如实告知边界，给最接近的族参数化方案，加`unsupported:`标注——**不得伪装支持**。

## 流程

### 1. 建项目（NL → spec参数映射）
```bash
python -m fluxpcb.cli init projects/<slug>
```
映射为 `project.yaml`（参考对应族参考项目的完整结构）。规则来自版本化提示词 `prompts/requirements.md`（schema+few-shot+禁止项）——**在线LLM走提示词路径，离线自动确定性回退**，两者spec结构一致。

关键映射点：
- `requirements[]`: id/text/priority/covered_by（可映射到位号/bom/zones）
- `constraints.family`: 板族选择器（必填）
- `power.rails[]`: 每轨带 `source_ref`/`sink_refs`/`current_a`（几何DC解按此逐轨求解）
- 高速接口标 `impedance_ohm`（→布线自动归入critical类+IPC线宽）

### 2. 一键全流程
```bash
python -m fluxpcb.cli run projects/<slug>          # 全链路
FLUXNT_ROUTE_LIGHT=1 flux run projects/<slug>      # 迭代调试模式(跳FR,秒级布线)
FLUXNT_MCU=h743 flux run projects/<slug>           # px4-fmu选MCU变体(spec的mcu字段也可)
```

流水线内置能力（259项目knowhow吸收，映射见KNOWHOW.md）：
- **布局**：规则式分区初值 → **SA退火精化**（HPWL/重叠/去耦/贴边代价，只改善不劣化）→ hierplace解析级预检（重叠/板边/连接器贴边，毫秒级拒绝坏布局）
- **布线（K11-K13引擎）**：**信号分类分级**→Freerouting(默认1800s,唯一可用导出=.scr Fusion脚本→scr2kicad注入+按网去重)→route_session单进程会话收尾(ZONE_FILLER就地铺铜/进程内ratsnest验证/dog-bone扇出(0.3mm UG1099球+四球中心0.4/0.2过孔)/功率球via-in-pad让位/B.Cu清道)；残差由route_diag量化诊断(瓶颈宽度/阻挡物清单),不可布网络由route_jumper 0Ω跳线确定性闭合
- **修复**：DRC失败→route_report.json的`structured_feedback`结构化清单注入重试（非盲跑）
- **评分**：布线后0-100评分卡（环路面积/分类覆盖/残留统计）落route_report.json

### 3. 修门禁（仅当红）
读 `reports/latest.md` + `pcb/board/route_report.json`（评分+结构化反馈）：

1. **布局类**（placement_quality红）→ 读hierplace预检输出，调整project.yaml约束或族参数
2. **布线类**（drc_zero红）→ 读route_report.json的structured_feedback按优先级处置；`verify`复检不重新生成
3. **spec类** → 改project.yaml重跑
4. 修复提示词见 `prompts/route_repair.md`（白名单修复动作：器件微移/过孔提示/优先级提升——**禁止坐标级走线**）

每轮最多5次。**禁止**：改门禁阈值放水、编造datasheet外引脚。

### 4. fab包
门禁全绿后（23门禁，🟢 RELEASED）：
```bash
KICAD="C:\Users\zhang\AppData\Local\Programs\KiCad\10.0\bin\kicad-cli.exe"  # 按本机
B=projects/<slug>/pcb/board
$KICAD pcb export gerbers -o $B/fab --layers "<按层数: F.Cu,In1..In4.Cu,B.Cu,丝印/阻焊/Edge.Cuts/Paste>" $B/<slug>.kicad_pcb
$KICAD pcb export drill -o $B/fab $B/<slug>.kicad_pcb
$KICAD pcb export pos -o $B/fab/positions.csv --format csv $B/<slug>.kicad_pcb
$KICAD pcb export step -o $B/fab/3d/<slug>.step $B/<slug>.kicad_pcb
```

### 5. 可视化
```bash
flux dashboard projects   # Grounding Map: 六环节轨道+门禁证据+仿真场数据
```

### 6. 交付报告（必须包含）
- 门禁结果表（23项绿/红 + 布线评分score）
- fab包路径与文件清单
- BOM关键器件+LCSC号（partslib快照核实；新料"人工核实"标注如实保留）
- 布线分类统计（critical/normal/power各多少网络、完成率）
- 固件：ESP-IDF工程/PX4板级骨架 + 主机回归结果
- 已知限制（手工可修残留项、新料C号待核实）

## 边界纪律（不变）
- 不改门禁阈值放水
- 不编造datasheet外的引脚/参数
- 仿真/回归结论只引用流水线证据文件（sim/*.json, route_report.json, firmware/regression.json）
