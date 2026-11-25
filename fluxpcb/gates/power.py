"""Power-budget gate: declared energy source vs declared load duty cycle.

Reads `spec.yaml power.budget` (a nested block, so the existing `power`
rails/total_w contract is untouched).  With no budget the gate is BLOCKED
and `flux.yaml` marks it optional: a USB-powered board is not failed for
not having a battery model, but the report says the runtime is unknown.
With a budget it becomes a hard gate — if you declare a 365-day target and
the arithmetic says 40, that is a failure, not advice.

Every number is an estimate of a declared model: battery capacity is derated
by the declared usable factor, droop uses declared pack/path resistance, and
no temperature or ageing model is applied.
"""
from __future__ import annotations

from pathlib import Path

from fluxpcb.models import GateResult, GateStatus
from fluxpcb.gates import gate
from fluxpcb.tools import power_budget as pb


def _spec(project_dir: Path):
    import yaml
    p = project_dir / 'spec.yaml'
    if not p.exists():
        return None
    try:
        return yaml.safe_load(p.read_text(encoding='utf-8')) or {}
    except Exception:
        return None


def _finding(rule, measured, limit, units, severity, assumption,
             needed=()):
    return {'rule_id': rule, 'where': {'text': 'battery'},
            'measured': measured, 'limit': limit, 'units': units,
            'severity': severity, 'status': 'estimate',
            'phase': 'post_route', 'assumption': assumption,
            'needed_information': list(needed)}


@gate('power_budget')
def power_budget_gate(project_dir: Path, outputs: list[str],
                      cfg: dict) -> GateResult:
    spec = _spec(project_dir)
    if spec is None:
        return GateResult('power_budget', GateStatus.BLOCKED, 'spec.yaml 不存在')
    budget = ((spec.get('power') or {}).get('budget'))
    if not isinstance(budget, dict) or not budget:
        return GateResult(
            'power_budget', GateStatus.BLOCKED,
            'spec.yaml 未声明 power.budget（功耗预算无法判定）',
            needed_information=['power.budget: 电池容量/化学体系、休眠电流、'
                                '占空比（电流×时长×次数）、周期、末期电压、'
                                '最小工作电压'])
    r = pb.run(budget)
    if r.get('status') != 'ok':
        return GateResult(
            'power_budget', GateStatus.BLOCKED,
            f"功耗预算缺输入: {', '.join(r.get('missing') or [])}",
            needed_information=[f'power.budget.{m}' for m in r.get('missing') or []])
    a = r['assumption']
    findings = []
    life, target = r.get('life_days'), r.get('life_target_days')
    if life is not None:
        sev = 'P1' if (target and life < target) else 'P3'
        findings.append(_finding('POWER.life', life, target, 'days', sev, a,
                                 [] if target else ['life_target_days']))
    vmin, vop = r.get('v_min_v'), r.get('v_min_op')
    if vmin is not None:
        findings.append(_finding('POWER.droop', vmin, vop, 'V',
                                 'P1' if (vop and vmin < vop) else 'P3', a))
    if r.get('cap_uf') is not None:
        avail = r.get('cap_available_uf')
        sev = 'P2' if (avail is not None and r['cap_uf'] > avail) else 'P3'
        findings.append(_finding('POWER.bulk_cap', r['cap_uf'], avail, 'uF',
                                 sev, a,
                                 [] if avail is not None else
                                 ['cap_available_uf (the bulk cap on the board)']))
    bad = [f for f in findings if f['severity'] in ('P0', 'P1')]
    summary = (f"续航 {life} 天" + (f"（目标 {target}）" if target else '')
               + f"；峰值压降后 {vmin} V（下限 {vop}）"
               + (f"；需要 {r['cap_uf']} µF" if r.get('cap_uf') is not None else ''))
    return GateResult('power_budget', GateStatus.FAIL if bad else GateStatus.PASS,
                      summary, details=r, findings=findings,
                      needed_information=[i for f in findings
                                          for i in (f.get('needed_information') or [])])
