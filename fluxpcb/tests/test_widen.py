"""Measured widening: the parts that need no board.

kicad-cli reports in the system language, and a width finding carries
its minimum in words that no pattern used to read.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from fluxpcb.verifiers.operators import (  # noqa: E402
    _item_kind, _item_net, _measure_from_description, _widen_certificate)
from fluxpcb.verifiers.params import OperatorParams, SCHEMA  # noqa: E402


def test_numbers_are_read_in_either_language():
    zh_clear = '间距违规 ( 间距 0.2000 mm; 实际 0.1795 mm)'
    en_clear = 'Clearance violation (Clearance 0.2032 mm; actual 0.1693 mm)'
    zh_width = '走线宽度 (电路板配置约束 最小宽度 0.2000 mm; 实际 0.1500 mm)'
    en_width = 'Track width (board setup constraint min width 0.1524 mm; actual 0.1016 mm)'
    assert _measure_from_description(zh_clear) == (0.2, 0.1795)
    assert _measure_from_description(en_clear) == (0.2032, 0.1693)
    assert _measure_from_description(zh_width) == (0.2, 0.15)
    assert _measure_from_description(en_width) == (0.1524, 0.1016)


def test_a_number_the_text_does_not_carry_stays_missing():
    assert _measure_from_description('Unconnected items') == (None, None)
    assert _measure_from_description('') == (None, None)
    req, act = _measure_from_description('间距违规 ( 间距 0.2000 mm)')
    assert req == 0.2 and act is None


def test_blocker_kind_and_net_from_the_drc_item():
    assert _item_kind('过孔 [/SDA] (F.Cu - B.Cu)') == 'via'
    assert _item_kind('B.Cu 上 C1 的焊盘 2 [GNDPWR]') == 'pad'
    assert _item_kind('走线 [/SCL] (F.Cu), 长度: 0.6486 mm') == 'track'
    assert _item_kind('Via [/SDA] (F.Cu - B.Cu)') == 'via'
    assert _item_kind('Pad 2 of C1 on B.Cu [GNDPWR]') == 'pad'
    assert _item_kind('Track [/SCL] on F.Cu, length: 0.6486 mm') == 'track'
    assert _item_kind('something else') == 'other'
    assert _item_net('过孔 [/SDA] (F.Cu - B.Cu)') == '/SDA'
    assert _item_net('B.Cu 上 C1 的焊盘 2 [GNDPWR]') == 'GNDPWR'
    assert _item_net('no net here') == ''


def test_violations_carry_their_measured_width():
    from fluxpcb.repair_drc import parse_violations
    drc = {'violations': [{
        'type': 'track_width', 'severity': 'error',
        'description': '走线宽度 (电路板配置约束 最小宽度 0.2000 mm; 实际 0.1500 mm)',
        'items': [{'description': '走线 [/SCL] (F.Cu), 长度: 0.6486 mm',
                   'uuid': 'u1', 'pos': {'x': 1.0, 'y': 2.0}}]}]}
    v = parse_violations(drc)[0]
    assert v.has_measurement
    assert (v.required_mm, v.actual_mm) == (0.2, 0.15)


def test_certificate_names_the_blocker_and_what_was_tried():
    fact = {'uuid': 'me', 'net': '/SCL', 'pos': [1.0, 2.0], 'w': 0.15}
    v = {'description': '间距违规 ( 间距 0.2000 mm; 实际 0.1795 mm)',
         'items': [{'description': '走线 [/SCL] (F.Cu), 长度: 0.6486 mm', 'uuid': 'me'},
                   {'description': '过孔 [/SDA] (F.Cu - B.Cu)', 'uuid': 'u9'}]}
    c = _widen_certificate(fact, 200000, [v], ('slide', 'replan'))
    assert c['operator'] == 'widen_narrow_tracks'
    assert c['outcome'] == 'verify_rejected'
    blk = c['why_not']['blocker']
    assert blk['kind'] == 'via' and blk['net'] == '/SDA'
    assert blk['short_by_mm'] == 0.0205 and blk['conflicts'] == 1
    assert c['attempt'] == {'class': 'track_width', 'from_mm': 0.15,
                            'to_mm': 0.2, 'tried': ['slide', 'replan']}
    assert c['requirement'] == {'clearance_mm': 0.2}
    # the record is about the track as it WAS, not about a live object
    assert c['item'] == {'net': '/SCL', 'pos': [1.0, 2.0]}
    # nothing measured: the blocker is unknown, never invented
    c0 = _widen_certificate(fact, 200000, [], ('slide',))
    assert c0['why_not']['blocker']['kind'] == 'other'
    assert 'short_by_mm' not in c0['why_not']['blocker']
    assert c0['requirement'] == {'clearance_mm': None}


def test_the_two_new_variables_are_typed_and_default_on():
    assert SCHEMA['widen_measure'] == {'kind': 'bool'}
    assert SCHEMA['widen_replan'] == {'kind': 'bool'}
    p = OperatorParams()
    assert p.widen_measure and p.widen_replan and p.blind_widen
    q = p.with_delta('widen_measure', False)
    assert [n for n, d, v in q.deltas_from(p)] == ['widen_measure']
