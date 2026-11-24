"""ledger — a falsification-gated evidence ledger.

Three rules, borrowed from nopekit and from the evaluation literature:

1. **A pass does not count until the gate has demonstrated it can fail.**
   Every gate ships a sealed known-good and a sealed known-bad fixture; the
   fixtures state everything the gate reads, so a control cannot pass in one
   repository and fail in another.  An unqualified gate yields UNQUALIFIED,
   never PASS.

2. **A verdict is keyed by the hash of everything it read (rho).**  Change
   the board, the rules, the tool version, or a fixture, and the old verdict
   no longer applies: the claim reads STALE, not Checked.  Silent divergence
   becomes a hash mismatch rather than a judgement call.

3. **Composition is worst-first, and sub-resolution comparisons are
   INDETERMINATE.**  A single Failing claim makes the requirement Failing;
   there is no arithmetic in which 78 unconnected items average away.  When
   the measured value and the limit differ by less than the channel's
   resolution, the ledger refuses to decide.

The ledger is append-only and hash-chained; ``digest`` covers every field
except itself and ``prev`` names the previous entry.
"""
from __future__ import annotations

import hashlib
import inspect
import json
import os
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Optional

from ..contract.quant import Cmp, compare_min


class Outcome(str, Enum):
    FAIL = 'fail'
    ERROR = 'error'
    SKIPPED = 'skipped'
    GAP = 'gap'                 # no qualified gate covers the claim
    INDETERMINATE = 'indeterminate'
    UNQUALIFIED = 'unqualified'
    STALE = 'stale'
    ASSUMED = 'assumed'         # depends on an unverified premise
    UNKNOWN = 'unknown'
    PASS = 'pass'


# Worst first.  A pass never hides a worse sibling.
WORST_ORDER = [Outcome.FAIL, Outcome.ERROR, Outcome.SKIPPED, Outcome.GAP,
               Outcome.INDETERMINATE, Outcome.UNQUALIFIED, Outcome.STALE,
               Outcome.ASSUMED, Outcome.UNKNOWN, Outcome.PASS]
_RANK = {o: i for i, o in enumerate(WORST_ORDER)}


def worst(outcomes) -> Outcome:
    outs = [o for o in outcomes if o is not None]
    if not outs:
        return Outcome.UNKNOWN
    return min(outs, key=lambda o: _RANK[o])


def canonical_json(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(',', ':'),
                      default=str)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def file_sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def rho(gate_name: str, version: str, ctx: dict) -> str:
    """Read-set content address, computed by the harness, never the gate."""
    return sha256_text(canonical_json({'gate': gate_name, 'version': version,
                                       'ctx': ctx}))


@dataclass
class Verdict:
    outcome: Outcome
    gate: str = ''
    basis: str = 'model-only'
    measured: Optional[float] = None
    limit: Optional[float] = None
    units: str = ''
    rho: str = ''
    note: str = ''
    at: float = field(default_factory=time.time)

    def as_dict(self) -> dict:
        return {'outcome': self.outcome.value, 'gate': self.gate,
                'basis': self.basis, 'measured': self.measured,
                'limit': self.limit, 'units': self.units, 'rho': self.rho,
                'note': self.note, 'at': self.at}


@dataclass
class Gate:
    """A check that must prove it can fail before its passes count."""
    name: str
    fn: Callable[[dict], dict]
    basis: str = 'model-only'
    known_good: dict = None
    known_bad: dict = None
    version: str = '1'
    # {'limit_nm': float, 'resolution_nm': float, 'direction': 'min'}
    acceptance: dict = None

    def code_rho(self) -> str:
        try:
            src = inspect.getsource(self.fn)
        except (OSError, TypeError):
            src = self.fn.__qualname__
        return sha256_text(src)[:16]


