"""The identity of an open is its net's count, not the two items the
DRC happened to name."""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from fluxpcb.verifiers.txn import (accept, claims_from_drc,  # noqa: E402
                                steady, union_claims)


def _drc(edges):
    return {'violations': [], 'unconnected_items': [
        {'type': 'unconnected', 'items': [{'uuid': 'a%d' % k, 'description': d1},
                                          {'uuid': 'b%d' % k, 'description': d2}]}
        for k, (d1, d2) in enumerate(edges)]}


A, B = ('走线 [/X] (B.Cu), 长度: 3.7 mm', 'F.Cu 上 U1 的焊盘 20 [/X]')
A2 = 'F.Cu 上 R1 的焊盘 2 [/X]'
Y = ('F.Cu 上 J2 的焊盘 1 [/Y]', 'F.Cu 上 U1 的焊盘 7 [/Y]')


def test_the_same_opens_under_another_spanning_tree_are_the_same_state():
    one = claims_from_drc(_drc([(A, B), Y]))
    two = claims_from_drc(_drc([(A2, B), Y]))
    assert one.unconnected_ids == two.unconnected_ids
    assert one.ids == two.ids


def test_a_closed_open_shrinks_the_identity_set():
    two = claims_from_drc(_drc([(A, B), Y]))
    one = claims_from_drc(_drc([(A, B)]))
    assert one.ids < two.ids
    assert accept(two, one)[0]


def test_two_opens_of_one_net_are_two_identities():
    net = claims_from_drc(_drc([(A, B), (A2, B)]))
    assert len(net.unconnected_ids) == 2
    again = claims_from_drc(_drc([(A2, B), (A, B)]))
    assert again.unconnected_ids == net.unconnected_ids


def _errs(*types_):
    return claims_from_drc({'violations': [
        {'type': t, 'severity': 'error', 'items': [{'uuid': 'u%d' % k}]}
        for k, t in enumerate(types_)]})


def test_the_union_of_evaluations_is_every_finding_seen():
    two = _errs('clearance', 'clearance')
    three = _errs('clearance', 'clearance', 'track_width')
    u = union_claims(two, three)
    # the ids are per finding (u0, u1, u2): the union has all three
    assert u.n_errors == 3 and dict(u.type_counts) == {'clearance': 2, 'track_width': 1}
    assert u.error_types == {'clearance', 'track_width'}
    assert union_claims(two, two) == two
    with_open = claims_from_drc(_drc([(A, B), Y]))
    assert union_claims(with_open, with_open) == with_open


def test_steady_asks_again_only_when_the_answer_moves():
    calls = []
    answers = iter([_errs('clearance'), _errs('clearance', 'clearance'),
                    _errs('clearance', 'clearance', 'track_width')])

    def evaluate(_path):
        calls.append(1)
        return next(answers)

    assert steady(evaluate)('b').n_errors == 3 and len(calls) == 3
    calls.clear()
    same = lambda _path: _errs('clearance')  # noqa: E731
    assert steady(same)('b').n_errors == 1 and True
    clean = lambda _path: claims_from_drc({'violations': []})  # noqa: E731
    counted = []

    def clean_counting(_path):
        counted.append(1)
        return claims_from_drc({'violations': []})
    assert steady(clean_counting)('b').total == 0 and len(counted) == 1


def test_a_randomly_unreported_violation_cannot_reject_a_repair():
    """A repair that leaves two of three violations must not be judged
    "total increased" because the DRC happened to see all three this
    time and only two before."""
    before = union_claims(_errs('clearance', 'clearance'), _errs('clearance', 'clearance', 'clearance'))
    after = union_claims(_errs('clearance', 'clearance'), _errs('clearance', 'clearance'))
    assert before.n_errors == 3 and after.n_errors == 2
    assert accept(before, after)[0]
