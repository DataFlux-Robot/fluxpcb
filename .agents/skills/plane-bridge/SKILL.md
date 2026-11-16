---
name: plane-bridge
description: 领域 skill —— 平面碎片的铜桥/缝合收割（zone_bridge/stitch）。当 GND/3V3 开路对是平面被信号走廊切碎的岛间缺口时使用。族前科 11→8、-5。
argument-hint: "[power-fragment 类残差]"
allowed-tools: "Read, Bash(python*)"
---

# plane-bridge: 平面碎片桥接

〔五元组〕

## 形态识别器

power-fragment 类（GND/3V3/VDD* 网名 + 岛间缺口）；开路对两端是
同网平面岛；走廊被信号走线切碎（plane_cut 计数高）。

## 机理（干什么）

- zone_bridge：跨切缝的铜桥（geomir.plan_paths hop-repair 下潜绕
  障 + 直连落板），把碎岛重新连成连通域；
- stitch：via 缝合上下层同网平面岛（短跳直连）；
- harvest/direct_link：有验证路直接收割（窄缝 0.11mm 级联可用）。
**必须走门禁**（strict 快照 + oracle 短路计数）——裸跑实证带损伤
（+2 短 +97 间距），门禁内重做才是干净闭合。

## 适用条件

- ✅ 岛间有几何走廊（哪怕窄）
- ✅ 走廊可绕（hop-repair 找到下潜层）
- ❌ 平面岛被完全围死（无走廊）→ 挪件/重布局
- ❌ 无门禁裸跑 —— 损伤不可控（实证）

## 验证协议

门禁裁决（objective 降且 0 新短）+ 证书（islands_after 实测）+
固定套件。zone_bridge 的撕-布集合守恒（只撕要布的网）——首跑教训：
撕 392 段只布 8 网 = 10→29 开。

## 反例

- 裸跑 zone_bridge 净闭 4 开但 +2 短 +97 间距（增益是假的）。
- 走廊窗 bbox 语义撕布（应为线段距离走廊带）。
- 修复 short_repair 后漂移（门禁外施工的连锁损伤）。
