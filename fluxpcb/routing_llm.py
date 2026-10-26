# -*- coding: utf-8 -*-
"""LLM driver for the routing chain (W-round): LLM 驾驶员 + 规则发动机.

The K7-K16 rounds established that every routing breakthrough came from
LLM judgment (bug diagnosis, chain-order calls, geometry reasoning) while
the rule engines (session/fluxrouter/Freerouting) excel at execution.
Pure rule-based autorouting plateaued at 49 opens on h743-fc for five
rounds — the remaining opens are DECISION problems (which net to jumper,
which footprint to nudge, which engine to rerun), not algorithm problems.

This module makes the judgment call a first-class loop step:

    rule engine executes -> telemetry collected -> decide() -> executor

decide() has three modes, in priority order:
  1. real LLM  — FLUXNT_LLM_PROVIDER/MODEL/API_KEY_ENV/BASEURL set
  2. policy    — FLUXNT_LLM_POLICY=<json file>, a queue of decisions an
                 LLM (or human) prepared offline; consumed in order
  3. fallback  — deterministic legacy policy (never blocks, replicates
                 the pre-W fixed strategy)

Every decision is validated against MENU; anything invalid degrades to
the fallback action, so a hallucinating model can at worst behave like
the legacy loop.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

# ------------------------------------------------------------- decision menu
MENU = {
    # surgical: rip EXACTLY these nets, fix-protect everything else, one
    # incremental Freerouting pass re-routes just them (reference-project
    # reroute_net.py pattern — the workhorse for a small stubborn residue)
    "reroute_nets": {"nets": list},
    # industry recipe: whitelist a net set for a first-claim session pass
    # (critical/diff pairs claim the good channels before the bulk run;
    # the converged preroutes then ride the Z1 fix-marking)
    "preroute_critical": {"nets": list},
    # run another session pass (fanout + sweep + bounded rip-up)
    "rerun_session": {"max_ripup": int},
    # negotiated-congestion pass on all signal layers
    "rerun_fluxrouter": {"iters": int},
    # incremental Freerouting pass over ALL opens (converged copper fix-marked)
    "rerun_freerouting": {"timeout_s": int},
    # place 0R jumpers on specific nets (post route_diag verdicts)
    "jumper": {"nets": list},
    # nudge footprints that keep blocking (R5 margin loop; blockers from
    # route_diag verdicts — a ref showing up >=2x is a placement problem,
    # not a routing problem)
    "adjust_placement": {"moves": list},
    # nudge a footprint and rip its nets' tracks (schematic-safe)
    "move_footprint": {"ref": str, "dx_mm": float, "dy_mm": float},
    # swap two pads' nets on a footprint (board-only; schematic drifts —
    # gated by FLUXNT_ALLOW_PIN_SWAP in the executor)
    "swap_pins": {"ref": str, "pad_a": str, "pad_b": str},
    # stop: current state is the deliverable
    "finish": {"reason": str},
    # stop: needs a human / upstream regeneration decision
    "escalate": {"reason": str},
}

FALLBACK = "fallback"


def _fallback_decision(tel: dict, history: list) -> dict:
    """Deterministic legacy policy — the pre-W auto_route behavior,
    upgraded with the reference-project's surgical-first residue rule:
    a small stubborn residue goes to per-net reroute, not another
    global pass."""
    opens = tel.get("opens", -1)
    shorts = tel.get("shorts", 0)
    if opens == 0 and shorts == 0:
        return {"action": "finish", "reason": "opens=0 shorts=0",
                "source": FALLBACK}
    # two consecutive no-progress decisions -> escalate (legacy stop)
    outcomes = [h.get("delta_opens", 0) for h in history[-2:]]
    if len(outcomes) == 2 and all(d >= 0 for d in outcomes) and history:
        # R5 margin rule: a footprint blocking >=2 diagnosed nets is a
        # PLACEMENT problem (UCSD margin-max: placement-only changes cut
        # DRV ~80%) — nudge it before giving up
        from collections import Counter
        refs = Counter()
        for v in tel.get("diag") or []:
            for b in v.get("blockers", []):
                refs[b] += 1
        hot = [r for r, c in refs.items() if c >= 2]
        if hot:
            moves = [{"ref": r, "dx_mm": 1.0, "dy_mm": 0.0} for r in hot[:3]]
            return {"action": "adjust_placement", "moves": moves,
                    "reason": f"布局阻塞器件{hot[:3]}", "source": FALLBACK}
        return {"action": "escalate",
                "reason": f"连续无进展({outcomes})", "source": FALLBACK}
    frag = tel.get("fragmented") or {}
    if 0 < opens <= 10 and frag:
        # surgical: the whole residue, one incremental FR pass
        return {"action": "reroute_nets", "nets": sorted(frag)[:10],
                "reason": f"残差{opens}网,外科手术", "source": FALLBACK}
    return {"action": "rerun_session", "max_ripup": 12, "source": FALLBACK}


def _validate(d: dict) -> dict | None:
    """Coerce/validate an LLM/policy decision against MENU."""
    if not isinstance(d, dict):
        return None
    act = d.get("action")
    if act not in MENU:
        return None
    out = {"action": act, "reason": str(d.get("reason", ""))[:200]}
    for k, typ in MENU[act].items():
        v = d.get(k)
        if v is None:
            return None
        try:
            if typ is int and isinstance(v, bool):
                raise TypeError
            out[k] = typ(v)
            if typ is float or typ is int:
                if not (-10.0 <= float(out[k]) <= 10.0
                        if typ is float else 0 <= out[k] <= 10 ** 6):
                    return None
        except (TypeError, ValueError):
            return None
    if act == "move_footprint" and abs(out["dx_mm"]) + abs(out["dy_mm"]) > 10:
        return None                      # nudges only, not teleporting
    return out


_SYSTEM = """你是PCB自动布线链的策略决策者(驾驶员)。规则引擎(会话/fluxrouter/Freerouting)
负责执行几何布线,你负责读仪表盘并从菜单选择下一步动作。

