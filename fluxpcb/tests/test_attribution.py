"""Every action carries its own attribution.

Who decided, what else was tried, which board state the numbers came
from, and what was missing.  These tests pin the four defects that made
attribution silently disappear, and the record shape the meta layer reads.
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import pytest  # noqa: E402

from fluxpcb import contracts  # noqa: E402
from fluxpcb.contracts import NetTask, SignalClass  # noqa: E402
from fluxpcb.dispatch.dispatcher import DispatchRuntime  # noqa: E402
from fluxpcb.verifiers.certs import cert_entry  # noqa: E402
from fluxpcb.verifiers.txn import (Claims, board_fingerprint,  # noqa: E402
                                   transactional_repair)


def test_a_certificate_names_its_rule_and_accepts_a_decider():
    d = cert_entry('widen_narrow_tracks', {'net': 'A'}, 'ok')
    assert d['decider'] == {'kind': 'rule', 'id': 'widen_narrow_tracks'}
    assert d['alternatives'] == [] and d['needed_information'] == []
    assert d['board_fp'] == ''
    llm = cert_entry('connect_unconnected', {'net': 'A'}, 'no_path',
                     decider={'kind': 'llm', 'id': 'route_agent',
                              'model': 'm', 'prompt_digest': 'abc'},
                     alternatives=[{'tried': 'nudge', 'why': 'no room'}],
                     needed_information=['pin 4 datasheet'],
                     board_fp='deadbeefdeadbeef')
    assert llm['decider']['kind'] == 'llm'
    assert llm['alternatives'][0]['tried'] == 'nudge'
    assert llm['needed_information'] == ['pin 4 datasheet']
    assert llm['board_fp'] == 'deadbeefdeadbeef'
    assert llm['cert_id'] != d['cert_id']


def test_every_round_of_a_transaction_names_the_decider_and_the_board(tmp_path):
    pcb = tmp_path / 'b.kicad_pcb'
    pcb.write_text('(kicad_pcb)', encoding='utf-8')
    fp = board_fingerprint(str(pcb))
    calls = {'n': 0}

    def action(_path):
        calls['n'] += 1
        return {'operators_run': ['nudge_clearance'], 'repaired': 1, 'failed': 0}

    def evaluate(_path):
        calls['n'] += 1
        # first round improves, second is flat
        return Claims(n_errors=2 if calls['n'] < 2 else 1,
                      error_types=frozenset({'clearance'}),
                      error_ids=frozenset({'clearance|u%d' % i
                                           for i in range(2 if calls['n'] < 2 else 1)}),
                      type_counts=(('clearance', 2 if calls['n'] < 2 else 1),))

    rep = transactional_repair(str(pcb), action, evaluate, max_rounds=2,
                               initial=Claims(n_errors=3,
                                              error_types=frozenset({'clearance'}),
                                              error_ids=frozenset({'clearance|u0',
                                                                   'clearance|u1',
                                                                   'clearance|u2'}),
                                              type_counts=(('clearance', 3),)),
                               decider={'kind': 'rule', 'id': 'repair:widen'})
    assert rep.log
    for entry in rep.log:
        assert entry['decider'] == {'kind': 'rule', 'id': 'repair:widen'}
        assert entry['board_fp'] == fp


def test_a_subtask_keeps_its_specialist_and_operator_through_json():
    t = NetTask(net='CLK', cls=SignalClass.CLOCK,
                specialist='length_match@clock', repair_operator='width_cascade_surgery',
                specialist_rationale='equal length required')
    body = contracts.to_json(t)
    back = json.loads(body) if isinstance(body, str) else body
    if isinstance(back, dict) and 'net' in back:
        assert back['specialist'] == 'length_match@clock'
        assert back['repair_operator'] == 'width_cascade_surgery'
    # and the dataclass itself carries them (not a private attribute)
    assert t.specialist and t.repair_operator


class _FakeLLM:
    def __init__(self, ok=True):
        self.ok = ok
        self.calls = []

    def complete(self, system, prompt, schema=None):
        self.calls.append((system, prompt))
        if not self.ok:
            return {'ok': False, 'text': '', 'error': 'offline',
                    'provider': 'fake', 'model': 'm1', 'prompt_digest': 'p1'}
        return {'ok': True, 'text': json.dumps({
            'via_type': 'through', 'via_size': [0.6, 0.3], 'track_width': 0.2,
            'routing_type': 'diff_pair', 'bga_fanout_style': 'dogbone'}),
            'error': '', 'provider': 'fake', 'model': 'm1',
            'prompt_digest': 'p1'}


def test_the_llm_dispatch_path_actually_runs_and_names_its_model():
    """`complete(prompt)` with one argument raised TypeError and was
    swallowed: the LLM mode had never run.  Two arguments, and the
    decider records the model and the prompt digest."""
    llm = _FakeLLM()
    d = DispatchRuntime(llm=llm)
    plan = d.dispatch(NetTask(net='USB_D+', cls=SignalClass.DIFFERENTIAL))
    assert llm.calls and isinstance(llm.calls[0], tuple) and llm.calls[0][1]
    assert plan.planner.value == 'llm' or str(plan.planner).endswith('LLM')
    assert plan.decider['kind'] == 'llm'
    assert plan.decider['model'] == 'm1'
    assert plan.decider['prompt_digest'] == 'p1'


def test_an_offline_llm_falls_back_to_rules_with_its_own_decider():
    d = DispatchRuntime(llm=_FakeLLM(ok=False))
    plan = d.dispatch(NetTask(net='PWR', cls=SignalClass.POWER))
    assert plan.decider['kind'] == 'rule'
    assert plan.decider['id'] in ('dispatch_rules', 'dispatch_baseline')
    assert plan.rationale


def test_the_meta_loop_receives_the_rounds_as_packets():
    from fluxpcb.pipeline import _packets_of

    class Ev:
        action = {'iteration': 0, 'fingerprint': 'abc',
                  'plans': {'*': {'decider': {'kind': 'rule', 'id': 'x'}}},
                  'verification': {'passed': True}}

    class Dev:
        trajectory = type('T', (), {'events': [Ev()]})()
        converged = True
        seconds = 1.0

    packs = _packets_of(Dev())
    assert packs and packs[0]['outcome'] == 'clean'
    assert packs[0]['rounds'][0]['board_fp'] == 'abc'
    assert packs[0]['rounds'][0]['operators'] == ['*']
    assert _packets_of(None) is None


def test_the_page_shows_what_was_missing_and_which_open():
    from fluxpcb.dashboard import board_page as P
    cert = {
        'board': 'b.kicad_pcb', 'route': {}, 'claims_after': {'total': 1,
                                                              'n_errors': 0,
                                                              'n_unconnected': 1},
        'failures': [
            {'op': 'conflict_reroute', 'outcome': 'no_path',
             'item': {'net': 'CSN', 'type': 'unconnected', 'pos': [1, 2],
                      'items': [{'description': 'U1 pad 4 [CSN]'},
                                {'description': 'P1 pad 8 [CSN]'}]},
             'needed_information': ['pin 4 needs a 0.15 mm escape']},
        ],
    }
    opens = P.open_connections(cert)
    assert opens and opens[0]['net'] == 'CSN'
    need = P.needed_information(cert)
    assert need and '0.15 mm escape' in need[0]['text']
    page = P.render(cert)
    assert 'Open connections' in page and 'U1 pad 4' in page
    assert 'Needed information' in page and '0.15 mm escape' in page
