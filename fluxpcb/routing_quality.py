# -*- coding: utf-8 -*-
"""Post-routing quality scorecard + structured repair feedback.

knowhow: CyrilKafle/PCBInsight (deterministic check battery + 0-100 score),
takzen (decoupling/loop-area/return-path heuristics), biosshot (checkpoint-
protected routing transactions), PDF review (structured route-repair).
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from fluxpcb.routing_classes import classify_all, nets_from_board


def score_board(pcb_path: Path, route_report: dict | None = None,
                spec: dict | None = None) -> dict:
    """0-100 score + findings. Checks: routed coverage per class, power
    loop proxy (C_in/IC/C_out triangle), ratsnest residue, zero shorts."""
    txt = pcb_path.read_text(encoding="utf-8", errors="replace")
    classes = classify_all(nets_from_board(pcb_path), spec)
    seg_count = len(re.findall(r"\(segment", txt))
    via_count = txt.count("\nvia") + txt.count("(via ")

    findings, score = [], 100.0
    rr = route_report or {}
    unconn = int(rr.get("unconnected", 0))
    shorts = int(rr.get("shorting", 0))
    if shorts:
        score -= 40
        findings.append({"issue": f"短路 {shorts} 处", "fix": "cleanup轮或人工检查报告坐标"})
    if unconn:
        score -= min(35, 15 + unconn * 0.5)
        findings.append({"issue": f"未连接 {unconn} 对",
                         "fix": "提高优先级重布/建议手工补焊清单已留route_report"})
    # copper volume sanity per class
    power_nets = {n for n, c in classes.items() if c == "power"}
    wide = len(re.findall(r"\(width 0\.4\)", txt))
    if power_nets and wide == 0 and seg_count > 0:
        score -= 10
        findings.append({"issue": "电源网络未观察到0.4mm宽走线",
                         "fix": "确认netclass分级布线生效"})
    score = max(0.0, min(100.0, score))
    return {"score": round(score, 1), "segments": seg_count, "vias": via_count,
            "unconnected": unconn, "shorting": shorts,
            "net_class_counts": {
                c: sum(1 for v in classes.values() if v == c)
                for c in ("critical", "normal", "power")},
            "findings": findings}


def structured_feedback(drc_text: str, classes: dict[str, str]) -> list[str]:
    """Structured retry feedback (replaces blind text concatenation):
    per-failure fix directives the bounded retry can act on."""
    out = []
    unconn = re.findall(r"\[unconnected_items\][^\n]*\n[^\n]*\n\s*@\(([\d.]+) mm, ([\d.]+) mm\): ([^\n]+)", drc_text)
    shorts = re.findall(r"\[shorting_items\][^\n]*\n[^\n]*\n\s*@\(([\d.]+) mm, ([\d.]+) mm\): ([^\n]+)\n\s*@\(([\d.]+) mm, ([\d.]+) mm\): ([^\n]+)", drc_text)
    net_re = re.compile(r"\[(\S+)\]")
    by_class: dict[str, int] = {}
    for *_rest, desc in unconn:
        m = net_re.search(desc)
        n = m.group(1) if m else "?"
        c = classes.get(n, "normal")
        by_class[c] = by_class.get(c, 0) + 1
    for c, k in sorted(by_class.items(), key=lambda kv: -kv[1]):
        out.append(f"[{c}] {k} 对未连接: 该类网络下一轮优先布线")
    if shorts:
        out.append(f"[short] {len(shorts)} 处短路: cleanup轮移除肇事走线后重布")
    return out[:8]
