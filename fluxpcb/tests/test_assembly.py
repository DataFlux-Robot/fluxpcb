"""Assembly kernel: courtyard overlap and containment, no pcbnew needed."""
from __future__ import annotations

from fluxpcb.proof import assembly


def _sq(x, y, w=1000):
    return [[x, y], [x + w, y], [x + w, y + w], [x, y + w]]


def test_same_side_overlap_is_reported_and_cross_side_is_not():
    courts = {'A': _sq(0, 0), 'B': _sq(500, 500), 'C': _sq(0, 0)}
    sides = {'A': 'F', 'B': 'F', 'C': 'B'}
    assert assembly.overlaps(courts, sides) == [('A', 'B')]
    assert assembly.overlaps({'A': _sq(0, 0), 'C': _sq(0, 0)},
                             {'A': 'F', 'C': 'B'}) == []


def test_outside_the_board_is_reported():
    outline = [[0, 0], [10000, 0], [10000, 10000], [0, 10000]]
    courts = {'IN': _sq(1000, 1000), 'OUT': _sq(9500, 9500, 2000)}
    assert assembly.outside(courts, outline) == ['OUT']
    assert assembly.outside({'IN': _sq(1000, 1000)}, outline) == []


def test_disjoint_courtyards_pass():
    courts = {'A': _sq(0, 0), 'B': _sq(2000, 0)}
    assert assembly.overlaps(courts, {'A': 'F', 'B': 'F'}) == []
