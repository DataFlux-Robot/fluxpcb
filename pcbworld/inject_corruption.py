#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""T1-C: prove the evidence ledger refuses three known corruptions.

The three corruptions are the defect patterns that produced the original
false passes:

  A. wrong project      -- the router's rule table differs from the referee's
                           (the `.kicad_pro` that was not loaded)
  B. stale zone fill    -- a verdict whose read set has moved (499 -> 129)
  C. sub-resolution     -- 0.1524 mm represented in integer um (152 um)

Each must be refused or marked indeterminate; none may read PASS.  Also
checks gate qualification (a gate that cannot fail never passes), the
worst-first composition, and the hash chain.
"""
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, '..', 'flux-neurotransmitter'))

from fluxpcb.contract.quant import Cmp, compare_min  # noqa: E402
from fluxpcb.contract.rules import ClassRule, RuleModel  # noqa: E402
from fluxpcb.verifiers.ledger import (Claim, Gate, GateRegistry, Ledger,  # noqa: E402
                                      Outcome, default_registry)
from fluxpcb.verifiers.binding import (bind_report, check_binding,  # noqa: E402
                                       project_identity)
from fluxpcb.repair_drc import parse_violations, run_drc  # noqa: E402
from fluxpcb.verifiers.txn import (Claims, accept,  # noqa: E402
                                   transactional_repair)

results = {}


def check(name, cond, detail):
    results[name] = {'ok': bool(cond), 'detail': detail}
    print(('PASS' if cond else 'FAIL'), name, '-', detail)


# ── A. wrong project: tables diverge ──────────────────────────────────
dst = tempfile.mkdtemp(prefix='fg_')
ledger = Ledger(os.path.join(dst, 'ledger.jsonl'))
reg = default_registry()
good = RuleModel('project', ClassRule('Default', ('GND',), 254000, 254000),
                 {'Default': ClassRule('Default', ('GND',), 254000, 254000)})
bad = RuleModel('dsn', ClassRule('<default>', (), 0, 0),
                {'kicad_default': ClassRule('kicad_default', ('GND',), 200000, 200000)})
diff = good.diff(bad)
v = reg.evaluate('rule_congruence',
                 {'project_rules': {n: list(x) for n, x in good.net_rules().items()},
                  'dsn_rules': {n: list(x) for n, x in bad.net_rules().items()}})
claim = Claim('rule_congruence', 'router table equals referee table',
              mechanism='established', owner='T1-A', gate='rule_congruence')
ledger.record(claim, v)
check('A1_divergence_detected', not diff.ok, diff.summary(2))
check('A2_ledger_refuses', v.outcome is Outcome.FAIL,
      f'outcome={v.outcome.value} note={v.note}')

# the same tables after the parsed patch read back equal
fixed = RuleModel('patched', ClassRule('Default', ('GND',), 254000, 254000),
                  {'kicad_default': ClassRule('kicad_default', ('GND',), 254000, 254000)})
check('A3_patch_restores_congruence', good.diff(fixed).ok,
      good.diff(fixed).summary())

# ── B. stale zone fill: read set moved ────────────────────────────────
v_fill = reg.evaluate('fill_fresh', {'fill_board_key': 'board-A',
                                     'board_key': 'board-B',
                                     'fill_rules_key': 'rules-A',
                                     'rules_key': 'rules-A'})
check('B1_stale_fill_refused', v_fill.outcome is Outcome.FAIL,
      f'outcome={v_fill.outcome.value}')
ledger.record(Claim('fill_fresh', 'zone fill belongs to the measured state',
                    mechanism='model-only', owner='T2-h', gate='fill_fresh'),
              v_fill)
# rho staleness: a pass recorded under one read set is stale under another
v_ok = reg.evaluate('fill_fresh', {'fill_board_key': 'board-A',
                                   'board_key': 'board-A',
                                   'fill_rules_key': 'rules-A',
                                   'rules_key': 'rules-A'})
ledger.record(Claim('fill_for_A', 'fill belongs to board A',
                    mechanism='model-only', owner='T2-h', gate='fill_fresh'),
              v_ok)
fresh_same = ledger.is_fresh('fill_for_A',
                             {'fill_board_key': 'board-A', 'board_key': 'board-A',
                              'fill_rules_key': 'rules-A', 'rules_key': 'rules-A'})
fresh_moved = ledger.is_fresh('fill_for_A',
                              {'fill_board_key': 'board-A', 'board_key': 'board-B',
                               'fill_rules_key': 'rules-A', 'rules_key': 'rules-A'})
check('B2_rho_stale_detected', fresh_same and not fresh_moved,
      f'same={fresh_same} moved={fresh_moved}')

# ── C. sub-resolution comparison ──────────────────────────────────────
c_fail = compare_min(152000.0, 152400.0, 100.0)   # 152 um vs 6 mil
c_band = compare_min(152400.0, 152400.0, 100.0)   # inside the band
c_exact = compare_min(152400.0, 152400.0, 0.0)    # exact channel
check('C1_shortfall_fails', c_fail is Cmp.FAIL, f'{c_fail}')
check('C2_band_indeterminate', c_band is Cmp.INDETERMINATE, f'{c_band}')
check('C3_exact_equality_passes', c_exact is Cmp.PASS, f'{c_exact}')

# a gate that claims pass while below its acceptance condition is refused
v_lie = reg.evaluate('quantization', {'measured_nm': 152000.0,
                                      'limit_nm': 152400.0,
                                      'resolution_nm': 0.0})
check('C4_pass_held_to_condition', v_lie.outcome is Outcome.FAIL,
      f'outcome={v_lie.outcome.value} note={v_lie.note}')
ledger.record(Claim('quantization', 'channel resolution is below the margin',
                    mechanism='sourced', owner='T2-e', gate='quantization'),
              v_lie)

# ── D. qualification: a gate that cannot fail never passes ────────────
reg2 = GateRegistry()
try:
    reg2.register(Gate('no_control', lambda ctx: {'passed': True}))
    check('D1_control_mandatory', False, 'registered without a control')
except ValueError as e:
    check('D1_control_mandatory', True, str(e))


def always_pass(ctx):
    return {'passed': True}


reg2.register(Gate('always_pass', always_pass, known_good={'x': 1},
                   known_bad={'x': 0}))
v_ap = reg2.evaluate('always_pass', {'x': 1})
check('D2_unqualified_never_passes', v_ap.outcome is Outcome.UNQUALIFIED,
      f'outcome={v_ap.outcome.value} note={v_ap.note}')

# ── E. worst-first composition ────────────────────────────────────────
require = ['rule_congruence', 'fill_for_A', 'quantization']
readiness = ledger.readiness(require)
check('E1_worst_first', readiness['worst'] == 'fail',
      json.dumps(readiness['by_claim']))

# ── F. hash chain ─────────────────────────────────────────────────────
ok, why = ledger.verify_chain()
check('F1_chain_intact', ok, why)
with open(ledger.path, encoding='utf-8') as f:
    lines = f.readlines()
entry = json.loads(lines[0])
entry['outcome'] = 'pass'
lines[0] = json.dumps(entry, sort_keys=True, separators=(',', ':')) + '\n'
with open(ledger.path, 'w', encoding='utf-8') as f:
    f.writelines(lines)
ok2, why2 = ledger.verify_chain()
check('F2_tamper_detected', not ok2, why2)

# ── G. wrong-but-self-consistent project ──────────────────────────────
# Old gate: both tables equal -> PASS.  New gate: project hash mismatch.
v_old = reg.evaluate('rule_congruence',
                     {'project_rules': {'A': [150000, 150000]},
                      'dsn_rules': {'A': [150000, 150000]}})
check('G1_selfconsistent_wrong_project_passes_old_gate',
      v_old.outcome is Outcome.PASS,
      f'outcome={v_old.outcome.value} (this is the false-pass vector)')
import tempfile as _tf
_d = _tf.mkdtemp(prefix='pid_')
_pro = os.path.join(_d, 'board.kicad_pro')
open(_pro, 'w').write('{"net_settings": {}}')
_wrong = project_identity(_pro, expected_sha='0' * 64)
v_new = reg.evaluate('project_identity', _wrong)
check('G2_project_identity_closes_vector', v_new.outcome is Outcome.FAIL,
      f'outcome={v_new.outcome.value} note={v_new.note}')

# ── H. zero-error report from another board ───────────────────────────
# Old gate: n_error_violations == 0 -> PASS regardless of provenance.
v_old_drc = reg.evaluate('drc_clean', {'n_error_violations': 0})
check('H1_stale_zero_report_passes_old_gate',
      v_old_drc.outcome is Outcome.PASS,
      f'outcome={v_old_drc.outcome.value} (this is the false-pass vector)')
_board = os.path.join(_d, 'a.kicad_pcb')
_report = os.path.join(_d, 'a.drc.json')
open(_board, 'w').write('board-A')
open(_report, 'w').write('{"violations": []}')
bind_report(_board, _report)
open(_board, 'w').write('board-A-modified')  # board state moves
v_bind = reg.evaluate('report_binding', check_binding(_board, _report + '.binding.json'))
check('H2_report_binding_closes_vector', v_bind.outcome is Outcome.FAIL,
      f'outcome={v_bind.outcome.value} note={v_bind.note}')

# ── I. rule register scope ────────────────────────────────────────────
v_scope = reg.evaluate('rule_scope', {'declared_rules': 40,
                                      'represented_rules': 28})
check('I1_rule_scope_exposes_model_gap', v_scope.outcome is Outcome.FAIL,
      f'outcome={v_scope.outcome.value} measured={v_scope.measured}')

# ── J. accept(): new type and total increase ─────────────────────────
def _cl(n, t, un=0):
    return Claims(n_errors=n, n_unconnected=un,
                  error_types=frozenset([t]), type_counts=((t, n),))

ok_new, why_new = accept(_cl(100, 'track_width'), _cl(1, 'clearance'))
check('J1_new_type_rejected_even_if_weighted_falls',
      not ok_new and 'new violation types' in why_new, why_new)
ok_inc, why_inc = accept(_cl(1, 'track_width'), _cl(2, 'track_width'))
check('J2_total_increase_rejected',
      not ok_inc and 'total increased' in why_inc, why_inc)

# ── K. transaction restores on action exception ──────────────────────
_kd = _tf.mkdtemp(prefix='txn_')
_kb = os.path.join(_kd, 'b.kicad_pcb')
open(_kb, 'w').write('ORIGINAL')
def _act(path):
    open(path, 'w').write('MUTATED')
    raise RuntimeError('boom')
def _ev(path):
    return _cl(1, 'track_width')
_rep = transactional_repair(_kb, _act, _ev, max_rounds=1)
check('K1_exception_restores_snapshot',
      _rep.rolled_back == 1 and open(_kb).read() == 'ORIGINAL',
      f'rolled_back={_rep.rolled_back}')

# ── L. English DRC description parses ────────────────────────────────
v = parse_violations({'violations': [{
    'type': 'clearance',
    'description': 'clearance violation (clearance 0.2 mm; actual 0.1769 mm)',
    'items': []}]})[0]
check('L1_english_measurement_parsed',
      v.has_measurement and abs(v.actual_mm - 0.1769) < 1e-9,
      f'actual={v.actual_mm} req={v.required_mm} measured={v.has_measurement}')

# ── M. missing DRC binary is not clean; stale report removed ─────────
_md = _tf.mkdtemp(prefix='drc_')
_mb = os.path.join(_md, 'b.kicad_pcb')
open(_mb, 'w').write('x')
_mo = os.path.join(_md, 'b.drc.json')
open(_mo, 'w').write('{"violations": [], "unconnected_items": []}')
_old = os.environ.get('KICAD_CLI')
os.environ['KICAD_CLI'] = os.path.join(_md, 'no-such-kicad-cli')
_d = run_drc(_mb, _mo)
if _old is None:
    os.environ.pop('KICAD_CLI', None)
else:
    os.environ['KICAD_CLI'] = _old
check('M1_missing_drc_not_clean',
      _d.get('ok') is False and not os.path.exists(_mo),
      f"ok={_d.get('ok')} stale_removed={not os.path.exists(_mo)}")

# ── N. GAP / ASSUMED are worse than PASS ─────────────────────────────
from fluxpcb.verifiers.ledger import WORST_ORDER as _WO  # noqa: E402
check('N1_gap_assumed_precede_pass',
      _WO.index(Outcome.GAP) < _WO.index(Outcome.PASS)
      and _WO.index(Outcome.ASSUMED) < _WO.index(Outcome.PASS),
      'GAP/ASSUMED ordered before PASS')

out = os.path.join(HERE, 'inject_corruption.json')
json.dump(results, open(out, 'w'), indent=1)
n_ok = sum(1 for r in results.values() if r['ok'])
print(f'\n{n_ok}/{len(results)} checks passed -> {out}')
sys.exit(0 if n_ok == len(results) else 1)
