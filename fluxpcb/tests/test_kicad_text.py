"""The independent text parser: same file, second witness."""
from __future__ import annotations

from fluxpcb.proof import kicad_text, state as S

BOARD = '''(kicad_pcb (version 20260206)
  (footprint "x" (layer "F.Cu") (at 10 20 90)
    (property "Reference" "U1")
    (pad "1" smd rect (at -0.75 0 90) (size 0.8 0.75)
      (layers "F.Cu") (net "+3V3") (uuid "pad-1")))
  (segment (start 1 2) (end 3 4) (width 0.25) (layer "F.Cu")
    (net "N") (uuid "seg-1"))
  (arc (start 5 5) (mid 6 6) (end 7 5) (width 0.2) (layer "F.Cu")
    (net "N") (uuid "arc-1"))
  (via (at 5 6) (size 0.6) (drill 0.4) (layers "F.Cu" "B.Cu")
    (net "N") (uuid "via-1"))
)
'''


def _state():
    st = {'board': 't', 'layers': ['F.Cu', 'B.Cu'],
          'items': [
              {'id': 'pad-1', 'net': '+3V3', 'layers': ['F.Cu'],
               'ref': 'U1', 'shape': {'kind': 'circle', 'cx': 10000000,
                                      'cy': 20750000, 'r': 400000}},
              {'id': 'seg-1', 'net': 'N', 'layers': ['F.Cu'],
               'shape': {'kind': 'seg', 'x1': 1000000, 'y1': 2000000,
                         'x2': 3000000, 'y2': 4000000, 'w': 250000}},
              {'id': 'arc-1', 'net': 'N', 'layers': ['F.Cu'],
               'shape': {'kind': 'seg', 'x1': 5000000, 'y1': 5000000,
                         'x2': 7000000, 'y2': 5000000, 'w': 200000}},
              {'id': 'via-1', 'net': 'N', 'layers': ['F.Cu', 'B.Cu'],
               'drill': 400000, 'span': 'F.Cu-B.Cu',
               'shape': {'kind': 'circle', 'cx': 5000000, 'cy': 6000000,
                         'r': 300000}}],
          'rules': {}, 'netPads': {'N': ['seg-1', 'arc-1', 'via-1'],
                                   '+3V3': ['pad-1']},
          'footprints': {'U1': ['pad-1']}}
    return S.freeze(st)


def test_text_parse_matches_the_state(tmp_path):
    p = tmp_path / 't.kicad_pcb'
    p.write_text(BOARD, encoding='utf-8')
    r = kicad_text.compare(str(p), st=_state())
    assert r['ok'], r['mismatches']
    assert r['counts']['pads'] == 1 and r['counts']['arcs'] == 1
    assert r['arcs_approximated'] == 1


def test_a_shifted_pad_is_a_mismatch(tmp_path):
    p = tmp_path / 't.kicad_pcb'
    p.write_text(BOARD, encoding='utf-8')
    st = _state()
    st['items'][0] = dict(st['items'][0],
                          shape={'kind': 'circle', 'cx': 10000000,
                                 'cy': 20000000, 'r': 400000})
    st = S.freeze(st)
    r = kicad_text.compare(str(p), st=st)
    assert not r['ok']
    assert any(m['field'] == 'position' for m in r['mismatches'])


def test_quoted_strings_with_spaces():
    toks = list(kicad_text.tokenize('(a "hello world" b)'))
    assert toks == ['(', 'a', 'hello world', 'b', ')']


def test_duplicate_uuid_is_reported(tmp_path):
    dup = BOARD.replace('(uuid "via-1")', '(uuid "seg-1")')
    p = tmp_path / 'dup.kicad_pcb'
    p.write_text(dup, encoding='utf-8')
    r = kicad_text.compare(str(p), st=_state())
    assert not r['ok']
    assert any(m['field'] == 'duplicate_uuid' for m in r['mismatches'])