决策菜单(必须输出JSON {"action": ..., "reason": ..., ...参数}):
- reroute_nets {nets:[...]}              — 外科手术:撕掉指定网络的铜,其余全部
                                          protect,FR只重布这几个网(残差首选)
- rerun_session {max_ripup:int}          — 再跑一轮会话布线(扇出+扫掠+有界撕除)
- rerun_fluxrouter {iters:int}           — 多层协商布线(适合开路多且分散时)
- rerun_freerouting {timeout_s:int}      — 增量FR布全部开路网(尊重已收敛铜)
- jumper {nets:[...]}                    — 对指定网放0Ω跳线(route_diag判jumper/
                                          disconnected-components时)
- move_footprint {ref,dx_mm,dy_mm}       — 挪器件并撕其网络走线(bottleneck堵死在
                                          器件锚点时;幅度<=10mm)
- swap_pins {ref,pad_a,pad_b}            — 交换两焊盘网络(封死BGA球换逃逸通道;
                                          注意原理图会漂移)
- finish {reason}                        — 当前状态即为交付
- escalate {reason}                      — 需要上游再生成/人工

判据经验(来自K7-K16实测+参考工程esp32s3_deskpet):
- 残差<=10个开路网 → reroute_nets逐网外科手术性价比最高
- physical失败主导+开路聚集BGA → 逃逸资源不足,考虑swap_pins或jumper
- nopath主导+失败签名多轮不变 → 板上铜太挤,move_footprint或escalate
- 开路分散且>15 → rerun_fluxrouter或rerun_freerouting性价比高
- route_diag判routable的少量开路 → rerun_session或reroute_nets可收
- shorts>0 → 引擎cleanup处理,若决策轮后仍>0且不降 → escalate
不要重复已经无效的动作(history里delta_opens>=0的同动作)。只输出一个JSON对象。"""


def _user_prompt(tel: dict, history: list) -> str:
    slim = {
        "opens": tel.get("opens"),
        "shorts": tel.get("shorts"),
        "fragmented_top": dict(list(
            (tel.get("fragmented") or {}).items())[:15]),
        "drc_counts": tel.get("drc_counts"),
        "drc_samples": (tel.get("drc_samples") or [])[:6],
        "fluxrouter_last": tel.get("fluxrouter_last"),
        "diag_verdicts": (tel.get("diag") or [])[:12],
        "history": [
            {"action": h.get("action"),
             "delta_opens": h.get("delta_opens"),
             "delta_shorts": h.get("delta_shorts")}
            for h in history[-6:]],
    }
    return ("仪表盘JSON:\n"
            + json.dumps(slim, ensure_ascii=False, indent=1)
            + "\n\n给出下一步决策(单个JSON对象)。")


class Driver:
    """Decision loop driver — mode selection + audit trail."""

    def __init__(self, log=print):
        self.log = log
        self.history: list[dict] = []
        self.decisions: list[dict] = []
        self._policy_queue = self._load_policy()
        self._llm = None
        if os.environ.get("FLUXNT_LLM_PROVIDER", "none").lower() != "none":
            from fluxpcb.llm import LLMAdapter
            self._llm = LLMAdapter()

    # -------------------------------------------------- modes
    @staticmethod
    def _load_policy() -> list:
        p = os.environ.get("FLUXNT_LLM_POLICY", "")
        if p and Path(p).exists():
            try:
                data = json.loads(Path(p).read_text(encoding="utf-8"))
                return data if isinstance(data, list) else [data]
            except (ValueError, OSError):
                return []
        return []

    @property
    def mode(self) -> str:
        if self._policy_queue:
            return "policy"
        if self._llm is not None and self._llm.online:
            return "llm"
        return FALLBACK

    # -------------------------------------------------- decide
    def decide(self, telemetry: dict) -> dict:
        d = None
        if self._policy_queue:
            d = _validate(self._policy_queue.pop(0))
            src = "policy"
        elif self._llm is not None and self._llm.online:
            r = self._llm.complete(_SYSTEM, _user_prompt(telemetry,
                                                         self.history))
            if r.get("ok"):
                try:
                    txt = r["text"].strip()
                    txt = txt[txt.find("{"):txt.rfind("}") + 1]
                    d = _validate(json.loads(txt))
                except (ValueError, AttributeError):
                    d = None
            src = "llm"
            if d is None and not r.get("ok"):
                self.log(f"  [driver] LLM调用失败: {r.get('error', '')[:80]}")
        if d is None:
            d = _fallback_decision(telemetry, self.history)
            src = FALLBACK if not self._policy_queue and self._llm is None \
                else f"{src}→{FALLBACK}"
        d["source"] = d.get("source", src)
        self.decisions.append(d)
        self.log(f"  [driver] {d['action']} ({d['source']}) "
                 f"{d.get('reason', '')[:60]}")
        return d

    # -------------------------------------------------- outcome feedback
    def record_outcome(self, opens_before: int, shorts_before: int,
                       opens_after: int, shorts_after: int) -> None:
        self.history.append({
            "action": self.decisions[-1]["action"] if self.decisions else "?",
            "delta_opens": opens_after - opens_before,
            "delta_shorts": shorts_after - shorts_before,
        })
