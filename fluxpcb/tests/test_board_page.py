"""The per-board page is a product of the certificate: what is on it is
read back from the data, and data that is not a certificate is reported
rather than drawn as an empty board."""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from fluxpcb.dashboard import board_page as P  # noqa: E402


def _cert():
    return {
        'board': 'demo.kicad_pcb', 'arm': {'name': 'gate+ripup~x'},
        'wall_seconds': 120.0,
        'route': {'routed': True, 'passes': 5, 'seconds': 368.0, 'error': '',
                  'attempts': [{'threads': 2, 'timeout_s': 240, 'routed': False,
                                'timed_out': True, 'seconds': 300.0, 'passes': 0},
                               {'threads': 8, 'timeout_s': 449, 'routed': True,
                                'timed_out': False, 'seconds': 268.0, 'passes': 5}]},
        'claims_after': {'total': 1, 'n_errors': 1, 'n_unconnected': 0},
        'dangling': {'track_dangling': 0, 'via_dangling': 2},
        'repair': [{'stage': 'ripup', 'mode': 'partial-rip', 'committed': 1,
                    'rolled_back': 0, 'denied': 0, 'log': []},
                   {'stage': 'trim', 'mode': 'leaves', 'committed': 2, 'rolled_back': 0,
                    'denied': 0, 'removed': 7, 'rounds': 2, 'log': []}],
        'failures': [
            {'op': 'conflict_reroute', 'outcome': 'ok',
             'item': {'net': '/SD_A', 'type': 'clearance'},
             'attempt': {'anchors': [[141.9, 59.5], [136.6, 64.8]],
                         'plan': {'mode': 'vias:2', 'length_mm': 9.7, 'vias': 2,
                                  'pts': [[141.9, 59.5, 1], [137.5, 63.7, 1],
                                          [137.5, 63.7, 0]]},
                         'conflict_set': []}},
            {'op': 'conflict_reroute', 'outcome': 'no_path',
             'item': {'net': 'CSN', 'type': 'unconnected'},
             'why_not': {'tried': [{'start_layer': 'Top', 'candidates': 0,
                                    'priced': 'crosses', 'crossed': 2, 'set': 0,
                                    'path_mm': 12.3}]},
             'attempt': {'plan': None, 'conflict_set': []}},
            {'op': 'connect_unconnected', 'outcome': 'no_path',
             'item': {'net': 'CSN', 'pos': [56.5, 36.4]},
             'why_not': {'profile': {'blocker': {'kind': 'track', 'short_by_mm': 0.044,
                                                 'move_by_mm': 0.044}}}},
            'not a certificate', None,
        ],
        'residue': {'total': 1},
    }


def test_the_page_shows_what_the_certificate_says():
    page = P.render(_cert())
    assert '1 error(s), 0 open(s)' in page and '2 stub(s) left' in page
    assert 'timed out' in page and '8 thr, cap 449 s: routed' in page
    assert 'removed 7 stub(s) in 2 round(s)' in page
    groups = P.findings(_cert())
    assert set(groups) == {'conflict_reroute', 'connect_unconnected'}
    assert [r['outcome'] for r in groups['conflict_reroute']] == ['ok', 'no_path']
    assert groups['conflict_reroute'][0]['plan'].startswith('vias:2, 9.7 mm, 2 via(s)')
    assert groups['conflict_reroute'][0]['plan_pts'][0] == [141.9, 59.5, 1]
    # the search is told in the certificate's numbers
    (row,) = groups['connect_unconnected']
    assert row['why'] == 'blocked by track, short 0.044 mm, move 0.044 mm'
    tried = P._tried_rows(_cert()['failures'][1])
    assert tried == ['set 0 · Top · 0 free paths · '
                     'cheapest crossing: crosses, 2 item(s), 12.3 mm']
    assert 'cheapest crossing: crosses, 2 item(s)' in page


def test_where_to_look_for_a_finding():
    rows = P.findings(_cert())['conflict_reroute']
    assert P._window_centre(rows[0]) == ((141.9 + 136.6) / 2, (59.5 + 64.8) / 2)
    assert P._window_centre(rows[1]) is None             # nothing on record
    assert P._window_centre({'pos': [1.0, 2.0]}) == (1.0, 2.0)
    assert P._window_centre({'plan_pts': [[0.0, 0.0, 1], [2.0, 4.0, 1]]}) == (1.0, 2.0)


def test_without_failures_the_stage_logs_are_read():
    cert = {'board': 'b', 'route': {}, 'claims_after': {}, 'repair': [
        {'stage': 'repair', 'log': [{'action': {'ledger': [
            {'op': 'nudge_clearance', 'outcome': 'no_path', 'item': {'net': 'N'},
             'why_not': {'margin_mm': -0.2}}]}}]}]}
    groups = P.findings(cert)
    assert list(groups) == ['nudge_clearance']
    assert groups['nudge_clearance'][0]['why'] == 'margin -0.2 mm'
    assert 'No failure on record' in P.render({'board': 'b', 'route': {}, 'claims_after': {}})


def test_data_that_is_not_a_certificate_is_reported(tmp_path):
    bad = tmp_path / 'bad.json'
    bad.write_text('{not json', encoding='utf-8')
    cert, problems = P.load_cert(str(bad))
    assert cert is None and problems[0].startswith('not JSON')
    bad.write_text('[1, 2]', encoding='utf-8')
    assert P.load_cert(str(bad)) == (None, ['not an object'])
    bad.write_text(json.dumps({'board': 'b'}), encoding='utf-8')
    cert, problems = P.load_cert(str(bad))
    assert cert is None and set(problems) == {"missing 'route'", "missing 'claims_after'"}
    assert P.load_cert(str(tmp_path / 'nope.json'))[0] is None
    # a problem passed to render is on the page, in words
    page = P.render(_cert(), None, problems=['board not found: x'])
    assert 'Not rendered as a result' in page and 'board not found: x' in page


def test_names_are_escaped():
    cert = _cert()
    cert['failures'][0]['item']['net'] = '<script>x</script>'
    page = P.render(cert)
    assert '<script>x</script>' not in page
    assert '&lt;script&gt;x&lt;/script&gt;' in page


def test_cli_writes_the_page_next_to_the_certificate(tmp_path, capsys):
    cert = tmp_path / 'cert.json'
    cert.write_text(json.dumps(_cert()), encoding='utf-8')
    assert P.main([str(cert)]) == 0
    out = tmp_path / 'cert.board.html'
    assert out.exists() and 'demo.kicad_pcb' in out.read_text(encoding='utf-8')
    assert P.main([str(tmp_path / 'missing.json')]) == 2
    assert 'not JSON' in capsys.readouterr().err
