---
name: topology-swap
description: 领域 skill —— 换球解逃逸拥塞/平面性反序（SWAP_CHAIN×AF 表）。当球位逃逸通道死锁（狗骨全灭）、平面性对冲（escape_crossings>0）时使用。族前科 +28。
argument-hint: "[残差网名或 escape-crossing 类]"
allowed-tools: "Read, Bash(python*)"
---

# topology-swap: 换球解拓扑不变量

〔五元组〕

## 形态识别器

- 球位逃逸拥塞：BGA 球场内焊盘的狗骨候选全灭（via_ok 逐位被邻网
  逃逸线占死）；
- 平面性反序：escape_crossings 计数 > 0（球位序 × 逃逸方向序对冲）；
- 特征：局部算子（nudge/rip/widen/move）全败——不变量在拓扑层。

## 机理（干什么）

在 MCU AF 复用表约束内换球位：
- swap_pair（成对交换）：解开两两交叉；
- swap_chain3（三元旋转）：两两解不动的三体对冲 A→B→C→A；
- single_drift（单球漂移）：球心方位与逃逸方向 cos 对齐。
落盘 FC_PIN_OVERRIDE → 板/原理图同源重生成（mcu_ball_nets 单源）→
门禁重布验证。mode 字段区分搜索深度（已修，三算子不再同貌）。

## 适用条件

- ✅ 残差属 escape-crossing 类、目标信号在 AF 族内有可换球
- ✅ 换球后门禁验证 0 新短、objective 降
- ❌ 电源/专球（AF 天然排除）
- ❌ AF 族无可换球 → 扇出-走廊融合或挪件

## 验证协议

A/B（z_ab: 换球臂 vs 基线臂，固定套件）+ 门禁回滚保护 + 证书
（换球后重测 delta_opens/delta_shorts 入账）。

## 反例

- 球位键用 'x,y' 合成键（真键 'A1' 行列名）—— 换球恒空案。
- mode 死字段（三算子执行同一搜索）—— A/B 臂无差异=无效实验。
- 换球后 gap 不可测仍按 gap 记成功 —— 兜底全局开路数（K29 教训）。
