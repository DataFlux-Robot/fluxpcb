"""The five physics verifiers, backed by the gates that do the work.

Before this module the verifier layer was a façade: five classes that
returned ``status='pass'`` unconditionally, while the real solvers sat
in ``fluxpcb/gates`` behind 26 gates that nothing in ``fluxpcb.pipeline``
ran.  A report could say "power integrity: pass" with no IR drop ever
computed.

Here each verifier names the gates that answer its question and runs
them.  Nothing is re-implemented: the gate remains the one measurement,
the verifier is the component-10 view of it.  A gate that cannot decide
(BLOCKED, solver missing) becomes ``status='unknown'`` with the missing
information, never a pass.

    GATE_MAP            verifier name -> gate names
    run_verifier(name, state, plan, policy) -> VerificationResult
    project_dir_of(state) -> Path | None
"""
from __future__ import annotations

import os
from pathlib import Path

from ..contracts import VerificationResult
from ..findings import now_iso
from .policy import PhysicsPolicy, DEFAULT

GATE_MAP = {
    'power_integrity': ('pdn_impedance_ac', 'dc_current_density',
                        'dc_irdrop_geom'),
    'signal_integrity': ('impedance_si',),
    'thermal': ('thermal', 'thermal_cfd'),
    'spice_functional': ('spice_functional',),
    'emc_precompliance': ('return_path', 'plane_cavity_resonance',
                          'em_susceptibility', 'em_field_analysis'),
}


def project_dir_of(state) -> Path | None:
    """Where the gates read their inputs from, given a BoardState."""
    cand = getattr(state, 'board_path', '') or ''
    if not cand:
        return None
    p = Path(cand)
    for d in (p.parent, p.parent.parent, p.parent.parent.parent):
        if any((d / f).exists() for f in ('project.yaml', 'spec.yaml',
                                          'stackup.yaml')):
            return d
    return p.parent


_STATUS = {'pass': 'pass', 'fail': 'fail', 'blocked': 'unknown',
           'skipped': 'skip'}


def run_verifier(name: str, state, plan=None,
                 policy: PhysicsPolicy = None) -> VerificationResult:
    """One component-10 verdict, assembled from the gates that answer it."""
    policy = policy or DEFAULT
    gates = GATE_MAP.get(name, ())
    project = project_dir_of(state)
    evidence = {'gates': {}, 'policy': policy.as_dict(),
                'at': now_iso(), 'project_dir': str(project or '')}
    if project is None or not project.exists():
        missing = ['no project directory beside the board: the physics '
                   'gates read sim/, spec.yaml, stackup.yaml']
        return VerificationResult(
            name=name, status='unknown', severity='warning',
            findings=[], needed_information=missing,
            evidence=dict(evidence, needed_information=missing))
    from ..gates import run_gate

    findings, needed, worst = [], [], 'pass'
    for g in gates:
        if not policy.allows(g):
            missing = policy.why_not(g)
            needed.extend(missing)
            evidence['gates'][g] = {'status': 'not run', 'why': missing}
            worst = _worse(worst, 'unknown')
            continue
        res = run_gate(g, project, [], {})
        evidence['gates'][g] = {
            'status': res.status.value, 'summary': res.summary,
            'details': res.details,
            'needed_information': res.needed_information}
        findings.extend(res.findings)
        needed.extend(res.needed_information)
        worst = _worse(worst, _STATUS.get(res.status.value, 'unknown'))
    if worst == 'pass' and any(
            f.get('status') == 'unknown' for f in findings):
        worst = 'unknown'
    return VerificationResult(
        name=name, status=worst,
        severity='error' if worst == 'fail' else 'warning',
        findings=findings,
        evidence=dict(evidence, needed_information=_uniq(needed)))


_ORDER = {'pass': 0, 'skip': 1, 'unknown': 2, 'fail': 3}


def _worse(a: str, b: str) -> str:
    return a if _ORDER.get(a, 2) >= _ORDER.get(b, 2) else b


def _uniq(items) -> list:
    out = []
    for i in items:
        if i and i not in out:
            out.append(str(i))
    return out
