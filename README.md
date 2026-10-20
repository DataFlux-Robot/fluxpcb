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
flux run projects/_example       # 端到端跑通示例项目(六环节+门禁)
flux report projects/_example    # 查看审计报告
flux gate projects/_example --only impedance_si,irdrop   # 单独跑门禁
```

## 工具链(全部可选,缺失时相关门禁进入blocked状态)

| 工具 | 用途 | 环节 |
|---|---|---|
| KiCad 8+ (kicad-cli) | 原理图/PCB生成、ERC、DRC、网表 | 原理图/PCB设计 |
| ngspice | 电路功能仿真+蒙特卡洛 | 验收仿真 |
| openEMS + gerber2ems | 阻抗/S参数/EM场终验 | 验收仿真 |
| Freerouting | 自动布线(DSN/SES) | PCB设计 |
| ESP-IDF / hpm-sdk | 固件真实编译门禁 | 固件开发 |
| LCSC/JLC API | 器件选型与BOM | 器件选型 |

内置纯Python物理内核(无外部依赖即可运行):IPC-2141阻抗闭式解、FDM IR-drop/热场求解、IPC-2152载流、PDN目标阻抗法。

## 项目状态

- [x] Phase A — 工具本体:编排引擎+门禁框架+物理内核+CLI
- [ ] Phase B — 首个设计案例:HPM6E80×ESP32-S31核心板(树莓派Zero尺寸,EtherCAT×2+TSN)

## License

Apache-2.0
