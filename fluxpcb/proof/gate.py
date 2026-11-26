"""The Step gate: no board change commits without its proof.

`commit_step(before, after, touched, claims, certs)` runs the Lean kernel
(DAG, connectivity, netlist, frame, carried scopes, obligation coverage) and
the fab domain kernel (clearance, width, ...) and refuses the commit if any
obligation is not proven.  There is deliberately no path that writes a board
without this call; the legacy pipeline keeps its old path, but proof mode has
no bypass.

Two obligation kinds:
  * `publish`: every claim the conjecture names (full board).
  * `preserve`: only the claims that were proven before must stay proven;
    a partially routed board may not lose a proof, but need not yet have one
    for a net that is still being repaired.
"""
from __future__ import annotations

from ..kernels import fab, profile as profile_mod
from . import bridge


class GateDenied(RuntimeError):
    """The step's proof obligations are not met."""


def _pin_problems(claims, obligations, carried=()):
    """Every obligation id must be present once with its own predicate, or be
    legitimately carried (its scope is then checked by the Lean frame)."""
    problems, by = [], {}
    carried_ids = {i for i, _ in carried}
    for c in claims:
        if c['id'] in by:
            problems.append(f"duplicate claim id {c['id']}")
        by[c['id']] = c
    for cid, pred in (obligations or {}).items():
        c = by.get(cid)
        if c is None:
            if cid not in carried_ids:
                problems.append(f"missing obligation {cid}")
        elif c['pred'] != pred:
            problems.append(f"{cid}: pred {c['pred']!r} != required {pred!r}")
    return problems


def verify(st, claims, certs, before=None, touched=(), carried=(), rules=None,
           profile='lean+fab', domain_claims=(), obligations=None,
           allow_unknown=False):
    """One verification pass.  `obligations` pins id -> predicate."""
    dom_preds = {p for ps in profile_mod.DOMAIN_PREDS.values() for p in ps}
    lean_obligations = {cid: pred for cid, pred in (obligations or {}).items()
                        if pred not in fab.PREDS and pred not in dom_preds}
    lean_claims = [c for c in claims
                   if c['pred'] not in fab.PREDS and c['pred'] not in dom_preds]
    lean = bridge.run(bridge.request(st, lean_claims, certs, before=before,
                                     touched=touched, carried=carried,
                                     obligations=list(lean_obligations)))
    fab_claims = [c for c in claims if c['pred'] in fab.PREDS]
    fab_report = fab.check(st, fab_claims, rules or st.get('rules', {}))
    prof = profile_mod.parse(profile) if isinstance(profile, str) else profile
    domain_report = profile_mod.run_domain(domain_claims, prof, st)
    reasons = []
    reasons += _pin_problems(claims, obligations, carried)
    if lean.get('drift'):
        reasons.append('translation drift: the kernel rebuilt a different digest')
    if lean.get('drift_before'):
        reasons.append('before-state drift: the before digest does not match')
    if lean.get('duplicate_ids'):
        reasons.append('duplicate claim ids')
    if not lean.get('frame_ok'):
        reasons.append('frame violated: untouched items/context changed, or a '
                       'reused proof\'s scope was touched')
    if not lean.get('carried_ok'):
        reasons.append('a carried proof\'s scope intersects the touched set')
    if not lean.get('coverage_ok'):
        reasons.append('uncovered obligations: ' + ','.join(lean.get('uncovered', [])))
    for c in lean.get('claims', []):
        if c['status'] not in ('proven', 'carried', 'foreign'):
            if allow_unknown and c['status'] == 'unknown':
                continue
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
    """The obligations a publish step on this state must discharge: id -> pred."""
    from . import claims as _c
    return {c['id']: c['pred'] for c in _c.bench_claims(st)}


def commit_step(before, after, touched, claims, certs, carried=(), rules=None,
                profile='lean+fab', domain_claims=(), obligations=None):
    """Publish-style commit: the canonical obligation set must all be present.

    `obligations` may add module/domain claims (id -> pred); the gate's own
    bench obligations are always included.
    """
    want = required_claims(after)
    want.update(obligations or {})
    missing = set(want) - {c['id'] for c in claims}
    if missing:
        raise GateDenied('missing proof obligations: ' + ','.join(sorted(missing)))
    r = verify(after, claims, certs, before=before, touched=touched,
               carried=carried, rules=rules, profile=profile,
               domain_claims=domain_claims, obligations=want)
    if not r['ok']:
        raise GateDenied('; '.join(r['reasons'][:6]))
    return r


def report_summary(r):
    """Status counts over the Lean, fab, and domain kernels."""
    counts = {'proven': 0, 'carried': 0, 'derived': 0, 'unknown': 0,
              'refuted': 0, 'invalid': 0, 'foreign': 0, 'disabled': 0}
    for c in r['lean'].get('claims', []):
        counts[c['status']] = counts.get(c['status'], 0) + 1
    for c in r['fab']['claims']:
        counts[c['status']] = counts.get(c['status'], 0) + 1
    for c in r.get('domain', {}).get('claims', []):
        counts[c['status']] = counts.get(c['status'], 0) + 1
    return counts
