"""Two-level gate for a live design.

`preserve` is the per-step gate: at every committed mutation, all fab
predicates run, and every claim that was proven before must still be proven
(or legitimately carried); a proof may not be lost, a clean predicate may not
turn dirty.  A partially routed board may keep opens — it just may not lose
what it already had, so `allow_unknown` lets a not-yet-proven net stay
unknown while obligations pin the ones that were proven.

`publish` is the full-board gate: every claim of the conjecture must be
discharged (connectivity, netlist, all fab predicates, declared domain
claims).  It is the only gate that says "this board is done".

The module-level cache keeps the last verified state per (path, bytes): the
route audit seeds it, each committed step refreshes it, so a repair loop does
not re-extract and re-verify the same board over and over.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from ..kernels import fab, profile as profile_mod
from . import certs, claims as claim_mod, gate, state as S

_CACHE = {}


def _fp(board_path):
    try:
        return hashlib.sha256(Path(board_path).read_bytes()).hexdigest()[:16]
    except OSError:
        return ''


def cache_key(board_path):
    return (str(Path(board_path).resolve()), _fp(board_path))


def cached(board_path):
    e = _CACHE.get(cache_key(board_path))
    return (e['state'], e['report']) if e else (None, None)


def seed(board_path, st, report, fp=None):
    _CACHE[(str(Path(board_path).resolve()), fp or _fp(board_path))] = {
        'state': st, 'report': report}


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


def _report(lean, fab_claims, dom_claims):
    return {'lean': lean,
            'fab': {'claims': list(fab_claims),
                    'ok': all(c['status'] == 'proven' for c in fab_claims)},
            'domain': {'claims': list(dom_claims),
                       'ok': all(c['status'] in ('derived', 'disabled')
                                 for c in dom_claims)}}


def preserve(before, after, rules, profile='lean+fab', domain_claims=(),
             before_report=None, store=None, board_path=None):
    """Gate one committed mutation.  `ok=False` means roll back."""
    touched = touched_items(before, after)
    bcl = claim_mod.bench_claims(before)
    if before_report is None:
        before_report = gate.verify(before, bcl,
                                    certs.connectivity_certs(before),
                                    rules=rules, profile=profile,
                                    domain_claims=domain_claims)
    before_lean = {cid for cid, stt in _statuses(before_report, 'lean').items()
                   if stt in ('proven', 'carried')}
    before_fab = {cid for cid, stt in _statuses(before_report, 'fab').items()
                  if stt == 'proven'}
    before_proven = before_lean | before_fab
    bscope = {c['id']: list(c.get('scope') or []) for c in bcl}
    acl = claim_mod.bench_claims(after)
    by_id = {c['id']: c for c in acl}
    missing = sorted(before_proven - set(by_id))
    if missing:
        return {'ok': False,
                'reasons': [f'{m}: claim disappeared' for m in missing],
                'touched': sorted(touched), 'summary': {},
                'before_digest': before['digest'],
                'after_digest': after['digest']}

    after_certs = {c['claim']: c for c in certs.connectivity_certs(after)}
    carried, trouble = [], []
    for cid in sorted(before_lean):
        c = by_id[cid]
        if c['pred'] != 'connected':
            continue
        scope = set(c.get('scope') or [])
        same = scope == set(bscope.get(cid, [])) and not (scope & touched)
        if same:
            carried.append((cid, list(c.get('scope') or [])))
        elif cid not in after_certs:
            trouble.append(f'{cid}: no connectivity certificate after the step')
    carried_ids = {i for i, _ in carried}
    lean_claims = [c for c in acl if c['pred'] not in fab.PREDS]
    cs = [after_certs[c['id']] for c in lean_claims
          if c['pred'] == 'connected' and c['id'] in after_certs
          and c['id'] not in carried_ids]
    lean_obligations = {cid: by_id[cid]['pred'] for cid in before_lean}
    r = gate.verify(after, lean_claims, cs, before=before,
                    touched=sorted(touched), carried=carried, rules=rules,
                    profile=profile, domain_claims=(),
                    obligations=lean_obligations, allow_unknown=True)
    reasons = trouble + list(r['reasons'])

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
        if dom_before.get(c['id']) == 'derived' and c['status'] != 'derived':
            reasons.append(f"{c['id']}: a derived domain claim regressed")

    ok = not reasons
    report = _report(r['lean'], list(fab_after.values()), dom_after['claims'])
    summary = {k: 0 for k in ('proven', 'carried', 'derived', 'unknown',
                              'refuted', 'invalid', 'foreign', 'disabled')}
    for c in report['lean'].get('claims', []):
        summary[c['status']] = summary.get(c['status'], 0) + 1
    for c in fab_after.values():
        summary[c['status']] = summary.get(c['status'], 0) + 1
    for c in dom_after['claims']:
        summary[c['status']] = summary.get(c['status'], 0) + 1
    if ok and store is not None:
        ih = S.item_hashes(after)
        for c in report['lean'].get('claims', []):
            if c['status'] == 'proven':
                store.put(by_id[c['id']], c['status'], '', after, ih)
    if board_path is not None and not missing:
        seed(board_path, after, report)
    return {'ok': ok, 'reasons': reasons,
            'before_digest': before['digest'], 'after_digest': after['digest'],
            'touched': sorted(touched), 'carried': [i for i, _ in carried],
            'summary': summary, 'report': report, 'fab': fab_after,
            'domain': dom_after['claims']}


def publish(after, rules, profile='lean+fab', domain_claims=(), store=None,
            board_path=None, process=None, robust=None):
    """Full-board gate: every claim must hold."""
    cl = claim_mod.bench_claims(after)
    if process:
        cl += claim_mod.process_claims(after, process)
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
    if board_path is not None:
        seed(board_path, after, {'lean': r['lean'], 'fab': r['fab'],
                                 'domain': r['domain']})
    out = {'ok': r['ok'], 'reasons': list(r['reasons']),
           'digest': after['digest'], 'summary': gate.report_summary(r),
           'report': r}
    if robust:
        from . import process as proc
        eff = dict(rules)
        if process:
            eff.update(S.process_rules(process))
        pr = proc.margins(after, eff, robust)
        out['margins'] = pr
        if not pr['ok']:
            out['ok'] = False
            out['reasons'] += pr['reasons']
    from . import repair as repair_mod
    obl = repair_mod.obligations(r)
    if robust:
        obl += repair_mod.margin_obligations(out.get('margins'))
    out['obligations'] = obl
    out['measure'] = repair_mod.measure(obl)
    return out


def audit(after, rules, profile='lean+fab', domain_claims=(), board_path=None):
    """Record a certificate without denying (a router candidate is not a design)."""
    cl = claim_mod.bench_claims(after)
    cs = certs.connectivity_certs(after)
    r = gate.verify(after, cl, cs, rules=rules, profile=profile,
                    domain_claims=domain_claims)
    if board_path is not None:
        seed(board_path, after, {'lean': r['lean'], 'fab': r['fab'],
                                 'domain': r['domain']})
    out = {'ok': r['ok'], 'reasons': list(r['reasons']),
           'digest': after['digest'], 'summary': gate.report_summary(r),
           'report': r}
    if robust:
        from . import process as proc
        eff = dict(rules)
        if process:
            eff.update(S.process_rules(process))
        pr = proc.margins(after, eff, robust)
        out['margins'] = pr
        if not pr['ok']:
            out['ok'] = False
            out['reasons'] += pr['reasons']
    from . import repair as repair_mod
    obl = repair_mod.obligations(r)
    if robust:
        obl += repair_mod.margin_obligations(out.get('margins'))
    out['obligations'] = obl
    out['measure'] = repair_mod.measure(obl)
    return out
