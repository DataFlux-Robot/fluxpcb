"""The Step gate: no board change commits without its proof.

`commit_step(before, after, touched, claims, certs)` runs the Lean kernel
(DAG, connectivity, netlist, frame, carried scopes) and the fab domain kernel
(clearance, width) and refuses the commit if any obligation is not proven.
There is deliberately no path that writes a board without this call; the
legacy pipeline keeps its old path, but proof mode has no bypass.
"""
from __future__ import annotations

from ..kernels import fab, profile as profile_mod
from . import bridge


class GateDenied(RuntimeError):
    """The step's proof obligations are not met."""


def verify(st, claims, certs, before=None, touched=(), carried=(), rules=None,
           profile='lean+fab', domain_claims=()):
    lean = bridge.run(bridge.request(st, claims, certs, before=before,
                                     touched=touched, carried=carried))
    fab_claims = [c for c in claims if c['pred'] in fab.PREDS]
    fab_report = fab.check(st, fab_claims, rules or st.get('rules', {}))
    prof = profile_mod.parse(profile) if isinstance(profile, str) else profile
    domain_report = profile_mod.run_domain(domain_claims, prof, st)
    reasons = []
    if lean.get('drift'):
        reasons.append('translation drift: the kernel rebuilt a different digest')
    if not lean.get('frame_ok'):
        reasons.append('frame violated: untouched items changed, or a reused '
                       'proof\'s scope was touched')
    if not lean.get('carried_ok'):
        reasons.append('a carried proof\'s scope intersects the touched set')
    for c in lean.get('claims', []):
        if c['status'] not in ('proven', 'foreign'):
            reasons.append(f"{c['id']}: {c['status']} {c['witness']}".strip())
    for c in fab_report['claims']:
        if c['status'] != 'proven':
            reasons.append(f"{c['id']}: {c['status']} {c['witness']}".strip())
    for c in domain_report['claims']:
        if c['status'] == 'disabled':
            continue
        if c['status'] not in ('derived', 'proven') or c.get('ok') is False:
            why = c.get('needed_information') or c.get('witness') or ''
            reasons.append(f"{c['id']}: {c['status']} {why}".strip())
    return {'ok': not reasons, 'reasons': reasons, 'lean': lean,
            'fab': fab_report, 'domain': domain_report,
            'profile': prof.name, 'digest': st.get('digest')}


def required_claims(st):
    """The obligations a step on this state must discharge, by id.

    A caller cannot make a step pass by supplying fewer claims: the gate
    computes the canonical obligation set itself and denies anything missing.
    """
    from . import claims as _c
    out = set()
    for c in _c.bench_claims(st):
        out.add(c['id'])
    return out


def commit_step(before, after, touched, claims, certs, carried=(), rules=None,
                profile='lean+fab', domain_claims=()):
    missing = required_claims(after) - {c['id'] for c in claims}
    if missing:
        raise GateDenied('missing proof obligations: ' + ','.join(sorted(missing)))
    r = verify(after, claims, certs, before=before, touched=touched,
               carried=carried, rules=rules, profile=profile,
               domain_claims=domain_claims)
    if not r['ok']:
        raise GateDenied('; '.join(r['reasons'][:6]))
    return r


def report_summary(r):
    """Status counts over the Lean, fab, and domain kernels."""
    counts = {'proven': 0, 'derived': 0, 'unknown': 0, 'refuted': 0, 'invalid': 0,
              'foreign': 0, 'disabled': 0}
    for c in r['lean'].get('claims', []):
        counts[c['status']] = counts.get(c['status'], 0) + 1
    for c in r['fab']['claims']:
        counts[c['status']] = counts.get(c['status'], 0) + 1
    for c in r.get('domain', {}).get('claims', []):
        counts[c['status']] = counts.get(c['status'], 0) + 1
    return counts
