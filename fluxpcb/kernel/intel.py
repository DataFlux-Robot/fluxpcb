# -*- coding: utf-8 -*-
"""Board State Kernel — intel.py: the ONE situational report (C1).

The大拼盘 defect (points 7/8): the same situation lived in five
unrelated artifacts (route_report / driver_decisions / drc.rpt /
ratsnest_pairs / chain_manifest) and nothing correlated them — nobody
held a single coherent picture, so decisions were made on fragments.

This module is the opposite: after every measurement, ALL dimensions
fold into ONE SituationReport, and the LLM (or a rule fallback when
offline) synthesizes it into ONE assessment:
  - state verdict
  - ROOT CAUSES ranked, each with attribution (downstream symptom ←
    upstream decision cause — point 6: escape-crossing opens are a
    PIN-ASSIGNMENT defect surfacing at routing time)
  - candidate actions WITH expected effects (the fix-it hints, point 4)

The assessment is the decision layer's only input. Every dimension in,
one coherent judgment out.
"""
from __future__ import annotations

import json
from pathlib import Path


def build(pcb_path: Path, measurements: dict, pairs: list,
          diag: list | None = None, extra: dict | None = None) -> dict:
    """Fold every dimension into one report dict.

    measurements: {"opens": KernelQuery, "shorts": KernelQuery, ...}
                  — REQUIRED to be fingerprint-bound (hard contract)
    """
    from .state import require_fp
    from .causal import classify, ACTION_FOR

    for k, v in measurements.items():
        require_fp(v, context=f"intel.build[{k}]")
    taxonomy = classify(pairs, diag)
    hist_path = pcb_path.parent / "chain_manifest.json"
    history = []
    try:
        if hist_path.exists():
            history = json.loads(
                hist_path.read_text(encoding="utf-8"))[-10:]
    except (ValueError, OSError):
        history = []
    return {
        "measurements": {k: v.to_dict() for k, v in measurements.items()},
        "taxonomy": taxonomy,
        "action_for": ACTION_FOR,
        "diag": diag or [],
        "history": history,
        "extra": extra or {},
        "fp": measurements["opens"].fp.short(),
    }


# ---------------------------------------------------------------- synthesis
_SYNTH_SYSTEM = """你是 PCB 自动布线闭环的总情报官。输入是多维情报（测量/失败分类/几何证据/历史）。
你的任务是产出一份统一评估(JSON)：
{
 "state": "converging|stalled|blocked",
 "root_causes": [{"cause": "...", "evidence": "...", "attribution": "下游症状<-上游因"}],
 "actions": [{"action": "<动作名>", "params": {...}, "expected_effect": "...", "priority": 1}]
}
归因原则：底层工序的失败往往是上游决策的缺陷（如 escape-crossing 是 pin 分配制造的交叉冲突），
root_causes 必须指向可改变的上游因，而不是停留在症状。动作只能从给定候选中选。
只输出一个 JSON 对象。"""


def _rule_assessment(report: dict) -> dict:
    """Offline fallback: taxonomy × flexibility scoring (deterministic).
    Same output shape as the LLM synthesis so the decision layer
    doesn't care which path produced it."""
    tax = report.get("taxonomy", {})
    actions = []
    order = ["power-fragment", "escape-crossing", "placement-blocked",
             "local-congestion", "unclassified"]
    pri = 1
    for cls in order:
        nets = tax.get(cls) or []
        if not nets:
            continue
        act_name = report.get("action_for", {}).get(cls, "reroute_nets")
        # per-ACTION params (the contract): a uniform {"nets"} dict got
        # rejected for adjust_placement ("缺少参数 moves") — borrow the
        # first candidate's params shape for this class
        _tpl = next((o for o in (report.get("extra", {}).get("candidates")
                                 or {}).get(nets[0], [])
                     if o.get("action") == act_name), None)
        params = (_tpl or {}).get("params") or {"nets": nets[:10]}
        actions.append({
            "action": act_name,
            "params": params,
            "expected_effect": f"{cls}:{len(nets)}网经上游动作闭合",
            "priority": pri,
        })
        pri += 1
    opens = report["measurements"]["opens"]["value"]
    causes = []
    if "escape-crossing" in tax:
        causes.append({
            "cause": "pin-assignment crossing conflicts",
            "evidence": f"{len(tax['escape-crossing'])} nets one-anchor-"
                        f"in-BGA (capacity math: channels exist)",
            "attribution": "routing opens <- pin assignment binding",
        })
    if "power-fragment" in tax:
        causes.append({
            "cause": "rail fragmentation by refill/carving",
            "evidence": f"{len(tax['power-fragment'])} power nets",
            "attribution": "power opens <- zone fill semantics",
        })
    state = "converging" if opens <= 5 else (
        "stalled" if report.get("history") else "blocked")
    return {"state": state, "root_causes": causes, "actions": actions,
            "source": "rule"}


