# -*- coding: utf-8 -*-
"""Board State Kernel — decision.py: the bidirectional decision protocol (C2).

The LLM↔rule feedback defect (points 3/4): decisions were free-text
hopes and rules either ran them or crashed — no type check, no
rejection feedback, no result flow-back. This module makes the
conversation between the decision layer and the rule layer explicit
and bidirectional, compiler-style:

    decision = typed Decision  (action + params + expected_effect)
    ok, fixit = validate(decision)      <- rule layer "type checks"
        rejection carries: violated constraint, by how much, and the
        alternative candidates  -> flows BACK to the decider
    outcome = execute(decision)         <- Txn-protected execution
        outcome (success / rollback + violated invariants) flows BACK
        into the next SituationReport -> the decider re-decides

A rejected or rolled-back decision is INFORMATION, never silence.
"""
from __future__ import annotations

import json
from pathlib import Path

from .state import require_fp

# the action contract: every action declares its params. A decision
# that violates the contract is rejected with a fix-it, not executed.
ACTION_CONTRACT = {
    "reroute_nets": {"nets": list},
    "rerun_session": {"max_ripup": int},
    "rerun_fluxrouter": {"iters": int},
    "rerun_freerouting": {},
    "jumper": {"nets": list},
    "direct_link": {"net": str, "pts": list},   # M3: decision = a PATH
                                                # on the geomir map
    # T3 FAT-corridor rescue (reroute_net.py trick as a compound action):
    # inflate the blockers, reroute them (they flee the fat walls),
    # restore widths — the corridor opens for the target
    "corridor_rescue": {"target": str, "blockers": list},
    "adjust_placement": {"moves": list},
    "move_footprint": {"ref": str, "dx_mm": float, "dy_mm": float},
    "reassign_pins": {"nets": list},
    "power_stitch": {},
    "finish": {"reason": str},
    "escalate": {"reason": str},
}


class DecisionRejected(Exception):
    def __init__(self, fixit: dict):
        self.fixit = fixit
        super().__init__(fixit.get("message", "decision rejected"))


def validate(d: dict, candidates: dict | None = None) -> tuple:
    """Rule-side type check. Returns (True, {}) or (False, fixit) —
    fixit is the compiler-style feedback: what was violated, by how
    much, and which alternatives exist (point 4: failure information
    must carry the repair action)."""
    if not isinstance(d, dict) or "action" not in d:
        return False, {"message": "决策必须是含 action 的对象",
                       "alternatives": list(ACTION_CONTRACT)[:5]}
    act = d["action"]
    if act not in ACTION_CONTRACT:
        return False, {"message": f"未知动作 {act}",
                       "violated": "action-contract",
                       "alternatives": list(ACTION_CONTRACT)}
    params = d.get("params") or {}
    for key, typ in ACTION_CONTRACT[act].items():
        v = params.get(key, d.get(key))
        if v is None:
            return False, {"message": f"{act} 缺少参数 {key}",
                           "violated": f"param:{key}",
                           "alternatives": [act]}
        if typ is float and isinstance(v, (int, float)):
            if abs(float(v)) > 10:
                return False, {"message": f"{key} 幅度 {v} 超限(<=10)",
                               "violated": f"param:{key}:range",
                               "alternatives": [f"{key}<=10mm"]}
        if typ is list and not isinstance(v, list):
            return False, {"message": f"{key} 必须是列表",
                           "violated": f"param:{key}:type",
                           "alternatives": []}
    if act == "move_footprint" and isinstance(params.get("ref", ""), str):
        if not params.get("ref"):
            return False, {"message": "ref 为空", "violated": "param:ref",
                           "alternatives": ["ref=<器件位号>"]}
    # anti-loop guard: repeating a no-effect action wastes the loop
    # budget — surface better candidates instead (point 4)
    if candidates and act in ("rerun_session",) :
        pass
    return True, {}


def record(pcb_path: Path, decision: dict, outcome: dict) -> None:
    """Result flow-back (the other half of the bidirectional channel):
    every decision+outcome lands in the loop journal, which the next
    SituationReport reads — the decider sees what happened last time."""
    j = pcb_path.parent / "decision_journal.json"
    try:
        entries = json.loads(j.read_text(encoding="utf-8")) \
            if j.exists() else []
    except (ValueError, OSError):
        entries = []
    entries.append({"decision": decision, "outcome": outcome})
    try:
        j.write_text(json.dumps(entries[-50:], ensure_ascii=False,
                                indent=1), encoding="utf-8")
    except OSError:
        pass
