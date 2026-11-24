"""Proposer — the TextGrad step, bounded to typed variables.

TextGrad's idea, kept where it is sound here: a variable, the traces where
it was used, and a criticism of the outcome go to an LLM, which returns how
to change the variable; the change is validated before it can act.

What is deliberately NOT taken from TextGrad: free text cannot change
behaviour.  A proposal is a (variable, value) pair that must validate
against the parameter schema, and it touches exactly one variable, so the
experiment that follows attributes its effect to that variable.  The loss
is a real DRC run, never an LLM's opinion.

Without a configured LLM the same interface is served by a rule table, and
every proposal records which proposer made it.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass

from ..verifiers.params import SCHEMA, check_value
from .diagnose import Pattern, diagnose
from .feedback import FailurePacket, residue_class
from .memory import Memory, delta_key

SYSTEM = (
    "You improve a PCB routing-repair system by proposing changes to its "
    "typed parameters. You never route boards and never write code. Each "
    "proposal changes exactly ONE variable of ONE arm, because the change "
    "is measured afterwards on real boards and must be attributable. "
    "Answer with JSON only."
)


@dataclass(frozen=True)
class Proposal:
    variable: str
    value: object
    why: str
    proposer: str            # 'llm' or 'rules'
    pattern: str             # the diagnosis pattern it answers

    def key(self, arm_name: str) -> str:
        return delta_key(arm_name, self.variable, self.value)


def moves(name: str, cur):
    """Deterministic next values for a variable, given where it is."""
    if name == 'maze_grid_extra_mm':
        return [round(min(0.5, cur + 0.04), 3), round(min(0.5, cur + 0.08), 3)]
    if name == 'maze_max_expand':
        return [min(1000000, cur * 2), min(1000000, cur * 4)]
    if name == 'corridor_offsets_mm':
        return [cur + (12.0, 16.0)]
    if name == 'via_slot_radii_mm':
        return [cur + (2.0, 2.5)]
    if name == 'jitter_steps_mm':
        return [cur + (0.5, 0.8)]
    if name == 'blind_widen':
        return [False] if cur else []
    if name == 'pad_clearance_strict':
        return [True] if not cur else []
    if name == 'nudge_steps_um':
        return [(5.0,) + cur, cur + (640.0, 1000.0)]
    if name in ('nudge_cap', 'connect_cap', 'maze_cap', 'rip_cap'):
        return [min(int(SCHEMA[name]['max']), cur * 2)]
    if name == 'preroute_pair_mm':
        return [round(max(1.0, cur * 0.5), 2), round(min(30.0, cur * 1.5), 2)]
    if name == 'preroute_max_nets':
        return [max(1, cur // 2), min(40, cur * 2)]
    if name == 'width_cascade':
        return [cur + (0.09,)]
    if name == 'jitter_candidates':
        return [min(20, cur * 2)]
    return []


def _valid(arm, name, value):
    try:
        arm.with_param(name, value)
        return True
    except (ValueError, TypeError):
        return False


def rule_proposals(diag: dict, arm, tried: set, max_deltas: int) -> list:
    out, seen = [], set()
    for pat in diag['patterns']:
        variables = (pat.variables if hasattr(pat, 'variables')
                     else (pat.get('variables') or []))
        for name, hint in variables:
            if name.startswith('__'):
                continue        # instrument issue, not a variable
            for value in moves(name, getattr(arm.params, name)):
                key = delta_key(arm.name, name, value)
                if key in tried or key in seen:
                    continue
                if not _valid(arm, name, value):
                    continue
                seen.add(key)
                out.append(Proposal(name, value, f'{pat.name}: {hint}',
                                    'rules', pat.name))
                if len(out) >= max_deltas:
                    return out
    return out


def _example(p: FailurePacket) -> dict:
    f = p.features or {}
    return {'board': p.board, 'outcome': p.outcome,
            'residue': residue_class(p),
            'layers': f.get('copper_layers'), 'pads': f.get('pads'),
            'nets': f.get('nets'), 'area_mm2': f.get('area_mm2'),
            'clearance_mm': f.get('rule_clearance_mm'),
            'wall_seconds': p.wall_seconds}


def llm_proposals(packets: list, diag: dict, arm, tried: set,
                  max_deltas: int) -> list:
    """Ask the LLM for at most `max_deltas` single-variable changes."""
    try:
        from ..llm import LLMAdapter
        adapter = LLMAdapter()
    except Exception:
        return []
    if not adapter.online:
        return []
    by_name = {}
    for p in packets:
        for pat in diag['patterns']:
            if p.outcome != 'clean':
                by_name.setdefault(pat.name, []).append(p)
    payload = {
        'objective': ('more boards pass a waiver-free KiCad DRC after the '
                      'repair loop, without making passing boards fail'),
        'arm': {'name': arm.name, 'plan': arm.plan, 'loop': arm.loop},
        'variables': [{'name': n, 'schema': s, 'current': c}
                      for n, s, c in arm.params.variables()],
        'failure_patterns': [
            {'name': pat.name, 'cells': pat.count, 'evidence': pat.evidence,
             'suspected_variables': [n for n, _ in pat.variables],
             'examples': [_example(p) for p in by_name.get(pat.name, [])[:5]]}
            for pat in diag['patterns']],
        'already_tried': sorted(tried)[-40:],
        'rules': ('at most %d proposals; each changes ONE variable; the '
                  'value must satisfy its schema; prefer changes that '
                  'address the largest patterns' % max_deltas),
    }
    prompt = ('Propose parameter changes as JSON:\n'
              '{"proposals": [{"variable": "...", "value": ..., "why": "..."}]}\n'
              'Data:\n' + json.dumps(payload, ensure_ascii=False, default=str))
    out = adapter.complete(SYSTEM, prompt, schema={'type': 'object'})
    if not out.get('ok'):
        return []
    try:
        text = out.get('text') or ''
        body = json.loads(text[text.index('{'):text.rindex('}') + 1])
        items = body.get('proposals') or []
    except (ValueError, AttributeError):
        return []
    out_list, seen = [], set()
    for item in items:
        if not isinstance(item, dict):
            continue
        name = item.get('variable')
        value = item.get('value')
        why = str(item.get('why') or '')[:300]
        if name not in SCHEMA or not _valid(arm, name, value):
            continue                      # does not compile: cannot act
        key = delta_key(arm.name, name, value)
        if key in tried or key in seen:
            continue
        seen.add(key)
        pat = next((p.name for p in diag['patterns']
                    if name in [n for n, _ in p.variables]), 'unattributed')
        out_list.append(Proposal(name, value, why, 'llm', pat))
        if len(out_list) >= max_deltas:
            break
    return out_list


def propose(packets: list, arm, memory: Memory = None,
            max_deltas: int = 3, allow_llm: bool = True) -> dict:
    """Diagnose one arm's traces and propose single-variable changes.

    Returns {'diagnosis', 'proposals', 'blocked_by'}: with an instrument
    problem the proposal list is empty and `blocked_by` says why.
    """
    diag = diagnose(packets)
    if not diag['instrument_ok']:
        return {'diagnosis': diag, 'proposals': [], 'blocked_by': 'instrument'}
    mem = memory or Memory('')
    tried = set(mem.tried()) | mem.pending()
    proposals = []
    if allow_llm:
        proposals = llm_proposals(packets, diag, arm, tried, max_deltas)
    if len(proposals) < max_deltas:
        have = {p.key(arm.name) for p in proposals}
        for p in rule_proposals(diag, arm, tried | have, max_deltas - len(proposals)):
            proposals.append(p)
    # A variable component 2 retired (tried repeatedly, never admitted) no
    # longer spends candidate budget.
    retired = {v for e in mem.entries('constraint') for v in e.get('retire', [])}
    proposals = [p for p in proposals if p.variable not in retired]
    return {'diagnosis': diag, 'proposals': proposals[:max_deltas],
            'blocked_by': None}


def candidate_arm(base, proposal: Proposal):
    """The one-variable intervention on `base` that a proposal describes."""
    import dataclasses

    value = json.dumps(proposal.value, default=str).replace('"', '').replace(' ', '')
    return dataclasses.replace(
        base.with_param(proposal.variable, proposal.value),
        name=f'{base.name}~{proposal.variable}={value}'[:80])


def as_record(proposals: list) -> list:
    return [asdict(p) for p in proposals]
