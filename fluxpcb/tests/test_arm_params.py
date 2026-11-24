"""Typed operator parameters and arms: the variables the meta loops change."""
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from fluxpcb.meta.arm import (LOOP_MODES, PLAN_MODES, Arm,  # noqa: E402
                              default_arm, factorial_arms)
from fluxpcb.meta.policy import PolicyArtifact  # noqa: E402
from fluxpcb.verifiers.operator_spec import (REROUTE_NETS,  # noqa: E402
                                             all_operator_names,
                                             default_registry)
from fluxpcb.verifiers.params import (SCHEMA, OperatorParams,  # noqa: E402
                                      check_value)


# -- parameters ---------------------------------------------------------
def test_defaults_round_trip_with_a_stable_digest():
    p = OperatorParams()
    q = OperatorParams.from_dict(json.loads(json.dumps(p.as_dict())))
    assert q == p
    assert q.digest() == p.digest()


def test_every_field_is_in_the_schema_and_vice_versa():
    names = {v[0] for v in OperatorParams.variables()}
    assert names == set(SCHEMA)


def test_out_of_range_and_unknown_values_are_rejected():
    p = OperatorParams()
    for name, value in [('maze_cap', 0), ('maze_max_expand', 10),
                        ('nudge_steps_um', [1.0]),
                        ('corridor_offsets_mm', [0.01, 50.0]),
                        ('blind_widen', 1), ('connect_cap', 2.5),
                        ('preroute_pair_mm', 'six'), ('not_a_variable', 1)]:
        with pytest.raises(ValueError):
            p.with_delta(name, value)


def test_one_variable_intervention_changes_exactly_that_variable():
    p = OperatorParams()
    q = p.with_delta('maze_cap', 9)
    assert q.deltas_from(p) == [('maze_cap', 6, 9)]
    assert q.digest() != p.digest()


# -- arms ---------------------------------------------------------------
def test_factorial_arms_are_twelve_distinct_interventions():
    arms = factorial_arms()
    assert len(arms) == 12
    assert len({a.digest() for a in arms}) == 12
    assert sorted({a.plan for a in arms}) == sorted(PLAN_MODES)
    assert sorted({a.loop for a in arms}) == sorted(LOOP_MODES)
    assert all(a.validate() == [] for a in arms)


def test_arm_json_round_trip_checks_its_own_digest(tmp_path):
    a = factorial_arms()[0]
    path = str(tmp_path / 'arm.json')
    digest = a.to_json(path)
    assert Arm.from_json(path).digest() == digest
    body = json.load(open(path, encoding='utf-8'))
    body['router_passes'] += 1                 # edited without re-hashing
    json.dump(body, open(path, 'w', encoding='utf-8'))
    with pytest.raises(ValueError):
        Arm.from_json(path)


def test_arm_rejects_unknown_modes_and_operators():
    assert Arm(name='x', plan='telepathy').validate()
    assert Arm(name='x', loop='vibes').validate()
    assert Arm(name='x', allowed_ops=('no_such_op',)).validate()
    assert Arm(name='x', allowed_ops=(REROUTE_NETS,)).validate() == []


def test_registry_emits_every_name_a_policy_can_name():
    emitted = set(all_operator_names())
    assert REROUTE_NETS not in emitted            # a stage, not a repair operator
    for spec in default_registry().available():
        assert set(spec.emitted()) <= emitted, spec.name


def test_loop_modes_differ_in_stages_and_rounds():
    arms = {a.name: a for a in factorial_arms()}
    assert arms['gate+route_only'].stages() == []
    assert arms['gate+batch'].stages() == ['repair:batch', 'reroute']
    assert arms['gate+morph'].stages() == ['repair:morph']
    assert arms['gate+ripup'].stages() == ['ripup', 'repair:batch']
    assert arms['gate+morph'].rounds() > arms['gate+batch'].rounds()


def test_param_intervention_on_an_arm_touches_one_variable():
    a = factorial_arms()[3]
    b = a.with_param('nudge_cap', 5)
    assert b.param_deltas(a) == [('nudge_cap', 20, 5)]
    assert b.plan == a.plan and b.loop == a.loop


def test_legacy_policy_adapts_to_an_arm():
    arm = Arm.from_policy(PolicyArtifact(), timeout_s=180)
    assert arm.plan == 'gate' and arm.loop == 'batch'
    assert arm.router_timeout_s == 180
    # the legacy policy forbade maze and rip-up: the arm must too, by name
    assert 'maze_escape' not in arm.allowed()
    assert 'short_rip_reroute' not in arm.allowed()
    assert set(arm.allowed()) <= set(all_operator_names())
    assert default_arm().allowed() is None
