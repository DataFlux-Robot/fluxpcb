# FluxNeurotransmitter

**全自动AI电路设计流水线** — 需求分析 → 器件选型 → 原理图 → PCB设计 → 验收仿真 → 固件开发,六环节一键贯通,每一步都有机器可判定的验证门禁。

```
需求 ──▶ spec.yaml ──▶ BOM ──▶ 原理图 ──▶ PCB ──▶ 仿真验证 ──▶ 固件
         ▲            ▲        ▲          ▲         ▲           ▲
         └────────────┴────────┴──────────┴─────────┴───────────┘
                    门禁失败 → 结构化反馈 → 自动重生成(有界迭代)
```

## 设计哲学(源自371个GitHub AI-PCB项目的实证分析)

1. **AI提议,物理裁决** — LLM负责生成,确定性求解器与EDA工具负责打分,绝不让LLM直接算物理量。
2. **门禁即规范** — "设计完成"的唯一定义是全部门禁绿灯:ERC/DRC零错、阻抗、IR-drop、热、EMC预合规、真实工具链编译。门禁全部机器可判定,失败产生结构化反馈回流。
3. **全程可审计** — 每个产出物带SHA256,每次运行产出完整报告,任何结论可追溯到证据。
4. **可复现** — `flux run projects/xxx` 一条命令重放整个设计流程。

## 快速开始

```bash
pip install -e .
flux run projects/esp32s31-dev        # 端到端跑通示例项目(六环节+门禁)
flux report projects/esp32s31-dev    # 查看审计报告
flux gate projects/esp32s31-dev --only impedance_si,irdrop   # 单独跑门禁
```

## 工具链(全部可选,缺失时相关门禁进入blocked状态)

| 工具 | 用途 | 环节 |
|---|---|---|
| KiCad 10+ (kicad-cli) | 原理图/PCB生成、ERC、DRC、网表 | 原理图/PCB设计 |
| ngspice | 电路功能仿真+蒙特卡洛 | 验收仿真 |
| openEMS + gerber2ems | 阻抗/S参数/EM场终验 | 验收仿真 |
| Freerouting | 自动布线(DSN/SES) | PCB设计 |
| ESP-IDF / hpm-sdk | 固件真实编译门禁 | 固件开发 |
| LCSC/JLC API | 器件选型与BOM | 器件选型 |

内置纯Python物理内核(无外部依赖即可运行):IPC-2141阻抗闭式解、FDM IR-drop/热场求解、IPC-2152载流、PDN目标阻抗法。

## 项目状态

- [x] Phase A — 工具本体:编排引擎+门禁框架+物理内核+CLI(26个单元测试全绿)
- [x] Phase B1 — ESP32-S31开发板:ERC=0,DRC=0,12/12门禁绿,Gerber导出 ✅
- [ ] Phase B2 — HPM6E80核心板(球阵映射待FAE数据手册)

---

## 项目案例

### 案例 1:ESP32-S31 开发板

**Prompt:**

> 只绘制一个树莓派Zero大小的esp32 s31开发板

**效果展示:**

![顶层装配视图](gallery/esp32s31-dev/render_assembly_top.svg)

<details>
<summary>查看更多渲染图</summary>

![底层装配视图](gallery/esp32s31-dev/render_assembly_bottom.svg)

![顶层铜箔布线](gallery/esp32s31-dev/render_copper_top.svg)

![顶层丝印标注](gallery/esp32s31-dev/render_silk_top.svg)

</details>

**验证结果:** 12/12门禁全绿 · KiCad ERC=0 · DRC=0 · Gerber已导出

| | |
|---|---|
| 板卡 | ESP32-S31 WiFi6开发板,65×30mm,2层 |
| 主控 | ESP32-S31-WROOM-3U(RISC-V双核320MHz) |
| 接口 | USB-C(供电+数据) · 28 GPIO · 烧录口 |
| 📦 下载 | [完整PCB项目](gallery/esp32s31-dev/esp32s31-dev-pcb.zip)(144KB) |
| 📋 详情 | [gallery/esp32s31-dev/prompt.md](gallery/esp32s31-dev/prompt.md) |

---

## License

Apache-2.0
