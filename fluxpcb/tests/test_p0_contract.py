"""P0 regression tests: fail-closed measurement, transaction rollback,
new-type rejection, and report/project binding.

Run from the repository root:
    python -m pytest fluxpcb/tests -q
"""
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from fluxpcb.repair_drc import parse_violations, run_drc  # noqa: E402
from fluxpcb.verifiers.binding import (check_binding, project_identity,  # noqa: E402
                                       bind_report)
from fluxpcb.verifiers.ledger import (Outcome, default_registry,  # noqa: E402
                                      WORST_ORDER)
from fluxpcb.verifiers.txn import (Claims, accept, transactional_repair)  # noqa: E402


def _claims(n=0, types=(), un=0):
    counts = tuple(types)
    return Claims(n_errors=n, n_unconnected=un,
                  error_types=frozenset(t for t, _ in counts),
                  type_counts=counts)


# ── accept(): new type and total-increase rejection ──────────────────

def test_accept_rejects_new_type_even_if_weighted_falls():
    before = _claims(100, (('track_width', 100),))
    after = _claims(1, (('clearance', 1),))
    ok, why = accept(before, after)
    assert not ok
    assert 'new violation types' in why


def test_accept_rejects_total_increase():
    before = _claims(1, (('track_width', 1),))
    after = _claims(2, (('track_width', 2),))
    ok, why = accept(before, after)
    assert not ok
    assert 'total increased' in why


def test_accept_accepts_strict_weighted_decrease_same_type():
    before = _claims(5, (('track_width', 5),))
    after = _claims(2, (('track_width', 2),))
    ok, _ = accept(before, after)
    assert ok


def test_accept_rejects_congruence_loss():
    before = _claims(0)
    after = Claims(rule_congruent=False)
    ok, why = accept(before, after)
    assert not ok and 'congruence' in why


# ── transactional_repair(): exception paths restore bytes ────────────

def _write(path, text):
    with open(path, 'w', encoding='utf-8') as f:
        f.write(text)


def _read(path):
    with open(path, encoding='utf-8') as f:
        return f.read()


def test_action_exception_restores_snapshot(tmp_path):
    board = tmp_path / 'b.kicad_pcb'
    _write(board, 'ORIGINAL')

    def action(path):
        _write(path, 'MUTATED')
        raise RuntimeError('boom')

    def evaluate(path):
        return _claims(1, (('track_width', 1),))

    rep = transactional_repair(str(board), action, evaluate, max_rounds=1)
    assert rep.rolled_back == 1
    assert _read(board) == 'ORIGINAL'


def test_eval_exception_restores_snapshot(tmp_path):
    board = tmp_path / 'b.kicad_pcb'
    _write(board, 'ORIGINAL')

    def action(path):
        _write(path, 'MUTATED')
        return {}

    def evaluate(path):
        raise RuntimeError('eval boom')

    rep = transactional_repair(str(board), action, evaluate, max_rounds=1,
                               initial=_claims(1, (('track_width', 1),)))
    assert rep.rolled_back == 1
    assert _read(board) == 'ORIGINAL'


def test_rejected_batch_restores_snapshot(tmp_path):
    board = tmp_path / 'b.kicad_pcb'
    _write(board, 'ORIGINAL')

    def action(path):
        _write(path, 'MUTATED')
        return {}

    def evaluate(path):
        # Worse: new type -> reject
        return _claims(1, (('clearance', 1),))

    rep = transactional_repair(str(board), action, evaluate, max_rounds=1,
                               initial=_claims(1, (('track_width', 1),)))
    assert rep.rolled_back == 1
    assert _read(board) == 'ORIGINAL'


# ── run_drc(): fail closed, never a stale/empty report ───────────────

def test_run_drc_missing_binary_is_not_clean(tmp_path, monkeypatch):
    board = tmp_path / 'b.kicad_pcb'
    _write(board, 'not a board')
    out = tmp_path / 'b.drc.json'
    _write(out, json.dumps({'violations': [], 'unconnected_items': []}))
    monkeypatch.setenv('KICAD_CLI', str(tmp_path / 'no-such-kicad-cli'))
    d = run_drc(str(board), str(out))
    assert d['ok'] is False
    assert 'error' in d
    assert not out.exists(), 'stale report must be removed'


def test_run_drc_missing_report_is_not_clean(tmp_path, monkeypatch):
    board = tmp_path / 'b.kicad_pcb'
    _write(board, 'x')
    out = tmp_path / 'b.drc.json'
    # A CLI that runs but writes no report (python + bogus script).
    monkeypatch.setenv('KICAD_CLI', sys.executable)
    d = run_drc(str(board), str(out))
    assert d['ok'] is False
    assert 'no DRC report' in d['error']


# ── parse_violations(): English, Chinese, structured, missing ────────

def test_parse_english_clearance():
    d = {'violations': [{'type': 'clearance',
                         'description': 'clearance violation (clearance 0.2 mm; actual 0.1769 mm)',
                         'items': []}]}
    v = parse_violations(d)[0]
    assert (v.required_mm, v.actual_mm, v.has_measurement) == (0.2, 0.1769, True)


def test_parse_chinese_clearance():
    d = {'violations': [{'type': 'clearance',
                         'description': '间距违规 ( 间距 0.2000 mm; 实际 0.1769 mm)',
                         'items': []}]}
    v = parse_violations(d)[0]
    assert (v.required_mm, v.actual_mm, v.has_measurement) == (0.2, 0.1769, True)


def test_parse_structured_fields_win():
    d = {'violations': [{'type': 'clearance', 'required_mm': 0.3,
                         'actual_mm': 0.25, 'description': 'x', 'items': []}]}
    v = parse_violations(d)[0]
    assert (v.required_mm, v.actual_mm, v.has_measurement) == (0.3, 0.25, True)


def test_parse_missing_measurement_is_flagged():
    d = {'violations': [{'type': 'clearance', 'description': 'no numbers',
                         'items': []}]}
    v = parse_violations(d)[0]
    assert v.has_measurement is False


# ── binding gates ────────────────────────────────────────────────────

def test_report_binding_detects_board_change(tmp_path):
    board = tmp_path / 'b.kicad_pcb'
    report = tmp_path / 'b.drc.json'
    _write(board, 'STATE-A')
    _write(report, '{"violations": []}')
    bind_report(str(board), str(report))
    ctx = check_binding(str(board), str(report) + '.binding.json')
    reg = default_registry()
    assert reg.evaluate('report_binding', ctx).outcome is Outcome.PASS
    _write(board, 'STATE-B')
    ctx = check_binding(str(board), str(report) + '.binding.json')
    assert reg.evaluate('report_binding', ctx).outcome is Outcome.FAIL


def test_project_identity_detects_hash_mismatch(tmp_path):
    pro = tmp_path / 'b.kicad_pro'
    _write(pro, '{"net_settings": {}}')
    reg = default_registry()
    ctx = project_identity(str(pro), expected_sha='0' * 64)
    assert reg.evaluate('project_identity', ctx).outcome is Outcome.FAIL


def test_gap_assumed_are_in_worst_order():
    assert WORST_ORDER.index(Outcome.GAP) < WORST_ORDER.index(Outcome.PASS)
    assert WORST_ORDER.index(Outcome.ASSUMED) < WORST_ORDER.index(Outcome.PASS)
