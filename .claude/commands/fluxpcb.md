---
description: 全自动PCB设计流水线 — 从自然语言需求到可下单fab包 (FluxPCB six-stage pipeline)
argument-hint: <用一句话描述你要设计的电路板,例如: 树莓派Zero大小的ESP32开发板>
---

# /fluxpcb — FluxPCB 流水线

你是 FluxPCB 流水线操作员。用户需求: **$ARGUMENTS**

按以下流程完成一次全自动电路设计,门禁全绿前不停止(有界重试),并在每步报告状态。

## 流程

### 1. 建项目
```bash
python -m fluxpcb.cli init projects/<slug>   # slug用短横线小写英文
```
然后编辑 `projects/<slug>/requirements.md` 写入用户需求(原文+拆解), 并将 `project.yaml` 改写为结构化 spec:
- `requirements[]`: 每条带 id(FR-001…)、text、priority(must/should)、covered_by(设计元素: bom/U1/J4/zones…)
- `power`: 输入源、总功耗、电源轨
- `interfaces[]`: 每个接口带类型/速率/阻抗目标(高速接口必须给 impedance_ohm+style+h_mm)
- `constraints`: 板尺寸mm、层数、介电常数
- `bom_seed`: 主控/模块的 mpn、lcsc、footprint、datasheet 路径

**器件资料规则**: 只用公开文档的器件(乐鑫/ST/TI等官网datasheet)。若主控封装/引脚表在公开渠道不可得, 立即换选公开文档齐全的型号并在报告中说明, 不要编造引脚。

### 2. 跑流水线
```bash
python -m fluxpcb.cli run projects/<slug>
```
六环节(需求分析→器件选型→原理图→PCB设计→验收仿真→固件开发)自动执行, 每环节有门禁, 失败自动带反馈重生成(默认3轮)。

### 3. 修门禁(仅当红)
读 `projects/<slug>/reports/latest.md` 找失败门禁。修复策略按优先级:
1. **结构/spec类失败**(spec_schema/requirement_coverage/bom_completeness): 改 project.yaml 后重跑该环节 `--only <stage>`
2. **ERC/DRC失败**: 读 reports 里的违例明细, 修 `fluxpcb/kicadgen/` 下对应生成器(网络表/封装/布线), 重跑 `--only schematic` 或 `--only pcb_layout`
3. **物理仿真失败**(thermal/irdrop/emc/impedance): 调整铺铜/去耦/线宽参数(生成器或 stackup)后重跑对应环节
4. 人工修板后只复检不重生成: `python -m fluxpcb.cli verify projects/<slug>`

每轮修复后重跑, 最多5轮。若某门禁确认无法自动修复, 如实报告剩余违例数与坐标, 不得调门禁阈值放水。

### 4. 导出fab包
门禁全绿后:
```bash
"C:\Users\zhang\AppData\Local\Programs\KiCad\10.0\bin\kicad-cli" pcb export gerbers \
  -o projects/<slug>/pcb/board/fab/ projects/<slug>/pcb/board/<board>.kicad_pcb
"C:\Users\zhang\AppData\Local\Programs\KiCad\10.0\bin\kicad-cli" pcb export drill \
  -o projects/<slug>/pcb/board/fab/ projects/<slug>/pcb/board/<board>.kicad_pcb
"C:\Users\zhang\AppData\Local\Programs\KiCad\10.0\bin\kicad-cli" pcb export pos \
  -o projects/<slug>/pcb/board/fab/pos.csv projects/<slug>/pcb/board/<board>.kicad_pcb
```
(路径按本机KiCad安装位置调整; 非Windows用 `kicad-cli`)

### 5. 可视化与交付
```bash
python -m fluxpcb.cli dashboard projects --port 8765   # Grounding Map面板, Ctrl+C停
```
最终报告必须包含:
- 门禁结果表(12项全绿与否)
- fab包路径与文件清单
- BOM关键器件与LCSC编号(未核实的标注 PENDING-VERIFY)
- 已知限制(手工可修的遗留项)

## 边界
- 不修改 `flux.yaml` 的门禁定义来通过测试
- 不编造datasheet里没有的引脚/参数
- 仿真结论只引用流水线产出的证据文件(sim/*.json, reports/*)
