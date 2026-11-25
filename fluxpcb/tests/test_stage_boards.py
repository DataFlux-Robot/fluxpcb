"""The pcb stage must route the board it owns, not the run's backups.

`pcb/` keeps earlier outputs (`*_p4_routed`, `placed`, `unrouted`, panels).
Routing every one of them made the H743 stage spend ~4x the wall clock on
files that are inputs of nothing.
"""
from __future__ import annotations

import inspect

from fluxpcb import board_files, gates
from fluxpcb.stages import _canonical_boards


def test_only_the_project_board_is_selected(tmp_path):
    b = tmp_path / 'pcb' / 'board'
    b.mkdir(parents=True)
    names = ('h743-fc.kicad_pcb', 'h743-fc_p4_routed.kicad_pcb',
             'placed.kicad_pcb', 'unrouted.kicad_pcb',
             'h743-fc_panel.kicad_pcb')
    for n in names:
        (b / n).write_text('x', encoding='utf-8')
    keep, skipped = _canonical_boards(tmp_path / 'pcb', 'h743-fc')
    assert [p.name for p in keep] == ['h743-fc.kicad_pcb']
    assert len(skipped) == 4


def test_no_named_board_falls_back_to_one(tmp_path):
    b = tmp_path / 'pcb'
    b.mkdir()
    (b / 'board_a.kicad_pcb').write_text('x', encoding='utf-8')
    (b / 'board_b.kicad_pcb').write_text('x', encoding='utf-8')
    keep, skipped = _canonical_boards(b, 'something-else')
    assert len(keep) == 1 and len(skipped) == 1


def test_gate_boards_ignore_backups(tmp_path):
    b = tmp_path / 'pcb' / 'board'
    b.mkdir(parents=True)
    for n in ('h743-fc.kicad_pcb', 'h743-fc_p4_routed.kicad_pcb',
              'placed.kicad_pcb', 'unrouted.kicad_pcb', 'x_panel.kicad_pcb'):
        (b / n).write_text('x', encoding='utf-8')
    keep = board_files.gate_boards(tmp_path)
    assert [p.name for p in keep] == ['h743-fc.kicad_pcb']


def test_all_board_iterating_gates_use_the_helper():
    # the H743 project reported "4 boards" because gates globbed the backups
    for name in ('drc_zero', 'routing_completeness', 'fabrication_netlist',
                 'placement_quality', 'plane_integrity'):
        fn = gates._REGISTRY.get(name) or gates.get_gate(name)
        assert fn is not None, name
        src = inspect.getsource(fn)
        assert 'board_files.gate_boards' in src, name
