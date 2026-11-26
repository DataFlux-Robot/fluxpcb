"""Two-level gate for a live design.

`preserve` is the per-step gate: at every committed mutation, all fab
predicates run, and every claim that was proven before must still be proven
(or legitimately carried); a proof may not be lost, a clean predicate may not
turn dirty.  A partially routed board may keep opens — it just may not lose
what it already had.

`publish` is the full-board gate: every claim of the conjecture must be
discharged (connectivity, netlist, all fab predicates, declared domain
claims).  It is the only gate that says "this board is done".
"""
from __future__ import annotations

from ..kernels import fab, profile as profile_mod
from . import certs, claims as claim_mod, gate, state as S


def touched_items(before, after, ihb=None, iha=None):
    """Ids added, removed, or changed in place (same id, different copper)."""
    ihb = ihb if ihb is not None else S.item_hashes(before)
    iha = iha if iha is not None else S.item_hashes(after)
    return {i for i in set(ihb) | set(iha) if ihb.get(i) != iha.get(i)}


def _statuses(report, kernel='lean'):
    if kernel == 'lean':
        return {c['id']: c['status'] for c in report['lean'].get('claims', [])}
    if kernel == 'fab':
        return {c['id']: c['status'] for c in report['fab']['claims']}
    return {c['id']: c['status'] for c in report.get('domain', {}).get('claims', [])}


def _proven(rep):
    out = set()
    for kernel in ('lean', 'fab', 'domain'):
        out |= {cid for cid, st in _statuses(rep, kernel).items()
                if st in ('proven', 'carried', 'derived')}
    return out


def preserve(before, after, rules, profile='lean+fab', domain_claims=(),
             before_report=None, store=None):
    """Gate one committed mutation.  `ok=False` means roll back."""
    touched = touched_items(before, after)
    bcl = claim_mod.bench_claims(before)
    if before_report is None:
        before_report = gate.verify(before, bcl,
                                    certs.connectivity_certs(before),
                                    rules=rules, profile=profile,
                                    domain_claims=domain_claims)
    before_proven = {cid for cid, stt in _statuses(before_report, 'lean').items()
                     if stt in ('proven', 'carried')}
    before_proven |= {cid for cid, stt in _statuses(before_report, 'fab').items()
                      if stt == 'proven'}
    bscope = {c['id']: list(c.get('scope') or []) for c in bcl}
    acl = claim_mod.bench_claims(after)
    by_id = {c['id']: c for c in acl}
    missing = sorted(before_proven - set(by_id))
    if missing:
        return {'ok': False, 'reasons': [f'{m}: claim disappeared' for m in missing],
                'touched': sorted(touched), 'summary': {},
                'before_digest': before['digest'],
                'after_digest': after['digest']}

    after_certs = {c['claim']: c for c in certs.connectivity_certs(after)}
    keep, carried, trouble = [], [], []
    for cid in sorted(before_proven):
        c = by_id[cid]
        keep.append(c)
        if c['pred'] == 'connected':
            scope = set(c.get('scope') or [])
            same = scope == set(bscope.get(cid, [])) and not (scope & touched)
            if same:
                carried.append((cid, list(c.get('scope') or [])))
            elif cid not in after_certs:
                trouble.append(f'{cid}: no connectivity certificate after the step')
    carried_ids = {i for i, _ in carried}
    certs_keep = [after_certs[c['id']] for c in keep
                  if c['pred'] == 'connected' and c['id'] in after_certs
                  and c['id'] not in carried_ids]
    obligations = {cid: by_id[cid]['pred'] for cid in before_proven}
    r = gate.verify(after, keep, certs_keep, before=before,
                    touched=sorted(touched), carried=carried, rules=rules,
                    profile=profile, domain_claims=(),
                    obligations=obligations)
    reasons = trouble + list(r['reasons'])

    # every process predicate runs, even on a board that is still dirty; only
    # a *regression* (proven -> refuted) or a new violation class denies.
    fab_all = [c for c in acl if c['pred'] in fab.PREDS]
    fab_after = {c['id']: c for c in fab.check(after, fab_all, rules)['claims']}
    fab_before = _statuses(before_report, 'fab')
    for cid, c in fab_after.items():
        if fab_before.get(cid) == 'proven' and c['status'] != 'proven':
            reasons.append(f"{cid}: a proven process predicate became dirty "
                           f"({c['witness']})")
    prof = profile_mod.parse(profile) if isinstance(profile, str) else profile
    dom_after = profile_mod.run_domain(domain_claims, prof, after)
    dom_before = _statuses(before_report, 'domain')
    for c in dom_after['claims']:
        if c['status'] == 'disabled':
            continue
        if dom_before.get(c['id']) == 'derived' and c['status'] not in ('derived',):
            reasons.append(f"{c['id']}: a derived domain claim regressed")

    ok = not reasons
    rows = {c['id']: c for c in keep}
    rows.update(fab_after)
    summary = {k: 0 for k in ('proven', 'carried', 'derived', 'unknown',
                              'refuted', 'invalid', 'foreign', 'disabled')}
    for c in r['lean'].get('claims', []):
        summary[c['status']] = summary.get(c['status'], 0) + 1
    for c in fab_after.values():
        summary[c['status']] = summary.get(c['status'], 0) + 1
    for c in dom_after['claims']:
        summary[c['status']] = summary.get(c['status'], 0) + 1
    if ok and store is not None:
        ih = S.item_hashes(after)
        for cid, c in rows.items():
            stt = (fab_after[cid]['status'] if cid in fab_after
                   else _statuses(r, 'lean').get(cid))
            if stt == 'proven':
                store.put(c, stt, '', after, ih)
    return {'ok': ok, 'reasons': reasons,
            'before_digest': before['digest'], 'after_digest': after['digest'],
            'touched': sorted(touched), 'carried': [i for i, _ in carried],
            'summary': summary, 'report': r, 'fab': fab_after,
            'domain': dom_after['claims']}


def publish(after, rules, profile='lean+fab', domain_claims=(), store=None):
    """Full-board gate: every claim must hold."""
    cl = claim_mod.bench_claims(after)
    cs = certs.connectivity_certs(after)
    obligations = {c['id']: c['pred'] for c in cl}
    r = gate.verify(after, cl, cs, rules=rules, profile=profile,
                    domain_claims=domain_claims, obligations=obligations)
    if r['ok'] and store is not None:
        ih = S.item_hashes(after)
        for c in cl:
            stt = _statuses(r, 'fab' if c['pred'] in fab.PREDS else 'lean').get(c['id'])
            if stt == 'proven':
                store.put(c, stt, '', after, ih)
    return {'ok': r['ok'], 'reasons': r['reasons'], 'digest': after['digest'],
            'summary': gate.report_summary(r), 'report': r}


def audit(after, rules, profile='lean+fab', domain_claims=()):
    """Record a certificate without denying (a router candidate is not a design)."""
    cl = claim_mod.bench_claims(after)
    cs = certs.connectivity_certs(after)
    r = gate.verify(after, cl, cs, rules=rules, profile=profile,
                    domain_claims=domain_claims)
    return {'ok': r['ok'], 'reasons': r['reasons'], 'digest': after['digest'],
            'summary': gate.report_summary(r)}