def synthesize(report: dict, log=print) -> dict:
    """One report in → ONE assessment out (LLM online preferred)."""
    if __import__("os").environ.get("FLUXNT_INTEL", "llm") == "rule":
        return _rule_assessment(report)
    try:
        from fluxpcb.llm import llm
        if not llm.online:
            return _rule_assessment(report)
        slim = {
            "measurements": report["measurements"],
            "taxonomy": report["taxonomy"],
            "diag": report["diag"][:8],
            "history": report["history"][-5:],
            "candidates": report.get("extra", {}).get("candidates", {}),
        }
        r = llm.complete(_SYNTH_SYSTEM,
                         json.dumps(slim, ensure_ascii=False)[:6000])
        if r.get("ok"):
            txt = r["text"].strip()
            txt = txt[txt.find("{"):txt.rfind("}") + 1]
            d = json.loads(txt)
            d["source"] = "llm"
            return d
    except Exception as e:                     # noqa: BLE001
        log(f"  [intel] LLM汇总失败回退规则: {e}")
    return _rule_assessment(report)


# ---------------------------------------------------------------- flexibility
def flexibility(pcb_path: Path, pairs: list, taxonomy: dict,
                plans: dict | None = None) -> dict:
    """Per-failing-net candidate actions with expected effects — the
    fix-it hints (point 4): the decision layer picks from candidates,
    it never writes open-ended answers."""
    from .causal import ACTION_FOR
    cands = {}
    for cls, nets in taxonomy.items():
        for net in nets:
            if "#" in net:
                # jumper halves (NET#J1): split nets awaiting ROUTING —
                # re-jumpering cascades (K13), but the halves are SHORT
                # hops to the jumper pads (placed in free space): map-
                # driven direct_link is exactly the right closer here.
                half_opts = []
                if plans and net in plans:
                    for pp in (plans[net].get("paths") or [])[:2]:
                        half_opts.append({
                            "action": "direct_link",
                            "params": {"net": net, "pts": pp["pts"],
                                       "width": pp.get("width", 0.2)},
                            "expected_effect":
                                f"半网直连 {pp['length_mm']}mm",
                        })
                half_opts.append({"action": "reroute_nets",
                                  "params": {"nets": [net]},
                                  "expected_effect": "跳线半网布线闭合"})
                cands[net] = half_opts
                continue
            act = ACTION_FOR.get(cls, "reroute_nets")
            # every candidate MUST carry executable params — the fix-it
            # contract: a decision without params can never validate
            opts = [{"action": act, "params": {"nets": [net]},
                     "expected_effect": f"{cls} 解因"}]
            if cls == "escape-crossing":
                opts.append({"action": "reassign_pins",
                             "params": {"nets": [net]},
                             "expected_effect": "换 AF 球位消交叉冲突"})
                opts.append({"action": "jumper", "params": {"nets": [net]},
                             "expected_effect": "0Ω 跨越封死通道"})
            elif cls == "local-congestion":
                opts.append({"action": "jumper", "params": {"nets": [net]},
                             "expected_effect": "0Ω 跨越局部堵点"})
                opts.append({"action": "adjust_placement",
                             "params": {"moves": [{"ref": "auto",
                                                   "dx_mm": 1.0,
                                                   "dy_mm": 0.0}]},
                             "expected_effect": "挪器件拉开通道"})
            elif cls == "power-fragment":
                opts.append({"action": "rerun_session",
                             "params": {"max_ripup": 6},
                             "expected_effect": "power_stitch 重缝合"})
            else:
                # unclassified persistent opens are the K13 class
                # (sealed-anchor / transformation-needed): routing retry
                # is exhausted by definition — give the FULL fallback
                # ladder, the loop must never lack a next move
                opts.append({"action": "jumper", "params": {"nets": [net]},
                             "expected_effect": "0Ω 跨越孤岛(K13闭合机制)"})
                opts.append({"action": "adjust_placement",
                             "params": {"moves": [{"ref": "auto",
                                                   "dx_mm": 1.0,
                                                   "dy_mm": 0.0}]},
                             "expected_effect": "挪器件开口后再布"})
            # M3: map-driven path candidates — the decision becomes a
            # PATH with coordinates (the fix-it's final form). Priority:
            # direct_link (map shows a free path) BEFORE transformation
            # actions (jumper/move) — draw the line first, transform
            # only when the map proves no path exists.
            path_opts = []
            if plans and net in plans:
                pl = plans[net]
                for pp in pl.get("paths", [])[:2]:
                    path_opts.append({
                        "action": "direct_link",
                        "params": {"net": net, "pts": pp["pts"],
                                   "width": pp.get("width", 0.2)},
                        "expected_effect":
                            f"图验证路径 {pp['length_mm']}mm"
                            f"@{pp.get('width', 0.2)}mm 直连",
                    })
                # reference pattern (i2c1_reroute / reroute_net.py): the
                # corridor is blocked? reroute the BLOCKER away first,
                # then draw. Blocker names come from the map's rejects.
                bl = [b for b in (pl.get("blockers") or []) if b]
                if bl and not pl.get("paths"):
                    path_opts.append({
                        "action": "reroute_nets",
                        "params": {"nets": bl[:3]},
                        "expected_effect":
                            f"重布阻挡网{bl[:3]}让出走廊",
                    })
                    # T3 playbook: FAT-corridor rescue (inflate blockers
                    # so their reroute flees the corridor walls)
                    if __import__("os").environ.get(
                            "FLUXNT_V_RESCUE", "1") != "0":
                        path_opts.append({
                            "action": "corridor_rescue",
                            "params": {"target": net, "blockers": bl[:3]},
                            "expected_effect":
                                f"膨胀{bl[:3]}逼其让出走廊",
                        })
            cands[net] = path_opts + opts
    return cands
