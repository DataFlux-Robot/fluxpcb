"""The physical audit must be able to fail — four interventions on real copper.

Each test changes the board (or the declared emitter) in exactly the way one
conclusion is about, and the conclusion has to move:

  * narrow the +9V neck to a quarter -> J crosses the 45 A/mm^2 policy line
  * remove the plane -> the return gap under SPI goes past 3h and the GND
    resistance rises an order of magnitude
  * add the heatsink -> Tj falls, and the energy balance still closes
  * put an emitter harmonic on a pour mode -> the cavity pair becomes a risk

The board is a routed PCBWorld fixture, not in git: the test skips when the
run directory is absent.  FLUXPCB_ROUTED_0376 or the z_lab run layout.
"""
from __future__ import annotations

import glob
import json
import os
import shutil
import sys

import pytest

pytest.importorskip('pcbnew')

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO = os.path.dirname(ROOT)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from fluxpcb.repair_drc import no_assert_dialogs  # noqa: E402
from fluxpcb.tools import cavity_audit as ca  # noqa: E402
from fluxpcb.tools import copper_graph as cg  # noqa: E402
from fluxpcb.tools import phys_audit as pa  # noqa: E402
from fluxpcb.tools import return_audit as ra  # noqa: E402
from fluxpcb.tools import thermal_audit as ta  # noqa: E402

pytestmark = pytest.mark.board
SPEC = os.path.join(REPO, 'projects', 'audit', '0376', 'audit_spec.json')


def _routed():
    env = os.environ.get('FLUXPCB_ROUTED_0376')
    if env and os.path.exists(env):
        return env
    for root in ('E:/DATA/vscode/ZCODE/z_lab/fp',
                 os.path.expanduser('~/z_lab/fp')):
        hits = sorted(glob.glob(os.path.join(root, '*_run', 'runs', '*',
                                             '0376*', '0376*.kicad_pcb')))
        if hits:
            return hits[0]
    pytest.skip('no routed 0376 fixture (FLUXPCB_ROUTED_0376)')


@pytest.fixture(scope='module')
def board(tmp_path_factory):
    no_assert_dialogs()
    return _routed()


@pytest.fixture(scope='module')
def spec():
    with open(SPEC, encoding='utf-8') as f:
        return json.load(f)


def _copy(tmp, src, name):
    dst = os.path.join(str(tmp), name)
    shutil.copyfile(src, dst)
    pro = src[:-len('.kicad_pcb')] + '.kicad_pro'
    if os.path.exists(pro):
        shutil.copyfile(pro, dst[:-len('.kicad_pcb')] + '.kicad_pro')
    return dst


def test_the_neck_narrows_and_j_crosses_the_line(board, tmp_path):
    base = cg.extract(board, cell_mm=0.2)
    ctrl = cg.solve(base, '+9V', 0.6, v_rail=9.0)
    assert ctrl['status'] == 'ok', ctrl
    assert ctrl['j_robust_a_mm2'] < 45.0, ctrl['j_robust_a_mm2']
    # same board, same current, the +9V track a quarter as wide
    import pcbnew
    dst = _copy(tmp_path, board, 'narrow.kicad_pcb')
    b = pcbnew.LoadBoard(dst)
    net = b.FindNet('+9V')
    n = 0
    for t in b.GetTracks():
        if t.GetNetCode() == net.GetNetCode():
            t.SetWidth(int(t.GetWidth() * 0.25))
            n += 1
    assert n > 0
    pcbnew.SaveBoard(dst, b)
    cop = cg.extract(dst, cell_mm=0.2)
    r = cg.solve(cop, '+9V', 0.6, v_rail=9.0)
    assert r['status'] == 'ok', r
    assert r['j_robust_a_mm2'] > 45.0, r['j_robust_a_mm2']
    assert r['j_robust_a_mm2'] > 3.0 * ctrl['j_robust_a_mm2']


