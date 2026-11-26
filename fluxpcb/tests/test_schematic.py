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


def test_alias_binds_an_unannotated_ref_without_hiding_identity():
    pins = {('REF**', '1'): 'A', ('REF**', '2'): 'B',
            ('R1', '1'): 'A', ('R1', '2'): 'B'}
    refs = {'REF**', 'R1'}
    obl, extra = schematic.refine_tables(COMPS, NETS, refs, pins, VALUES, FPS,
                                         {'U1': 'REF**'})
    ids = {o['id']: o for o in obl}
    assert ids['COMP:U1']['ok'] and ids['ALIAS:U1']['ok']
    assert ids['SCH:N1']['ok'] and ids['SCH:N2']['ok']
    assert not any(o['id'] == 'EXTRA:REF**' for o in obl)
    # identity is still the schematic's: the symbol has a declared footprint
    # in COMPS, so only the alias is needed; without it U1 would be missing.


def test_declared_no_connects_are_not_unplaced_pins():
    nets = NETS + [{'name': 'unconnected-(U1-NC-PadZ)',
                    'nodes': [('U1', 'Z')]}]
    obl, _ = schematic.refine_tables(COMPS, nets, {'U1', 'R1'}, PINS,
                                     VALUES, FPS)
    ids = {o['id']: o for o in obl}
    assert ids['SCH:unconnected-(U1-NC-PadZ)']['ok']
    assert ids['SCH:unconnected-(U1-NC-PadZ)']['kind'] == 'nc'
    assert not any(o['id'] == 'PIN:U1:Z' for o in obl)


def test_value_may_match_the_footprint_id():
    comps = {'U1': {'value': 'ICM-42688-P', 'footprint': 'ICM-42688-P'},
             'R1': {'value': '1k', 'footprint': 'R_0603'}}
    obl, _ = schematic.refine_tables(comps, NETS, {'U1', 'R1'}, PINS,
                                     values={'U1': 'LGA14', 'R1': '1k'},
                                     fps={'U1': 'ICM-42688-P',
                                          'R1': 'R_0603'})
    assert all(o['ok'] for o in obl if o['kind'] == 'identity'), \
        [o for o in obl if o['kind'] == 'identity' and not o['ok']]
