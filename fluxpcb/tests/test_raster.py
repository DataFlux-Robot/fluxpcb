"""Coverage equivalence: set equality at a declared resolution."""
from __future__ import annotations

from fluxpcb.proof import raster, state as S


def _st(dx=0):
    st = {'board': 'r', 'layers': ['F.Cu'],
          'items': [{'id': 'p', 'net': 'N', 'layers': ['F.Cu'],
                     'shape': {'kind': 'poly', 'pts': [
                         [5000000 + dx, 5000000], [6000000 + dx, 5000000],
                         [6000000 + dx, 6000000], [5000000 + dx, 6000000]]}}],
          'rules': {}, 'netPads': {'N': ['p']}}
    return S.freeze(st)


def test_coverage_accepts_the_same_copper_and_rejects_a_shift():
    b = _st()
    g = _st()
    r = raster.coverage(b, g)
    assert r['ok'] is True, r
    r = raster.coverage(b, _st(50000))       # the 50 um negative control
    assert r['ok'] is False, r


def test_one_cell_of_boundary_rounding_is_tolerated():
    b = _st()
    r = raster.coverage(b, _st(10000))       # one 10 um cell
    assert r['ok'] is True, r
