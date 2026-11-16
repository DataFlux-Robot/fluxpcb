---
name: policy-waiver-probe
description: 领域 skill —— 规则类残差的显式政策放行（带地板+写后探针+审计）。当 DRC 违例实测值在工艺能力带内（差距=设计规则过严而非不可制造）时使用。
argument-hint: "[fab_waivers.json 路径]"
allowed-tools: "Read, Bash(python*), Bash(kicad-cli*)"
---

# policy-waiver-probe: 政策放行与写后探针

〔五元组〕

## 形态识别器

规则违例但实测 gap ≥ 工艺地板（如 JLC 0.1mm + 20% 裕量 = 0.12mm），
且弯折量（shortfall）≤ 0.03mm —— "规则过严"而非"制造不行"。

## 机理（干什么）

分类三态 → 政策放行的写**命名规则**（flux_policy_网对_实测值）到
.kicad_dru，豁免=该网对的间距下限=实测值-1μm → **写后探针**重跑
DRC 证明规则真生效（PROBE_OK）→ 证书（地板+探针+命名+审计签名）。
地板以上放行、地板以下必须手术，界限不许被调低凑数。

## 适用条件

- ✅ 实测 ≥ 政策地板 且 短缺 ≤ 弯折上限
- ✅ 网名可命名（无名网响亮拒绝——规则无法匹配）
- ❌ 实测 < 地板（真制造问题）→ width-cascade-surgery 或挪几何
- ❌ 同网对多实例 → **合并为一条规则取最小实测**（双规则重叠严格者
   赢 = PROBE_FAILED 案）

## 验证协议

probe PROBE_OK + waiver 键残账=0 + 证书核（proofs.waiver_cert_ok）
+ 政策计数入 ledger（policy_count/policy_floor_mm 供审计对表）。

## 反例

- 同网对双实例双规则重叠 → 0.1315 实例被 0.1397 规则判负（still_waived=1 案）。
- 无名网 '<no net>' 规则写了匹配不上 → 写端响亮拒绝。
- 旧 .kicad_dru 残留规则被 load_rules 当全局约束（规则表污染案）。
