"""Assembly set + one freeze manifest: what ships, from what, with which tools.

The package is only a release if the files and the description travel
together: this pins the classification (DNP/THT never in the CPL), the
quantity arithmetic (panels vs pieces vs sets), and the manifest's hashes.
"""
from __future__ import annotations

from fluxpcb.mfg import assembly
from fluxpcb.mfg import release_manifest as rm
from fluxpcb.models import RunReport


BOM = [
    {'ref': 'R1', 'value': '10k', 'footprint': '0402', 'mpn': 'X'},
    {'ref': 'C1', 'value': '100n', 'footprint': '0603'},
    {'ref': 'U1', 'value': 'MCU', 'footprint': 'QFN-32'},
    {'ref': 'J1', 'value': 'HDR', 'footprint': 'PinHeader_1x04'},
    {'ref': 'D1', 'value': 'LED', 'footprint': '0805', 'dnp': True},
    {'ref': 'X1', 'value': '?', 'footprint': ''},
]


def test_classification_is_explicit_about_unknowns():
    c = assembly.classify(BOM)
    assert 'R1' in c['smt'] and 'U1' in c['smt']
    assert 'J1' in c['tht']
    assert 'D1' in c['dnp']
    assert 'X1' in c['unknown']


def test_cpl_is_the_smt_subset_and_reports_missing_positions(tmp_path):
    pos = tmp_path / 'positions.csv'
    pos.write_text('Ref,Val,Package,PosX,PosY,Rot,Side\n'
                   'R1,10k,0402,1,2,0,top\nU1,MCU,QFN-32,3,4,90,top\n'
                   'J1,HDR,PinHeader,5,6,0,top\n', encoding='utf-8')
    rows, missing = assembly.cpl_rows(BOM, str(pos))
    refs = [r['ref'] for r in rows]
    assert 'R1' in refs and 'U1' in refs
    assert 'J1' not in refs and 'D1' not in refs      # THT / DNP
    assert 'C1' in missing[0]                          # SMT but no position


def test_quantity_units_are_not_conflated():
    q = assembly.quantity({'manufacturing': {'panels': 2,
                                             'pieces_per_panel': 4,
                                             'pieces_per_set': 2}})
    assert q['finished_pieces'] == 8 and q['sets'] == 4


def _project(tmp_path):
    (tmp_path / 'pcb' / 'fab').mkdir(parents=True)
    (tmp_path / 'pcb' / 'board.kicad_pcb').write_bytes(b'(kicad_pcb)')
    (tmp_path / 'pcb' / 'fab' / 'board-F_Cu.gbr').write_bytes(b'G04 gerber*')
    (tmp_path / 'pcb' / 'fab' / 'positions.csv').write_text('Ref\nR1\n',
                                                           encoding='utf-8')
    (tmp_path / 'bom.json').write_text('[]', encoding='utf-8')
    (tmp_path / 'pcb' / 'fab_package.json').write_text(
        '{"waivers": [{"rule": "x"}], "truthnet": {"ok": true}}',
        encoding='utf-8')
    return tmp_path


def test_manifest_hashes_every_artifact_and_the_tools(tmp_path):
    p = _project(tmp_path)
    m = rm.build(p)
    assert m['schema'] == rm.SCHEMA
    kinds = {a['kind'] for a in m['artifacts']}
    assert {'gerber', 'csv', 'board', 'bom'} <= kinds, kinds
    assert all(len(a['sha256']) == 64 for a in m['artifacts'])
    assert m['tools']['python']
    assert m['accepted_exceptions'].get('truthnet')
    before = [a['sha256'] for a in m['artifacts'] if a['kind'] == 'gerber'][0]
    (p / 'pcb' / 'fab' / 'board-F_Cu.gbr').write_bytes(b'G04 changed*')
    m2 = rm.build(p)
    after = [a['sha256'] for a in m2['artifacts'] if a['kind'] == 'gerber'][0]
    assert before != after
    stamp = rm.stamp(p)
    assert stamp['sha256'] and stamp['n_files'] >= 4


def test_a_package_without_manifest_is_not_released():
    rep = RunReport(project='t')
    rep.fab_export = {'ok': True, 'files': 3, 'errors': []}
    chk = rep.release_check('')
    assert chk['manifest_ok'] is False
    assert any('发布清单' in r for r in chk['reasons'])
    assert rep.released is False
    rep.fab_export = {'ok': True, 'manifest': {'path': 'x', 'sha256': 'y'}}
    assert rep.release_check('')['manifest_ok'] is True
