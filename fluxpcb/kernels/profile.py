"""Kernel profiles: which domain kernels a run requires.

`lean+fab` is the everyday profile (bench runs); `full` adds power/thermal/
signal.  A disabled kernel's claims are `disabled`, never silently dropped,
and the Step gate only holds the run to the profile it declares.  A domain
result is `derived` (a checked calculation over declared inputs) or `refuted`
(it violates its limit) — never `proven`.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import derive

DOMAIN_PREDS = {
    'power': ('power.drop', 'power.j'),
    'thermal': ('thermal.tj',),
    'signal': ('signal.z0',),
}
PROFILES = {
    'lean+fab': ('lean', 'fab'),
    'full': ('lean', 'fab', 'power', 'thermal', 'signal'),
}


@dataclass
class Profile:
    name: str
    kernels: tuple = field(default_factory=tuple)

    def has(self, k):
        return k in self.kernels


def parse(name):
    if name not in PROFILES:
        raise ValueError(f'unknown profile {name}; known: {sorted(PROFILES)}')
    return Profile(name=name, kernels=PROFILES[name])


def _domain_of(pred):
    for dom, preds in DOMAIN_PREDS.items():
        if pred in preds:
            return dom
    return None


def _run_one(c):
    pred = c['pred']
    p = dict(c.get('params') or {})
    if pred == 'power.drop':
        d = derive.power_drop(p.get('current_a'), p.get('length_mm'),
                              p.get('width_mm'), p.get('t_mm', 0.035),
                              p.get('v_rail'))
    elif pred == 'power.j':
        cur, w = p.get('current_a'), p.get('width_mm')
        t = p.get('t_mm', 0.035)
        if not cur or not w:
            d = {'status': 'unknown',
                 'needed_information': [k for k, v in
                                        (('current_a', cur), ('width_mm', w))
                                        if not v]}
        else:
            j = cur / (w * t)
            d = {'status': 'derived', 'measured': round(j, 2),
                 'units': 'A/mm2',
                 'evidence': {'model': 'J = I/(w*t)', 'model_version': '1',
                              'inputs': {'I': cur, 'w': w, 't': t},
                              'inputs_hash': f'{cur}-{w}-{t}',
                              'result': round(j, 6)}}
    elif pred == 'thermal.tj':
        d = derive.thermal_tj(p.get('power_w'), p.get('theta_ja'),
                              p.get('theta_jc'), p.get('theta_cs'),
                              p.get('theta_sa'), p.get('t_amb', 25.0),
                              p.get('sink', False))
    elif pred == 'signal.z0':
        d = derive.z0_microstrip(p.get('width_mm'), p.get('h_mm'),
                                 p.get('er'), p.get('t_mm', 0.035))
        target = p.get('target_ohm')
        if d['status'] == 'derived' and target:
            tol = p.get('tol_pct', 10.0)
            d['limit'] = target
            d['dev_pct'] = round(100 * (d['measured'] - target) / target, 2)
            d['ok'] = abs(d['dev_pct']) <= tol
    else:
        d = {'status': 'unknown', 'needed_information': [f'no derivation for {pred}']}
    limit = c.get('limit') if c.get('limit') is not None else p.get('limit')
    if d.get('status') == 'derived' and limit is not None:
        over = d['measured'] > limit if c.get('pred') != 'signal.z0' \
            else not d.get('ok', True)
        if over:
            d['status'] = 'refuted'
        d.setdefault('ok', not over)
    out = {'id': c['id'], 'pred': pred, 'kernel': _domain_of(pred)}
    out.update(d)
    out.setdefault('units', '')
    out.setdefault('measured', None)
    return out


def run_domain(claims, profile, state=None):
    rows, ok = [], True
    for c in claims or []:
        dom = _domain_of(c.get('pred'))
        if dom is None:
            continue
        if not profile.has(dom):
            rows.append({'id': c['id'], 'pred': c['pred'], 'kernel': dom,
                         'status': 'disabled', 'measured': None, 'units': '',
                         'witness': f'{dom} kernel disabled by profile {profile.name}'})
            continue
        r = _run_one(c)
        rows.append(r)
        if r['status'] not in ('derived', 'proven') or \
                (r.get('ok') is False):
            ok = False
    return {'claims': rows, 'ok': ok,
            'profile': profile.name, 'kernels': list(profile.kernels)}
