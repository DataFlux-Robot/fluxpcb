# z_lab 实验驱动（复现资产）

工程实验驱动的仓库副本 —— 原件在实验资产盘 `E:/DATA/vscode/ZCODE/z_lab/`
（大体积工件/板工作区不入库；此处收录**可复现代码**）。

| 驱动 | 职责 |
|------|------|
| z_watchdog | 单点守护（监督 rsi/arena/evo/clear, 断点自愈, 全成自停） |
| z_clear_fc | 飞控清线战役（实时单源逐对收割+证书） |
| z_harvest | 收割链驱动（geomir 规划→direct_link） |
| z_uart8_attack/shrink/rip | 三把手术刀（推线/收窄/拆墙）—— 战训见 DESIGN_PROGRESS |
| z_rsi_round | 内环性能 RL 训练环（学习曲线） |
| z_arena | 跨板存活（pinned/natural 两形态） |
| z_stage5 | 四曲线总验收聚合 |
| z_ab / z_rollout | A/B 双臂 / 窗口 rollout |
| z_evo / z_l2 | 外环 harness 进化 / L2 自改进环 |
| z_jlc_align / z_gapclean | JLC 规则对齐 / 间距清线 |
| board_synth | 板池构建（真实板+课程烤验板） |

复现要点：KiCad python（pcbnew）、`z_lab/.env_secret`（LLM 密钥，不入库）、
固定套件 `fixed_tests.json`、经验卡 `experience_cards/`。
