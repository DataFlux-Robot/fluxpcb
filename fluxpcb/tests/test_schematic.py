"""Schematic -> PCB netlist refinement: partition equality, no net names."""
from __future__ import annotations

from fluxpcb.proof import schematic

COMPS = {'U1': {'value': 'MCU', 'footprint': 'QFP'},
         'R1': {'value': '1k', 'footprint': 'R_0603'}}
NETS = [{'name': 'N1', 'nodes': [('U1', '1'), ('R1', '1')]},
        {'name': 'N2', 'nodes': [('U1', '2'), ('R1', '2')]}]
PINS = {('U1', '1'): 'A', ('R1', '1'): 'A',
        ('U1', '2'): 'B', ('R1', '2'): 'B'}
VALUES = {'U1': 'MCU', 'R1': '1k'}
FPS = {'U1': 'QFP', 'R1': 'R_0603'}


def _run(pins=PINS, values=VALUES, refs=('U1', 'R1'), comps=COMPS):
    return schematic.refine_tables(comps, NETS, set(refs), pins,
                                   values, FPS)


def test_partition_equality_passes():
    obl, extra = _run()
    assert all(o['ok'] for o in obl), [o for o in obl if not o['ok']]
    assert not extra


def test_a_short_between_two_schematic_nets_fails():
    pins = dict(PINS)
    pins[('R1', '2')] = 'A'          # R1.2 shorted to N1's net
    obl, _ = _run(pins)
    ids = {o['id']: o for o in obl}
    assert not ids['PCB:A']['ok']
    assert not ids['SCH:N2']['ok']
    assert 'merges' in ids['PCB:A']['witness']


def test_an_open_splits_the_schematic_net():
    pins = dict(PINS)
    pins[('R1', '1')] = 'C'          # R1.1 on its own net
    obl, _ = _run(pins)
    ids = {o['id']: o for o in obl}
    assert not ids['SCH:N1']['ok']
    assert 'split' in ids['SCH:N1']['witness']


def test_missing_pin_and_footprint_and_value_identity():
    pins = {k: v for k, v in PINS.items() if k != ('R1', '2')}
    obl, _ = _run(pins)
    ids = {o['id']: o for o in obl}
    assert not ids['PIN:R1:2']['ok']
    comps = dict(COMPS)
    comps['U1'] = {'value': 'MCU', 'footprint': ''}
    obl, _ = _run(comps=comps)
    ids = {o['id']: o for o in obl}
    assert not ids['FOOTPRINT:U1']['ok']
    values = dict(VALUES, R1='2k')
    obl, _ = _run(values=values)
    ids = {o['id']: o for o in obl}
    assert not ids['VALUE:R1']['ok']


def test_an_unannotated_netted_footprint_is_identity_failure():
    pins = dict(PINS)
    pins[('REF**', '1')] = 'A'
    refs = ('U1', 'R1', 'REF**')
    obl, _ = _run(pins=pins, refs=refs,
                  values=dict(VALUES, **{'REF**': 'LGA14'}))
    ids = {o['id']: o for o in obl}
    assert not ids['EXTRA:REF**']['ok']
    assert 'LGA14' in ids['EXTRA:REF**']['witness']
