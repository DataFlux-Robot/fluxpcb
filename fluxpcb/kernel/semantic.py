# -*- coding: utf-8 -*-
"""A4 — 语义耦合状态表示.

出处: SchGen(2605.30345)"表示即瓶颈: 引脚对/相对语义优于裸坐标"
+ PCB-QA(2606.23704) JSON 文本表示对 LLM 最优(93%)
+ OmniRouting(2608.04434) 语义耦合: 网功能角色(电源/差分/时钟)绑几何。

本模块把测量/分类输出编成**引脚对+网角色** JSON 语义视图:
决策层(LLM/规则)看到的是"N1 时钟类, 2 对断点, 逃逸交叉"而不是
裸坐标列表 —— 坐标只在执行层(direct_link spec)出现。

契约:
  读=pairs 行/taxonomy/net_classes/diff_pairs(全部已有真源);
  写=semantic view dict(JSON 可序列化, 供 intel.report["extra"]);
  语义前提=角色是**派生标签**不覆盖测量(taxonomy 的类判定优先)。
"""
from __future__ import annotations

CLK_HINTS = ("CLK", "OSC", "XTAL", "HSE", "LSE", "MCO", "CLOCK")


def net_role(name: str, net_classes: dict | None = None,
             diff_pairs: list | None = None) -> str:
    """网功能角色: power / diff / clock / critical / signal."""
    nc = (net_classes or {}).get(name, "")
    if nc == "power":
        return "power"
    for pair in (diff_pairs or []):
        members = pair if isinstance(pair, (list, tuple)) else [pair]
        if name in [str(m) for m in members]:
            return "diff"
    up = name.upper()
    if any(h in up for h in CLK_HINTS):
        return "clock"
    if nc == "critical":
        return "critical"
    return "signal"


def semantic_view(pairs_lines: list, taxonomy: dict | None = None,
                  net_classes: dict | None = None,
                  diff_pairs: list | None = None) -> dict:
    """引脚对+网角色语义视图(决策层输入, SchGen 表示)。

    pairs_lines: conn_audit 的 'net|x|y|l|x|y|l' 行;
    taxonomy: {cls: [nets]} — 类归属(逃逸交叉/平面碎片...)。
    """
    taxonomy = taxonomy or {}
    cls_of = {}
    for cls, nets in taxonomy.items():
        for n in nets:
            cls_of[str(n)] = cls
    nets: dict = {}
    for ln in pairs_lines or []:
        parts = str(ln).split("|")
        if len(parts) < 7:
            continue
        name = parts[0]
        entry = nets.setdefault(name, {
            "role": net_role(name, net_classes, diff_pairs),
            "class": cls_of.get(name, "unclassified"),
            "pairs": [],
        })
        entry["pairs"].append({
            "a": [round(float(parts[1]), 3), round(float(parts[2]), 3)],
            "b": [round(float(parts[5]), 3), round(float(parts[6]), 3)],
        })
    return {"nets": nets,
            "summary": {
                "by_role": _count(nets, "role"),
                "by_class": _count(nets, "class"),
                "open_nets": len(nets),
                "open_pairs": sum(len(e["pairs"])
                                  for e in nets.values()),
            }}


def _count(nets: dict, key: str) -> dict:
    out: dict = {}
    for e in nets.values():
        out[e.get(key, "?")] = out.get(e.get(key, "?"), 0) + 1
    return out