@dataclass
class Qualification:
    ok: bool = False
    reasons: list = field(default_factory=list)
    rho: str = ''
    code_rho: str = ''

    def as_dict(self) -> dict:
        return {'ok': self.ok, 'reasons': self.reasons, 'rho': self.rho,
                'code_rho': self.code_rho}


class GateRegistry:
    def __init__(self):
        self.gates = {}
        self.quals = {}

    def register(self, gate: Gate) -> 'GateRegistry':
        if gate.known_good is None or gate.known_bad is None:
            raise ValueError(f'gate {gate.name}: negative control is mandatory')
        self.gates[gate.name] = gate
        return self

    def qualify(self, name: str) -> Qualification:
        g = self.gates[name]
        q = Qualification(code_rho=g.code_rho())
        try:
            good = self._run(g, g.known_good)
            bad = self._run(g, g.known_bad)
        except Exception as e:
            q.reasons.append(f'control raised: {e}')
            self.quals[name] = q
            return q
        if good.outcome is not Outcome.PASS:
            q.reasons.append(f'known-good did not pass ({good.outcome.value}: {good.note})')
        if bad.outcome is not Outcome.FAIL:
            q.reasons.append(f'known-bad did not fail ({bad.outcome.value}: {bad.note})')
        q.ok = not q.reasons
        q.rho = rho(name, g.version, {'good': g.known_good, 'bad': g.known_bad,
                                      'code': q.code_rho})
        self.quals[name] = q
        return q

    def _run(self, g: Gate, ctx: dict) -> Verdict:
        ctx_rho = rho(g.name, g.version, ctx)
        try:
            res = g.fn(ctx) or {}
        except Exception as e:
            return Verdict(Outcome.ERROR, gate=g.name, basis=g.basis,
                           rho=ctx_rho, note=f'gate raised: {e}')
        passed = res.get('passed')
        measured = res.get('measured')
        note = res.get('note', '')
        if passed is None:
            return Verdict(Outcome.ERROR, gate=g.name, basis=g.basis,
                           measured=measured, rho=ctx_rho, note=note or 'no verdict')
        outcome = Outcome.PASS if passed else Outcome.FAIL
        limit = units = None
        if g.acceptance and measured is not None:
            # The acceptance condition belongs to the claim, not the gate:
            # a gate may read it, never author it.
            limit = ctx.get('limit_nm', g.acceptance.get('limit_nm'))
            units = ctx.get('units', g.acceptance.get('units', 'nm'))
            res_nm = ctx.get('resolution_nm',
                             g.acceptance.get('resolution_nm', 0.0))
            direction = g.acceptance.get('direction', 'min')
            if direction == 'min':
                c = compare_min(measured, limit, res_nm)
            else:
                c = compare_min(limit, measured, res_nm)
            if c is Cmp.INDETERMINATE:
                outcome = Outcome.INDETERMINATE
            elif passed and c is Cmp.FAIL:
                # nopekit: a pass is held to the condition it read.
                outcome = Outcome.FAIL
                note = (note + ' | passed but does not meet acceptance '
                        f'({measured} vs {limit} {units})').strip(' |')
            elif not passed:
                outcome = Outcome.FAIL
        return Verdict(outcome, gate=g.name, basis=g.basis, measured=measured,
                       limit=limit, units=units or '', rho=ctx_rho, note=note)

    def evaluate(self, name: str, ctx: dict) -> Verdict:
        if name not in self.gates:
            return Verdict(Outcome.ERROR, gate=name, note='gate not registered')
        g = self.gates[name]
        q = self.quals.get(name)
        if q is None:
            q = self.qualify(name)
        if not q.ok:
            return Verdict(Outcome.UNQUALIFIED, gate=name, basis=g.basis,
                           rho=rho(name, g.version, ctx),
                           note='; '.join(q.reasons[:2]))
        return self._run(g, ctx)


