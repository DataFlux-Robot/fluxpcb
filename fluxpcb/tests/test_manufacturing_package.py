"""Panelization and IPC-7351 land patterns: the package has to be a real board.

The panel is only useful if it is a `.kicad_pcb` a fab can parse; the
land-pattern check is only useful if it compares against the nominal rather
than "smaller than 0.2 mm".  The board-level panel test skips without
pcbnew/fixtures; the pure checks always run.
"""
from __future__ import annotations

import os

import pytest

from fluxpcb.mfg import ipc7351


def test_land_pattern_accepts_nominal_and_rejects_half_size():
    ok = ipc7351.check_footprint('R_0603_1608Metric', [(0.85, 0.80)])
    assert ok['package'] == '0603' and ok['bad'] is False, ok
    bad = ipc7351.check_footprint('R_0603_1608Metric', [(0.40, 0.40)])
    assert bad['bad'] is True and bad['deviation'][0] < -0.5, bad
    unknown = ipc7351.check_footprint('MyCustomThing', [(1.0, 1.0)])
    assert unknown['skipped'] is True


def test_package_name_matching():
    assert ipc7351.package_of('Package_TO_SOT_SMD:SOT-23') == 'sot-23'
    assert ipc7351.package_of('C_0805_2012Metric') == '0805'
    assert ipc7351.package_of('BGA-256') is None


def test_fabrication_netlist_is_wired_into_the_pipeline():
    from pathlib import Path
    y = Path(__file__).resolve().parents[2] / 'flux.yaml'
    txt = y.read_text(encoding='utf-8')
    assert 'fabrication_netlist' in txt
    assert 'power_budget' in txt


def test_panel_is_a_real_board_with_four_copies(tmp_path):
    pytest.importorskip('pcbnew')
    from fluxpcb.tests import boards_local as F
    from fluxpcb.kicadgen.panelization import Panelizer, PanelSpec

    pcb = F.board('0023', tmp_path)
    import pcbnew
    b = pcbnew.LoadBoard(pcb)
    bb = b.GetBoardEdgesBoundingBox()
    w, h = bb.GetWidth() / 1e6, bb.GetHeight() / 1e6
    n_fp = len(list(b.GetFootprints()))
    p = Panelizer(PanelSpec(width=150, height=120, frame_width=5, gap=2))
    panel = p.panelize([(w, h, pcb)] * 4, panel_width=150, panel_height=120)
    out = tmp_path / 'panel_4up.kicad_pcb'
    r = p.generate_kicad_panel(panel, str(out), src_path=pcb)
    assert r['status'] == 'ok', r
    assert r['board_count'] == 4 and r['fiducial_count'] == 3
    assert out.exists() and os.path.getsize(out) > 1000
    b2 = pcbnew.LoadBoard(str(out))
    assert len(list(b2.GetFootprints())) == 4 * n_fp + r['tooling_hole_count']
