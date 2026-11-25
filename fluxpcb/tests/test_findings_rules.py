"""One finding vocabulary, one rule index, one profile, one policy.

These are the tests that keep the seven additions honest: a finding
cannot claim a measurement it does not have, an estimate cannot outrank
a measurement, an unknown must say what is missing, every gate has a
rule id, the fab profile carries its date and source, and the benchmark
path does not import any of it.
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import pytest  # noqa: E402

from fluxpcb import findings as F  # noqa: E402
from fluxpcb.gates import all_gates, run_gate  # noqa: E402
from fluxpcb.knowledge import docs, principles, rules  # noqa: E402
from fluxpcb.mfg import fab_profile  # noqa: E402
from fluxpcb.verifiers import from_gates, policy  # noqa: E402


# ------------------------------------------------------------------ finding
def test_an_estimate_can_never_outrank_a_measurement():
    est = F.Finding('X.y', measured=1.0, limit=2.0, units='mm',
                    severity='P0', status='estimate', assumption='a')
    assert est.severity == 'P1' and not est.blocking
    conf = F.Finding('X.y', measured=1.0, limit=2.0, units='mm',
                     severity='P0', status='confirmed', assumption='a')
    assert conf.severity == 'P0' and conf.blocking
    assert F.cap('P0', 'estimate') == 'P1'
    assert F.cap('P2', 'estimate') == 'P2'      # capping only lowers


def test_an_unknown_carries_no_number_and_says_what_is_missing():
    u = F.unknown('EMF.field', ['openEMS not installed'])
    assert u.measured is None and u.needed_information
    assert 'openEMS not installed' in u.issue()
    with pytest.raises(ValueError):
        F.Finding('X.y', status='unknown', measured=0.1, units='mm',
                  assumption='a', needed_information=['x'])
    with pytest.raises(ValueError):
        F.Finding('X.y', status='unknown', assumption='a')


def test_a_measurement_needs_units_and_an_assumption_is_mandatory():
    with pytest.raises(ValueError):
        F.Finding('X.y', measured=0.1, limit=0.2, assumption='a')
    with pytest.raises(ValueError):
        F.Finding('X.y', assumption='')
    assert F.Finding('X.y').assumption == F.NO_ASSUMPTION


def test_the_legacy_aliases_are_what_old_consumers_read():
    f = F.Finding('DRC.clearance', measured=0.1, limit=0.2, units='mm',
                  severity='P0', where={'x_mm': 1.5, 'y_mm': 2.5},
                  kind='clearance', net='CLK', items=({'uuid': 'u1'},))
    d = f.as_dict()
    assert d['issue'].startswith('DRC.clearance')
    assert d['type'] == 'clearance' and d['net'] == 'CLK'
    assert d['actual'] == 0.1 and d['required'] == 0.2
    assert d['location'] == [1.5, 2.5]
    assert d['items'] == [{'uuid': 'u1'}]
    assert F.verify_index([f]) == []


def test_verify_index_catches_a_broken_finding():
    good = F.Finding('A.b', assumption='x')
    assert F.verify_index([good, {'rule_id': 'x'}]) == [
        'not a Finding: dict']
    # a hand-made bad one cannot be constructed, so check the checker
    est = F.Finding('A.c', severity='P2', status='estimate', assumption='x')
    assert est.severity == 'P2'


# --------------------------------------------------------------------- rules
def test_every_gate_has_a_rule_id_severity_status_and_assumption():
    idx = rules.index()
    missing = [g for g in all_gates() if g not in idx['gates']]
    assert missing == [], missing
    for gate, entry in idx['gates'].items():
        assert entry.get('rule_id'), gate
        assert entry.get('severity') in F.SEVERITIES, gate
        assert entry.get('status') in F.STATUSES, gate
        assert entry.get('assumption'), gate
        assert rules.known(entry['rule_id']), gate


def test_wrapping_a_legacy_finding_gives_it_the_vocabulary():
    out = rules.normalise([{'issue': 'DRC违例 3 处', 'type': 'clearance',
                            'net': 'CLK', 'actual': 0.1, 'required': 0.2,
                            'location': (1.0, 2.0)}],
                          'drc_zero', board_fp='abc', phase='post_route')
    assert len(out) == 1
    d = out[0]
    assert d['rule_id'] == 'DRC.report'
    assert d['severity'] == 'P0' and d['status'] == 'confirmed'
    assert d['measured'] == 0.1 and d['limit'] == 0.2
    assert d['where'] == {'x_mm': 1.0, 'y_mm': 2.0}
    assert d['board_fp'] == 'abc'
    assert 'assumption' in d and d['assumption']


def test_a_gate_finding_that_cannot_obey_becomes_an_unknown_not_a_drop():
    out = rules.normalise([{'issue': 'x', 'status': 'unknown',
                            'measured': 0.5}], 'thermal_cfd')
    assert out[0]['status'] == 'unknown' and out[0]['measured'] is None
    assert out[0]['needed_information']


def test_the_gate_layer_emits_findings_with_ids(tmp_path):
    d = tmp_path / 'p'
    (d / 'pcb').mkdir(parents=True)
    (d / 'project.yaml').write_text('name: p\n', encoding='utf-8')
    r = run_gate('drc_zero', d, [])
    assert r.name == 'drc_zero'
    # nothing to measure: the gate says what is missing, it does not pass
    assert r.needed_information
    assert isinstance(r.findings, list)


# ---------------------------------------------------------------------- docs
def test_the_numbered_documents_load_on_demand():
    st = docs.stats()
    assert st.get('knowhow', 0) >= 20
    assert st.get('litreview', 0) >= 5
    assert st.get('skills', 0) >= 5
    ids = docs.ids()
    assert len(ids) == len(set(ids)), 'document ids must be unique'
    body = docs.load('K25')
    assert body and '测量' in body or 'state' in body.lower()
    assert docs.load('no-such-doc') == ''
    assert 'K1' in docs.index_text(40)


def test_the_principles_are_prepended_once():
    once = principles.with_principles('You are a router.')
    assert once.startswith(principles.PRINCIPLES)
    assert principles.with_principles(once) == once
    assert '不确定就说不确定' in principles.PRINCIPLES


# ------------------------------------------------------------------ profile
def test_the_fab_profile_carries_its_date_source_and_tiers():
    prof = fab_profile.active_profile()
    assert prof.retrieved and prof.sources and prof.assumption
    assert fab_profile.check_profile(prof) == []
    below = prof.tier_for('clearance', 0.05)
    assert below['tier'] == 'not_manufacturable' and below['below_limit']
    adv = prof.board_tier({'clearance': 0.1, 'track_width': 0.2})
    assert adv['tier'] == 'advanced'
    advice = prof.drop_tier_advice({'clearance': 0.1, 'track_width': 0.2})
    assert advice and all(a['target_mm'] > a['measured_mm'] for a in advice)
    assert advice[0]['source'].startswith('http')
    assert advice[0]['retrieved'] == prof.retrieved


def test_a_project_profile_overrides_the_packaged_one(tmp_path):
    (tmp_path / 'fab_profile.json').write_text(json.dumps({
        'profile_id': 'lab', 'vendor': 'lab', 'retrieved': '2026-11-01',
        'sources': [{'url': 'x', 'quote': 'y'}],
        'assumption': 'lab process',
        'parameters': {'clearance': {'limit_mm': 0.05, 'recommended_mm': 0.1}},
        'tiers': [], 'decides_tier': ['clearance']}), encoding='utf-8')
    prof = fab_profile.active_profile(tmp_path)
    assert prof.profile_id == 'lab' and prof.tier_for('clearance', 0.06)['tier'] == 'advanced'


def test_an_unknown_parameter_is_unknown_not_defaulted():
    prof = fab_profile.active_profile()
    t = prof.tier_for('dielectric_height', 0.1)
    assert t['known'] is False and t['tier'] == 'unknown'


# ------------------------------------------------------------------- policy
def test_execute_needs_a_confirmation_and_degrades_to_estimate():
    p = policy.PhysicsPolicy(mode='execute')
    assert p.mode == 'estimate' and 'confirmation' in p.reason
    p2 = policy.PhysicsPolicy(mode='execute', confirmed=True)
    assert p2.executes and p2.allows('em_field_analysis')
    p3 = policy.PhysicsPolicy(mode='off')
    assert not p3.allows('thermal') and p3.why_not('thermal')
    assert not policy.PhysicsPolicy(mode='estimate').allows('thermal_cfd')


def test_an_estimate_policy_runs_the_proxies_but_not_the_solvers():
    est = policy.PhysicsPolicy(mode='estimate')
    assert est.allows('thermal') and est.allows('em_susceptibility')
    assert not est.allows('em_field_analysis')
    assert 'not confirmed' in ' '.join(est.why_not('em_field_analysis'))


# ------------------------------------------------------------------- suite
def test_a_verifier_without_a_project_says_unknown():
    from fluxpcb.contracts import BoardState
    st = BoardState()
    r = from_gates.run_verifier('power_integrity', st)
    assert r.status == 'unknown' and r.needed_information
    assert 'project directory' in r.needed_information[0]


def test_the_five_verifiers_are_the_gate_map():
    from fluxpcb.verifiers import (EMCVerifier, PowerIntegrityVerifier,
                                   SignalIntegrityVerifier, SpiceVerifier,
                                   ThermalVerifier)
    for cls, name in ((PowerIntegrityVerifier, 'power_integrity'),
                      (SignalIntegrityVerifier, 'signal_integrity'),
                      (ThermalVerifier, 'thermal'),
                      (SpiceVerifier, 'spice_functional'),
                      (EMCVerifier, 'emc_precompliance')):
        v = cls()
        assert v.name == name and name in from_gates.GATE_MAP


# --------------------------------------------------- the benchmark invariant
def test_the_repair_path_imports_none_of_the_new_physics():
    """run_bench repairs a routed board with repair_drc; if that import
    pulls in gates, the verifier suite or the new physics modules, the
    benchmark would change with work that is not about routing."""
    import subprocess
    code = (
        "import sys; sys.path.insert(0, r'%s');\n"
        "import fluxpcb.repair_drc, fluxpcb.verifiers.operators,"
        " fluxpcb.verifiers.conflict_reroute, fluxpcb.verifiers.txn\n"
        "bad = [m for m in sys.modules if m.startswith(('fluxpcb.gates',"
        " 'fluxpcb.verifiers.suite', 'fluxpcb.verifiers.from_gates',"
        " 'fluxpcb.tools.susceptibility', 'fluxpcb.mfg'))]\n"
        "print(','.join(sorted(bad)))\n" % ROOT)
    out = subprocess.run([sys.executable, '-c', code], capture_output=True,
                         text=True, timeout=180)
    assert out.returncode == 0, out.stderr[-400:]
    assert out.stdout.strip() == '', out.stdout
