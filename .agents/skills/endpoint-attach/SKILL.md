---
name: endpoint-attach
description: 领域 skill —— 跨层路径的端点连通解析（三态：同层/借道/附着孔）。当路径画了但连通不变、或规划路在内层而焊盘在表层时使用。
argument-hint: "[direct_link spec 或失败的收割记录]"
allowed-tools: "Read, Bash(python*)"
---

# endpoint-attach: 端点三态连通解析

〔五元组〕

## 形态识别器

"drawn=N 但开路对不变"（浮空走线）；规划层 ≠ 端点焊盘层（内层
走廊 vs 表层焊盘）；收割链 reverify 通过但闭合测量不动。

## 机理（干什么）

绘制前对每个端点做三态解析：
① 焊盘在路层 → 同层直连；
② 同网过孔在附近（fanout 狗骨）→ **借道**：端点改指过孔位置；
③ 都不是 → 端点打**附着过孔**（via_ok 全层校验，不过则诚实失败，
   不画半截线）。
端点解析必须发生在 reverify **之前**（否则被改段逃出验证覆盖）。
闭合=测量语义：出证带 islands_after 实测（=1 才算闭合）。

## 适用条件

- ✅ 路径跨层（内层走廊 + 表层端点）
- ✅ fanout 已扇出（借道零成本）
- ❌ 球场死位无处打孔（via_ok 全灭）→ 先 topology-swap 换球位
- ❌ 端点是过孔/焊盘对无处附着 → 需挪件/换球，不硬打孔

## 验证协议

证书（proofs.make_closure_cert）：endpoint_attach 三态日志 +
via_ok 标记 + islands_after=1 + coords 圆整；closure_cert_ok 通过
且实时开路对账无 stale 声明。

## 反例

- 同层画线两表层焊盘间跨内层走廊（浮空案 drawn=3 连通不变）。
- 端点解析在验证之后（被改段未验即画）。
- quiet 层负索引验证回绕绘制不回绕（验 bottom 画 F 案）。
