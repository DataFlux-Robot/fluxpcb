"""The Gerber kernel and the process box, on synthetic artifacts."""
from __future__ import annotations

import pytest

from fluxpcb.kernels import fab
from fluxpcb.proof import gerber, process, state as S


def _rect(pid, net, cx, cy, w, h, ref='U1'):
    return {'id': pid, 'net': net, 'layers': ['F.Cu'], 'ref': ref,
            'shape': {'kind': 'poly', 'pts': [
                [cx - w // 2, cy - h // 2], [cx + w // 2, cy - h // 2],
                [cx + w // 2, cy + h // 2], [cx - w // 2, cy + h // 2]]}}


def _board():
    st = {'board': 'g', 'layers': ['F.Cu', 'B.Cu'],
          'items': [_rect('p1', 'N', 1000000, 1000000, 500000, 500000),
                    {'id': 'p2', 'net': 'N', 'layers': ['F.Cu'], 'ref': 'U2',
                     'shape': {'kind': 'circle', 'cx': 3000000, 'cy': 1000000,
                               'r': 250000}},
                    {'id': 't1', 'net': 'N', 'layers': ['F.Cu'],
                     'shape': {'kind': 'seg', 'x1': 1000000, 'y1': 1000000,
                               'x2': 3000000, 'y2': 1000000, 'w': 200000}}],
          'rules': {'clearance': 100000, 'track_min': 150000},
          'netPads': {'N': ['p1', 'p2']},
          'footprints': {'U1': ['p1'], 'U2': ['p2']},
          'zones': 0,
          'outline': [[0, 0], [4000000, 0], [4000000, 4000000],
                      [0, 4000000]]}
    return S.freeze(st)


GTL = """%FSLAX46Y46*%
%MOMM*%
%ADD10R,0.500000X0.500000*%
%ADD11C,0.500000*%
%ADD12C,0.200000*%
G01*
D10*
X1000000Y-1000000D03*
D11*
X3000000Y-1000000D03*
D12*
X1000000Y-1000000D02*
X3000000Y-1000000D01*
M02*
"""
GM1 = """%FSLAX46Y46*%
%MOMM*%
%ADD10C,0.150000*%
G01*
D10*
X0Y0D02*
X4000000Y0D01*
X4000000Y-4000000D01*
X0Y-4000000D01*
X0Y0D01*
M02*
"""
DRL = """M48
METRIC
T1C0.400
%
T1
X1.0Y-1.0
M30
"""


def _files(tmp_path, gtl=GTL):
    d = tmp_path / 'g'
    d.mkdir()
    (d / 'f.gtl').write_text(gtl, encoding='utf-8')
    (d / 'e.gm1').write_text(GM1, encoding='utf-8')
    (d / 'b.drl').write_text(DRL, encoding='utf-8')
    return {'F.Cu': str(d / 'f.gtl'), 'B.Cu': str(d / 'f.gtl'),
            'Edge.Cuts': str(d / 'e.gm1')}, str(d / 'b.drl'), d


def test_gerber_bijection_and_negative_control(tmp_path):
    st = _board()
    files, drill, d = _files(tmp_path)
    # B.Cu reuses the F.Cu plot: its items are on F.Cu only, so this checks
    # the bijection for one layer; the real board passes both layers.
    files = {'F.Cu': files['F.Cu'], 'Edge.Cuts': files['Edge.Cuts']}
    r = gerber.check(st, files, drill)
    assert r['ok'], r['reasons']
    assert r['equiv']['n_extra'] == 0 and r['equiv']['n_missing'] == 0
    shifted = gerber.shift_x(files['F.Cu'], d / 'shift.gtl', 50000)
    r2 = gerber.check(st, {'F.Cu': str(shifted),
                           'Edge.Cuts': files['Edge.Cuts']}, drill)
    assert not r2['ok']
    assert r2['equiv']['n_extra'] > 0 and r2['equiv']['n_missing'] > 0


def test_gerber_reports_a_dropped_pad(tmp_path):
    st = _board()
    files, drill, d = _files(tmp_path)
    files = {'F.Cu': files['F.Cu'], 'Edge.Cuts': files['Edge.Cuts']}
    dropped = GTL.replace('X3000000Y-1000000D03*', '')
    (d / 'drop.gtl').write_text(dropped, encoding='utf-8')
    r = gerber.check(st, {'F.Cu': str(d / 'drop.gtl'),
                          'Edge.Cuts': files['Edge.Cuts']}, drill)
    assert not r['ok']
    assert r['equiv']['n_missing'] >= 1


def _gap_state(gap_nm, ann_nm=100000, drill_nm=400000):
    st = {'board': 'g', 'layers': ['F.Cu'],
          'items': [{'id': 'a', 'net': 'A', 'layers': ['F.Cu'],
                     'shape': {'kind': 'seg', 'x1': 0, 'y1': 0,
                               'x2': 1000000, 'y2': 0, 'w': 200000}},
                    {'id': 'b', 'net': 'B', 'layers': ['F.Cu'],
                     'shape': {'kind': 'seg', 'x1': 0, 'y1': 200000 + gap_nm,
                               'x2': 1000000, 'y2': 200000 + gap_nm, 'w': 200000}},
                    {'id': 'v', 'net': 'A', 'layers': ['F.Cu', 'B.Cu'],
                     'drill': drill_nm, 'span': 'F.Cu-B.Cu',
                     'shape': {'kind': 'circle', 'cx': 2000000, 'cy': 0,
                               'r': ann_nm + drill_nm // 2}}],
          'rules': {'clearance': 150000, 'track_min': 150000,
                    'annular': 75000},
          'netPads': {'A': ['a', 'v'], 'B': ['b']},
          'outline': [[-1000000, -1000000], [5000000, -1000000],
                      [5000000, 3000000], [-1000000, 3000000]]}
    return S.freeze(st)


def test_process_margins_are_numbers_not_booleans():
    st = _gap_state(200000)
    box = {'registration': 0, 'drill_offset': 0, 'etch': 10000, 'rout': 0}
    r = process.margins(st, st['rules'], box)
    assert r['ok'], r['reasons']
    assert r['margins']['clearance']['margin_nm'] == 200000 + 20000 - 150000
    assert r['margins']['width']['margin_nm'] == 200000 - 20000 - 150000
    # annular: 100 um nominal, 75 um rule, 50 um drill + 25 um registration
    box2 = {'registration': 25000, 'drill_offset': 50000, 'etch': 10000,
            'rout': 0}
    r2 = process.margins(st, st['rules'], box2)
    assert r2['margins']['annular']['margin_nm'] == 100000 - 75000 - 50000 - 25000 - 10000
    assert not r2['ok']


def test_a_same_hole_on_two_layers_is_one_hole():
    st = {'board': 'h', 'layers': ['F.Cu', 'B.Cu'],
          'items': [{'id': 'f', 'net': 'A', 'layers': ['F.Cu'], 'drill': 400000,
                     'shape': {'kind': 'circle', 'cx': 1000000, 'cy': 1000000,
                               'r': 300000}},
                    {'id': 'b', 'net': 'A', 'layers': ['B.Cu'], 'drill': 400000,
                     'shape': {'kind': 'circle', 'cx': 1000000, 'cy': 1000000,
                               'r': 300000}}],
          'rules': {}, 'netPads': {}}
    st = S.freeze(st)
    assert fab.hole_min_gap(st) is None


def test_slot_drill_uses_both_axes_for_annular():
    st = {'board': 'slot', 'layers': ['F.Cu'],
          'items': [{'id': 'p', 'net': 'N', 'layers': ['F.Cu'], 'drill': 1150000,
                     'drill_y': 500000,
                     'shape': {'kind': 'poly', 'pts': [
                         [0, 0], [1550000, 0], [1550000, 1000000],
                         [0, 1000000]]}}],
          'rules': {'annular': 75000}, 'netPads': {'N': ['p']}}
    st = S.freeze(st)
    # annulus is min(1.55-1.15, 1.0-0.5)/2 = 0.2 mm
    assert fab.annular_min_value(st)[0] == 200000
    rep = fab.check(st, [{'id': 'A', 'pred': 'annular', 'scope': None,
                          'rule': 150000}], st['rules'])
    assert rep['claims'][0]['status'] == 'proven', rep['claims'][0]
    rep = fab.check(st, [{'id': 'A', 'pred': 'annular', 'scope': None,
                          'rule': 250000}], st['rules'])
    assert rep['claims'][0]['status'] == 'refuted'


def test_npth_is_a_hole_not_copper():
    st = {'board': 'npth', 'layers': ['F.Cu', 'B.Cu'],
          'items': [{'id': 'h', 'net': '~h', 'layers': [], 'npth': True,
                     'drill': 1000000,
                     'shape': {'kind': 'circle', 'cx': 5000000, 'cy': 5000000,
                               'r': 500000}},
                    {'id': 't', 'net': 'A', 'layers': ['F.Cu'],
                     'shape': {'kind': 'seg', 'x1': 4000000, 'y1': 5000000,
                               'x2': 6000000, 'y2': 5000000, 'w': 200000}}],
          'rules': {'clearance': 200000, 'annular': 75000}, 'netPads': {}}
    st = S.freeze(st)
    rep = fab.check(st, [{'id': 'C', 'pred': 'clearance', 'scope': None,
                          'rule': 200000},
                         {'id': 'A', 'pred': 'annular', 'scope': None,
                          'rule': 75000}], st['rules'])
    assert rep['claims'][0]['status'] == 'proven', rep['claims'][0]
    assert rep['claims'][1]['status'] == 'proven', rep['claims'][1]
    assert fab.hole_min_gap(st) is None       # one hole, nothing to pair with


def test_pareto_compare_uses_margins_not_weights():
    from fluxpcb.proof import compare
    a = {'margins': {'margins': {'clearance': {'margin_nm': 100},
                                 'width': {'margin_nm': 50}}},
         'margins_box': 'jlc-1oz'}
    b = {'margins': {'margins': {'clearance': {'margin_nm': 100},
                                 'width': {'margin_nm': 40}}}}
    r = compare.compare(a, b)
    assert r['relation'] == 'a_dominates'
    b2 = {'margins': {'margins': {'clearance': {'margin_nm': 90},
                                  'width': {'margin_nm': 60}}}}
    assert compare.compare(a, b2)['relation'] == 'incomparable'


def test_refutation_obligations_and_measure():
    from fluxpcb.proof import repair
    r = {'lean': {'claims': [
            {'id': 'CONN:A', 'status': 'unknown', 'witness':
             'pads not reached: 11111111-1111-1111-1111-111111111111'}]},
         'fab': {'claims': [
            {'id': 'FAB:CLEARANCE', 'status': 'refuted',
             'witness': 'a vs b: gap -100.0 um < 200.0 um'}]},
         'domain': {'claims': []}}
    obl = repair.obligations(r)
    assert len(obl) == 2
    assert obl[0]['targets'] and obl[1]['targets'] == []   # 'a','b' are short
    m = repair.margin_obligations({'margins': {
        'width': {'margin_nm': -20000, 'witness': 'g1: width 180000 nm'}}})
    assert m[0]['delta_nm'] == 20000
    assert repair.terminates(obl, [{'kernel': 'lean', 'delta_nm': None}])
    assert not repair.terminates([], obl)
    assert repair.measure([]) < repair.measure(obl)


def test_macro_shapes_are_single_polygons_in_nm():
    # RoundRect: radius 0.25, corners +/-0.725 x +/-0.6 -> outer 1.95 x 1.70
    sh = gerber._macro_shape('RoundRect', [0.25, 0.725, -0.6, 0.725, 0.6,
                                            -0.725, 0.6, -0.725, -0.6, 0],
                             128525000, 112150000)
    xs = [p[0] for p in sh['pts']]
    ys = [p[1] for p in sh['pts']]
    assert sh['kind'] == 'poly'
    assert abs(min(xs) - (128525000 - 975000)) <= 2000
    assert abs(max(xs) - (128525000 + 975000)) <= 2000
    assert abs(min(ys) - (112150000 - 850000)) <= 2000
    # RotRect: length 1.55, width 0.6, 90 degrees -> bbox 0.6 x 1.55
    sh = gerber._macro_shape('RotRect', [1.55, 0.6, 90], 10000000, 10000000)
    xs = [p[0] for p in sh['pts']]
    ys = [p[1] for p in sh['pts']]
    assert abs(max(xs) - min(xs) - 600000) <= 2000
    assert abs(max(ys) - min(ys) - 1550000) <= 2000


MACRO_GTL = """%FSLAX46Y46*%
%MOMM*%
%AMRoundRect*
0 Rectangle with rounded corners*
4,1,4,$2,$3,$4,$5,$6,$7,$8,$9,$2,$3,0*
1,1,$1+$1,$2,$3*
1,1,$1+$1,$4,$5*
1,1,$1+$1,$6,$7*
1,1,$1+$1,$8,$9*
20,1,$1+$1,$2,$3,$4,$5,0*
%
%ADD10RoundRect,0.250000X0.725000X-0.600000X0.725000X0.600000X-0.725000X0.600000X-0.725000X-0.600000X0*%
G01*
D10*
X1000000Y-1000000D03*
M02*
"""


def test_a_macro_flash_parses_as_one_pad(tmp_path):
    from test_gerber_process import GM1, DRL
    d = tmp_path / 'm'
    d.mkdir()
    (d / 'f.gtl').write_text(MACRO_GTL, encoding='utf-8')
    (d / 'e.gm1').write_text(GM1, encoding='utf-8')
    (d / 'b.drl').write_text(DRL, encoding='utf-8')
    prims = gerber.parse_gerber(str(d / 'f.gtl'), 'F.Cu')
    assert len(prims) == 1
    sh = prims[0].shape
    xs = [p[0] for p in sh['pts']]
    ys = [p[1] for p in sh['pts']]
    assert abs(min(xs) - (1000000 - 975000)) <= 2000
    # parse_gerber works in the plot frame, where the board's +y is negative
    assert abs(min(ys) - (-(1000000 + 850000))) <= 2000
