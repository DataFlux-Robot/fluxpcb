"""The identity of an open is its net's count, not the two items the
DRC happened to name."""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from fluxpcb.verifiers.txn import accept, claims_from_drc  # noqa: E402


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
