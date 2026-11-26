"""The calculus: conjecture, theorem, lemma, tactic.

A **conjecture** is the whole design requirement: the claims that must hold.
A **theorem** is one unit of proof a subagent (or a module proof) delivers:
it states the claims it discharges, names the lemmas it used, and carries the
kernel's verdict.  A **lemma** is a rule of the system that can discharge a
claim class (frame, connectivity, netlist, a fab predicate, a domain model,
the router's soundness); its check lives in the kernels, and its identity is
recorded in the certificate.  A **tactic** is a search procedure that
proposes a Step (the router, a repair operator); it is never trusted — its
output is a candidate that the gate then checks.

`discharge` is the composition check: every conjecture claim must be covered
by some theorem or lemma, the dependency graph must be acyclic, and the full
board must verify with the conjecture's obligations.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import certs as cert_mod, claims as claim_mod, gate

LEMMAS = {
    'frame': 'items outside touched are equal and carried scopes miss touched',
    'connectivity': 'a spanning tree over touching copper reaches every pad',
    'netlist': 'the board nets equal the expected netlist',
    'pads': 'every named pad exists and belongs to its net',
    'fab.clearance': 'every different-net copper pair is at least the rule apart',
    'fab.width': 'every track is at least the minimum width',
    'fab.annular': 'every via/PTH annulus is at least the rule',
    'fab.drill': 'every drill is inside the process range',
    'fab.hole_to_hole': 'every hole pair is at least the rule apart',
    'fab.edge': 'every copper item is at least the rule from the board edge',
    'router.route_sound': 'a router proposal is a Step whose after-state verifies',
    'merge.boundary': 'module boundary pads partition the board pads per net',
    'power.drop': 'a declared rail drop is derived from routed copper',
    'power.j': 'a declared current density is derived from routed copper',
    'thermal.tj': 'a junction temperature is derived from the theta chain',
    'signal.z0': 'an impedance is derived from the stackup and width',
    'signal.skew': 'a differential pair skew is derived from the routed lengths',
}


@dataclass
class Conjecture:
    id: str
    statement: str
    claims: list
    profile: str = 'lean+fab'
    deps: list = field(default_factory=list)

    def obligations(self):
        return {c['id']: c['pred'] for c in self.claims}

    def as_dict(self):
        return {'id': self.id, 'statement': self.statement,
                'profile': self.profile,
                'obligations': sorted(self.obligations())}


@dataclass
class Theorem:
    id: str
    conjecture_id: str
    claims: list
    deps: list = field(default_factory=list)
    lemma_refs: list = field(default_factory=list)
    contract: dict | None = None
    digest: str | None = None
    board_fp: str | None = None
    proof: dict | None = None

    def claim_ids(self):
        return {c['id'] for c in self.claims}

    def verify(self, st, rules, profile='lean+fab', domain_claims=()):
        cs = [c for c in cert_mod.connectivity_certs(st)
              if c['claim'] in self.claim_ids()]
        r = gate.verify(st, self.claims, cs, rules=rules, profile=profile,
                        domain_claims=domain_claims,
                        obligations={c['id']: c['pred'] for c in self.claims})
        self.proof = {'ok': r['ok'], 'summary': gate.report_summary(r),
                      'reasons': r['reasons']}
        return r

    def as_dict(self):
        return {'id': self.id, 'conjecture': self.conjecture_id,
                'claims': sorted(self.claim_ids()), 'deps': list(self.deps),
                'lemma_refs': list(self.lemma_refs), 'contract': self.contract,
                'digest': self.digest, 'board_fp': self.board_fp,
                'proof': self.proof}


@dataclass
class Tactic:
    lemma: str
    name: str
    params: dict = field(default_factory=dict)
    result: dict = field(default_factory=dict)

    def as_dict(self):
        return {'lemma': self.lemma, 'tactic': self.name,
                'params': self.params, 'result': self.result}


def _cycles(theorems):
    by = {t.id: t for t in theorems}
    seen, stack = set(), set()
    bad = []

    def visit(tid, path):
        if tid in stack:
            bad.append(path + [tid])
            return
        if tid in seen:
            return
        stack.add(tid)
        for d in by[tid].deps if tid in by else []:
            if d in by:
                visit(d, path + [tid])
        stack.discard(tid)
        seen.add(tid)

    for tid in by:
        visit(tid, [])
    return bad


def discharge(conjecture, theorems, st, rules, profile='lean+fab',
              domain_claims=(), lemmas=()):
    """Compose: coverage + acyclicity + one full verification."""
    reasons = []
    covered = set()
    for t in theorems:
        covered |= t.claim_ids()
    lemma_refs = set(lemmas)
    for t in theorems:
        lemma_refs |= set(t.lemma_refs)
    missing = set(conjecture.obligations()) - covered
    if missing:
        reasons.append('uncovered by any theorem: ' + ','.join(sorted(missing)))
    unknown_lemmas = {l for l in lemma_refs if l not in LEMMAS}
    if unknown_lemmas:
        reasons.append('unknown lemmas: ' + ','.join(sorted(unknown_lemmas)))
    cyc = _cycles(theorems)
    if cyc:
        reasons.append('dependency cycle: ' + ' -> '.join(cyc[0]))
    # the whole conjecture, with theorem claims carried where they cover
    cl = []
    for t in theorems:
        cl.extend(t.claims)
    seen = set()
    cl = [c for c in cl if not (c['id'] in seen or seen.add(c['id']))]
    cl += claim_mod.bench_claims(st)
    seen = set()
    cl = [c for c in cl if not (c['id'] in seen or seen.add(c['id']))]
    cs = [c for c in cert_mod.connectivity_certs(st)
          if c['claim'] not in covered]
    carried = [(c['id'], list(c.get('scope') or [])) for c in cl
               if c['id'] in covered]
    r = gate.verify(st, cl, cs, carried=carried, rules=rules,
                    profile=profile, domain_claims=domain_claims,
                    obligations=conjecture.obligations())
    reasons += [x for x in r['reasons'] if x not in reasons]
    return {'ok': r['ok'] and not reasons, 'reasons': reasons,
            'conjecture': conjecture.as_dict(),
            'theorems': [t.as_dict() for t in theorems],
            'lemmas': sorted(lemma_refs),
            'coverage': {'covered': sorted(covered),
                         'missing': sorted(missing)},
            'summary': gate.report_summary(r), 'digest': st['digest'],
            'report': r}