@dataclass
class Claim:
    """An acceptance condition owned outside the evaluator."""
    id: str
    statement: str
    quantity: str = ''
    mechanism: str = 'model-only'
    owner: str = ''
    gate: str = ''
    limit_nm: Optional[float] = None
    resolution_nm: float = 0.0
    units: str = 'nm'
    scope_id: str = ''       # the verdict's scope (project/dsn/register/tools)
    rule_id: str = ''        # stable register id, when the claim is a rule
    source: str = ''         # where the rule/limit came from


class Ledger:
    def __init__(self, path: str):
        self.path = path
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self._last_digest = ''
        self._last_line = None
        if os.path.exists(path):
            with open(path, encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if line:
                        self._last_line = json.loads(line)
                        self._last_digest = self._last_line.get('digest', '')

    def _append(self, entry: dict) -> dict:
        entry['prev'] = self._last_digest
        body = dict(entry)
        entry['digest'] = sha256_text(canonical_json(body))
        with open(self.path, 'a', encoding='utf-8') as f:
            f.write(canonical_json(entry) + '\n')
        self._last_digest = entry['digest']
        self._last_line = entry
        return entry

    def record(self, claim: Claim, verdict: Verdict) -> dict:
        return self._append({
            'claim': claim.id,
            'statement': claim.statement,
            'mechanism': claim.mechanism,
            'owner': claim.owner,
            'quantity': claim.quantity,
            'limit_nm': claim.limit_nm,
            'resolution_nm': claim.resolution_nm,
            'units': claim.units,
            'scope_id': claim.scope_id,
            'rule_id': claim.rule_id,
            'source': claim.source,
            'gate': verdict.gate,
            'basis': verdict.basis,
            'outcome': verdict.outcome.value,
            'measured': verdict.measured,
            'limit': verdict.limit,
            'units': verdict.units,
            'rho': verdict.rho,
            'note': verdict.note,
            'at': verdict.at,
        })

    def entries(self) -> list:
        out = []
        if not os.path.exists(self.path):
            return out
        with open(self.path, encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line:
                    out.append(json.loads(line))
        return out

    def status(self, claim_id: str) -> Outcome:
        outs = [Outcome(e['outcome']) for e in self.entries()
                if e.get('claim') == claim_id]
        return worst(outs) if outs else Outcome.UNKNOWN

    def worst(self, claim_ids) -> Outcome:
        return worst(self.status(c) for c in claim_ids)

    def readiness(self, claim_ids=None) -> dict:
        ents = self.entries()
        ids = sorted({e['claim'] for e in ents}) if claim_ids is None else list(claim_ids)
        by = {c: self.status(c).value for c in ids}
        counts = {}
        for e in ents:
            if e['claim'] in ids:
                counts[e['outcome']] = counts.get(e['outcome'], 0) + 1
        return {'worst': worst(Outcome(v) for v in by.values()).value if by else 'unknown',
                'by_claim': by, 'counts': counts}

    def verify_chain(self) -> tuple:
        prev = ''
        for i, e in enumerate(self.entries()):
            body = {k: v for k, v in e.items() if k != 'digest'}
            want = sha256_text(canonical_json(body))
            if e.get('digest') != want:
                return False, f'entry {i}: digest mismatch'
            if e.get('prev', '') != prev:
                return False, f'entry {i}: prev mismatch'
            prev = e['digest']
        return True, 'ok'

    def is_fresh(self, claim_id: str, current_ctx: dict, gate_version: str = '1') -> bool:
        ents = [e for e in self.entries() if e.get('claim') == claim_id]
        if not ents:
            return False
        cur = rho(ents[-1].get('gate', ''), gate_version, current_ctx)
        return ents[-1].get('rho') == cur


# ──────────────────────────────────────────────────────────────────────
# reusable gates for the PCB pipeline
# ──────────────────────────────────────────────────────────────────────

def gate_rule_congruence(ctx: dict) -> dict:
    """The router's rule table equals the referee's, exhaustively."""
    if 'verified' in ctx:
        return {'passed': bool(ctx['verified']),
                'note': 'read-back verified' if ctx['verified']
                else 'read-back mismatch'}
    a = ctx.get('project_rules') or {}
    b = ctx.get('dsn_rules') or {}
    if not a or not b:
        return {'passed': None, 'note': 'missing rule table'}
    mism = [k for k in set(a) | set(b) if a.get(k) != b.get(k)]
    return {'passed': not mism, 'measured': float(len(mism)),
            'note': f'{len(mism)} net mismatches' if mism else 'tables equal'}


def gate_drc_report_integrity(ctx: dict) -> dict:
    """Two-channel check on the referee: the report exists and parses."""
    if not ctx.get('report_exists'):
        return {'passed': False, 'note': 'no DRC report'}
    if ctx.get('parse_error'):
        return {'passed': False, 'note': f"report parse error: {ctx['parse_error']}"}
    n = ctx.get('n_error_violations', 0)
    return {'passed': True, 'measured': float(n),
            'note': f'{n} error-severity violations'}


def gate_drc_clean(ctx: dict) -> dict:
    """The referee's error count is zero."""
    n = ctx.get('n_error_violations')
    if n is None:
        return {'passed': None, 'note': 'no count'}
    return {'passed': n == 0, 'measured': float(n),
            'note': f'{n} error violations'}


def gate_project_identity(ctx: dict) -> dict:
    """The project beside the board is the one the manifest expects.

    ``rule_congruence`` compares two tables; two self-consistent wrong
    tables agree.  This gate closes that vector by comparing the project
    bytes to the expected hash.
    """
    want = ctx.get('expected_project_sha')
    got = ctx.get('actual_project_sha')
    if not want or not got:
        return {'passed': False, 'note': 'missing project hash'}
    return {'passed': want == got, 'note': 'project identity matches'
            if want == got else 'project identity mismatch'}


def gate_report_binding(ctx: dict) -> dict:
    """The DRC report was produced from *this* board.

    A zero-error report from another board (or an earlier state) passes
    ``drc_clean``; binding the report to the board hash closes that vector.
    """
    if not ctx.get('report_exists'):
        return {'passed': False, 'note': 'no report'}
    board = ctx.get('board_sha')
    bound = ctx.get('binding_board_sha')
    if not board or not bound:
        return {'passed': False, 'note': 'missing binding'}
    same = board == bound
    return {'passed': same, 'note': 'report bound to board' if same
            else 'report belongs to another board/state'}


def gate_rule_scope(ctx: dict) -> dict:
    """Fraction of the declared rule register the model can represent.

    Prefers ID sets (auditable) over counts (a number someone typed).
    """
    dec_ids = ctx.get('declared_rule_ids')
    rep_ids = ctx.get('represented_rule_ids')
    if dec_ids is not None and rep_ids is not None:
        declared = len(set(dec_ids))
        represented = len(set(dec_ids) & set(rep_ids))
        if not declared:
            return {'passed': False, 'note': 'empty rule register'}
        frac = represented / declared
        return {'passed': True, 'measured': frac,
                'note': f'{represented}/{declared} rules representable (ids)'}
    declared = ctx.get('declared_rules')
    represented = ctx.get('represented_rules')
    if not declared or represented is None:
        return {'passed': False, 'note': 'no rule register'}
    frac = float(represented) / float(declared)
    return {'passed': True, 'measured': frac,
            'note': f'{represented}/{declared} rules representable'}


def gate_unconnected_zero(ctx: dict) -> dict:
    """KiCad's own connectivity finds no missing connection."""
    n = ctx.get('n_unconnected')
    if n is None:
        return {'passed': None, 'note': 'no connectivity count'}
    return {'passed': n == 0, 'measured': float(n),
            'note': f'{n} unconnected items'}


def gate_error_set_monotone(ctx: dict) -> dict:
    before = set(ctx.get('before_types') or [])
    after = set(ctx.get('after_types') or [])
    new = sorted(after - before)
    return {'passed': not new, 'measured': float(len(new)),
            'note': f'new types {new}' if new else 'no new types'}


def gate_quantization(ctx: dict) -> dict:
    measured = ctx.get('measured_nm')
    limit = ctx.get('limit_nm')
    if measured is None or limit is None:
        return {'passed': None, 'note': 'missing measurement'}
    return {'passed': True, 'measured': float(measured),
            'note': f'limit {limit}'}


def gate_fill_fresh(ctx: dict) -> dict:
    same = (ctx.get('fill_board_key') == ctx.get('board_key')
            and ctx.get('fill_rules_key') == ctx.get('rules_key'))
    return {'passed': bool(same), 'note': 'fill key matches' if same
            else 'fill belongs to another state'}


def default_registry() -> GateRegistry:
    """Gates with sealed fixtures; the fixtures are the seal (no host reads)."""
    reg = GateRegistry()
    reg.register(Gate(
        'rule_congruence', gate_rule_congruence, basis='model-only',
        known_good={'project_rules': {'A': [200000, 200000]},
                    'dsn_rules': {'A': [200000, 200000]}},
        known_bad={'project_rules': {'A': [200000, 200000]},
                   'dsn_rules': {'A': [200000, 150000]}}))
    reg.register(Gate(
        'drc_report_integrity', gate_drc_report_integrity, basis='established',
        known_good={'report_exists': True, 'n_error_violations': 0},
        known_bad={'report_exists': False}))
    reg.register(Gate(
        'project_identity', gate_project_identity, basis='established',
        known_good={'expected_project_sha': 'a' * 64, 'actual_project_sha': 'a' * 64},
        known_bad={'expected_project_sha': 'a' * 64, 'actual_project_sha': 'b' * 64}))
    reg.register(Gate(
        'report_binding', gate_report_binding, basis='established',
        known_good={'report_exists': True, 'board_sha': 'c' * 64,
                    'binding_board_sha': 'c' * 64},
        known_bad={'report_exists': True, 'board_sha': 'c' * 64,
                   'binding_board_sha': 'd' * 64}))
    reg.register(Gate(
        'rule_scope', gate_rule_scope, basis='sourced',
        known_good={'declared_rules': 40, 'represented_rules': 40},
        known_bad={'declared_rules': 40, 'represented_rules': 28},
        acceptance={'limit_nm': 0.9, 'resolution_nm': 0.0,
                    'direction': 'min', 'units': 'fraction'}))
    reg.register(Gate(
        'drc_clean', gate_drc_clean, basis='established',
        known_good={'n_error_violations': 0},
        known_bad={'n_error_violations': 2}))
    reg.register(Gate(
        'unconnected_zero', gate_unconnected_zero, basis='established',
        known_good={'n_unconnected': 0},
        known_bad={'n_unconnected': 3}))
    reg.register(Gate(
        'error_set_monotone', gate_error_set_monotone, basis='model-only',
        known_good={'before_types': ['clearance'], 'after_types': ['clearance']},
        known_bad={'before_types': ['clearance'], 'after_types': ['clearance', 'short']}))
    reg.register(Gate(
        'quantization', gate_quantization, basis='sourced',
        known_good={'measured_nm': 200100.0, 'limit_nm': 200000.0},
        known_bad={'measured_nm': 199900.0, 'limit_nm': 200000.0},
        acceptance={'limit_nm': 200000.0, 'resolution_nm': 100.0,
                    'direction': 'min', 'units': 'nm'}))
    reg.register(Gate(
        'fill_fresh', gate_fill_fresh, basis='model-only',
        known_good={'fill_board_key': 'b', 'board_key': 'b',
                    'fill_rules_key': 'r', 'rules_key': 'r'},
        known_bad={'fill_board_key': 'b', 'board_key': 'b2',
                   'fill_rules_key': 'r', 'rules_key': 'r'}))
    for name in list(reg.gates):
        reg.qualify(name)
    return reg