def test_without_the_plane_the_return_gap_explodes(board, tmp_path):
    base = cg.extract(board, cell_mm=0.2)
    assert not base.zones, 'the fixture already has a plane: not the control'
    plain = ra.availability(base, 'Net-(U3-Pad13)', h_mm=1.6)
    assert plain['status'] == 'ok', plain
    assert plain['max_gap_mm'] > 3 * 1.6, plain['max_gap_mm']
    poured_path = os.path.join(str(tmp_path), 'poured.kicad_pcb')
    pr = pa.pour_plane(board, poured_path)
    assert pr['status'] == 'ok', pr
    poured = cg.extract(poured_path, cell_mm=0.2)
    withp = ra.availability(poured, 'Net-(U3-Pad13)', h_mm=1.6)
    assert withp['status'] == 'ok', withp
    assert withp['max_gap_mm'] < 3 * 1.6, withp['max_gap_mm']
    assert withp['gnd_share_mean'] > 3 * plain['gnd_share_mean']
    # and the DC return resistance between the same two footprints drops
    a = ra.solve_return(base, 'Net-(U3-Pad13)', 'U3', 'U4', 0.02, h_mm=1.6)
    b = ra.solve_return(poured, 'Net-(U3-Pad13)', 'U3', 'U4', 0.02, h_mm=1.6)
    assert a['status'] == 'ok' and b['status'] == 'ok', (a, b)
    assert b['gnd_resistance_mohm'] < 0.3 * a['gnd_resistance_mohm']


def test_the_heatsink_lowers_tj_and_keeps_the_balance(board, spec):
    cop = cg.extract(board, cell_mm=0.2)
    r = ta.run(cop, spec)
    assert r['status'] == 'ok', r
    assert r['base']['balance_err'] < 0.02, r['base']['balance_err']
    assert r['with_sink']['balance_err'] < 0.02, r['with_sink']['balance_err']
    u1 = next(p for p in r['parts'] if p['ref'] == 'U1')
    assert u1['tj_sink']['lumped_c'] < 0.6 * u1['tj_base']['lumped_c'], u1
    assert u1['tj_drop_c'] > 40, u1['tj_drop_c']
    assert r['with_sink']['tmax_c'] < r['base']['tmax_c']


def test_an_emitter_on_a_mode_becomes_a_risk(board, tmp_path):
    poured_path = os.path.join(str(tmp_path), 'poured2.kicad_pcb')
    pr = pa.pour_plane(board, poured_path)
    assert pr['status'] == 'ok', pr
    cop = cg.extract(poured_path, cell_mm=0.2)
    first = ca.run(cop, {'plane_pair': True, 'emitters': []}, er=4.4)
    assert first['status'] == 'ok', first
    f0 = first['modes'][0]['f_hz']
    phi = first['mode_fields'][0]['phi']
    import numpy as np
    j, i = np.unravel_index(int(np.argmax(np.abs(phi))), phi.shape)
    cell = first['mode_fields'][0]['cell_mm']
    at = (cop.x0_mm + (i + 0.5) * cell, cop.y0_mm + (j + 0.5) * cell)
    second = ca.run(cop, {'plane_pair': True, 'tol': 0.02,
                          'emitters': [{'net': 'CLK', 'f_hz': f0, 'harmonics': 1,
                                        'at': at}]}, er=4.4)
    assert second['status'] == 'ok', second
    assert second['pairs'], 'the harmonic on the mode was not paired'
    assert second['pairs'][0]['risk'] is True, second['pairs'][0]
    # and a harmonic far from every mode does not pair
    third = ca.run(cop, {'plane_pair': True, 'tol': 0.02,
                         'emitters': [{'net': 'CLK', 'f_hz': f0 * 3.7,
                                       'harmonics': 1, 'at': at}]}, er=4.4)
    assert third['status'] == 'ok'
    assert not third['pairs'], third['pairs']
