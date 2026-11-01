# -*- coding: utf-8 -*-
"""morphology — 不变量类型格 × 算子矩阵(方案②: 不学经验, 推导算子).

把残差按**被阻塞的不变量**分格, 把机制按**设计变更基元**分行 ——
矩阵格子是可枚举算子面:
  已验证格 = 已验证机制(链接 skill 五元组, 证据随行)
  空格     = 研究空白地图(下一批假设从这里出, 而不是拍脑袋)

格轴: 不变量四根 × 子类
  topology: connectivity(连通) | planarity(平面性/交叉)
  geometry: spacing(间距) | width(线宽) | escape(逃逸通道)
  rule:     drc_rule(设计规则) | netlist(网表/注解)
  capacity: corridor(走廊容量) | current(电流容量)

行轴: 设计变更五基元
  move(挪) | scale(缩) | swap(换) | bridge(桥) | replan(重划)

morphology_of(): 残差特征 -> 格坐标(分类证据随行, 不再只出网名)。
"""
from __future__ import annotations

# 格: (root, sub) —— 全枚举, 空格即空白
LATTICE = [
    ("topology", "connectivity"),
    ("topology", "planarity"),
    ("geometry", "spacing"),
    ("geometry", "width"),
    ("geometry", "escape"),
    ("rule", "drc_rule"),
    ("rule", "netlist"),
    ("capacity", "corridor"),
    ("capacity", "current"),
]

PRIMITIVES = ["move", "scale", "swap", "bridge", "replan"]

# 算子矩阵: (primitive, cell) -> 机制条目。
# status: verified(有实证) | experimental(在验) | gap(空白)
# skill:  五元组档案(适用条件/验证协议/反例都在 skill 里)
OPERATOR_MATRIX = {
    ("swap", ("topology", "connectivity")): {
        "mechanism": "topology-swap 换球/换网序解连通对冲",
        "status": "verified", "skill": "topology-swap",
        "evidence": "K29 reassign 12球 +28"},
    ("swap", ("topology", "planarity")): {
        "mechanism": "swap_pair/swap_chain3 解 escape_crossings",
        "status": "verified", "skill": "topology-swap",
        "evidence": "escape_crossings 平面性对冲计数下降"},
    ("bridge", ("topology", "connectivity")): {
        "mechanism": "zone_bridge/stitch 平面岛桥接",
        "status": "verified", "skill": "plane-bridge",
        "evidence": "zone_bridge 11->8(_zb), stitch -5"},
    ("replan", ("topology", "connectivity")): {
        "mechanism": "harvest/direct_link 验证路收割",
        "status": "verified", "skill": "endpoint-attach",
        "evidence": "机制全门验通(VDDA draw), 证书核绿"},
    ("scale", ("geometry", "width")): {
        "mechanism": "width-cascade-surgery 宽度级联收窄",
        "status": "verified", "skill": "width-cascade-surgery",
        "evidence": "55->10 真残差(PROBE_OK)"},
    ("scale", ("geometry", "spacing")): {
        "mechanism": "policy-waiver 政策放行(地板以上)",
        "status": "verified", "skill": "policy-waiver-probe",
        "evidence": "31 规则 PROBE_OK"},
    ("move", ("geometry", "spacing")): {
        "mechanism": "gap_nudge 局部几何微挪",
        "status": "experimental", "skill": "",
        "evidence": "0.002-0.005mm 级差残差待清"},
    ("move", ("geometry", "escape")): {
        "mechanism": "place_opt 挪件开逃逸 margin",
        "status": "experimental", "skill": "",
        "evidence": "'无 margin 合法候选' — 候选生成缺口"},
    ("replan", ("geometry", "escape")): {
        "mechanism": "扇出-走廊融合(狗骨 via 作端点身份)",
        "status": "experimental", "skill": "endpoint-attach",
        "evidence": "H2 试点在验"},
    ("swap", ("geometry", "escape")): {
        "mechanism": "换球解逃逸拥塞(球场死位)",
        "status": "experimental", "skill": "topology-swap",
        "evidence": "H1 A/B 在验"},
    ("replan", ("capacity", "corridor")): {
        "mechanism": "pocket_rezone 撤回-重划",
        "status": "gap", "skill": "",
        "evidence": "P1 已证伪(10->29) — 该格需新机制"},
    ("scale", ("capacity", "corridor")): {
        "mechanism": "? 走廊容量物理加宽", "status": "gap",
        "skill": "", "evidence": "空白: 挪连接器/分区重谈判待研"},
    ("move", ("capacity", "current")): {
        "mechanism": "? 电源路径加宽/换层", "status": "gap",
        "skill": "", "evidence": "空白(地板 0.15 保守, 未验证需求)"},
    ("bridge", ("geometry", "spacing")): {
        "mechanism": "? 跨缝桥的间距语义", "status": "gap",
        "skill": "", "evidence": "空白"},
    ("swap", ("rule", "netlist")): {
        "mechanism": "fix_annotation 位号注入(上游修复)",
        "status": "verified", "skill": "contract-single-source",
        "evidence": "N1 pin_mismatch 7->1"},
}


def morphology_of(residual: dict) -> dict:
    """残差特征 -> 格坐标 + 证据(不再只出网名)。
    residual: {net, class, gap_mm, islands, plane_cut,
               blockers_type, in_bga, actual_mm, kind_hint...}
    返回 {cell, evidence, confidence}。"""
    ev = []
    cls = (residual or {}).get("class") or ""
    kind = (residual or {}).get("kind_hint") or ""
    if residual.get("dogbone_blocked"):
        # 最具体的形态特征优先于宽泛类名(逃逸拥塞 ≠ 一般平面性)
        cell = ("geometry", "escape")
        ev.append("狗骨候选全灭(逃逸拥塞)")
    elif kind == "clearance":
        cell = ("rule", "drc_rule")
        ev.append("DRC clearance 违例")
    elif kind == "annotation":
        cell = ("rule", "netlist")
        ev.append("位号/网表差异")
    elif cls == "escape-crossing" or residual.get("escape_crossings"):
        cell = ("topology", "planarity")
        ev.append("escape_crossings>0 或 BGA 内外出血")
    elif cls == "power-fragment":
        cell = ("topology", "connectivity")
        ev.append("平面碎片岛间开路")
    elif residual.get("gap_mm") is not None and \
            float(residual.get("gap_mm")) < 0.12:
        cell = ("geometry", "spacing")
        ev.append("gap<0.12 需动几何")
    elif cls == "local-congestion":
        cell = ("capacity", "corridor")
        ev.append("走廊容量<铜密度")
    else:
        cell = ("topology", "connectivity")
        ev.append("默认: 开路连通类")
    return {"cell": cell, "evidence": ev,
            "confidence": "rule"}


def gaps() -> list:
    """矩阵空格 = 研究空白地图(下一批假设的出题处)。"""
    return [{"primitive": p, "cell": c,
             "question": "? %s x %s 不变量" % (p, c[1])}
            for p in PRIMITIVES for c in LATTICE
            if (p, c) not in OPERATOR_MATRIX]


def cell_status(cell: tuple) -> dict:
    """该格所有基元的机制状态(按 verified>experimental>gap)。"""
    return {p: OPERATOR_MATRIX[(p, cell)]["status"]
            for p in PRIMITIVES if (p, cell) in OPERATOR_MATRIX}
