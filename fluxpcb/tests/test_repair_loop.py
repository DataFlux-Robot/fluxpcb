"""Open-net targeting and the tactic chain of the proof-directed repair loop."""
from __future__ import annotations

import json

from fluxpcb.proof import repair, repair_loop
from test_proof_gate import _two_net_state


def test_open_nets_finds_the_unreached_net():
    st = _two_net_state(False)          # N connected, M open
    assert repair_loop.open_nets(st) == ['M']
    st = _two_net_state(True)
    assert repair_loop.open_nets(st) == []


# ---------------------------------------------------------------- chain tests
# The control flow is tested without KiCad: states, reports and the scoped
# route are faked, so what is under test is exactly the adoption rule —
# every tactic passes the same gate, and only a strict measure descent is
# allowed to replace the working board.
def _report(open_nets):
    return {'lean': {'claims': [{'id': f'CONN:{n}', 'status': 'unknown',
                                 'witness': ''} for n in open_nets]},
            'fab': {'claims': [{'id': 'FAB:clearance', 'status': 'unknown',
                                'witness': 'zones'}]},
            'domain': {'claims': []}}


def test_chain_falls_back_after_a_backend_crash(monkeypatch, tmp_path):
    board = tmp_path / 'b.kicad_pcb'
    board.write_text('(kicad_pcb)', encoding='utf-8')
    cand = tmp_path / 'cand_embedded.kicad_pcb'
    cand.write_text('(kicad_pcb)', encoding='utf-8')
    states = {board.name: {'digest': 'd0', 'open': ['M', 'P'], 'items': []},
              cand.name: {'digest': 'd1', 'open': ['M'], 'items': []}}
    seen = []

    monkeypatch.setattr(repair_loop.S, 'extract',
                        lambda p, rules: dict(states[__import__('pathlib')
                                              .Path(p).name]))
    monkeypatch.setattr(repair_loop, '_report',
                        lambda st, rules: _report(st['open']))
    monkeypatch.setattr(repair_loop, 'open_nets',
                        lambda st, rep=None: list(st['open']))
    monkeypatch.setattr(repair_loop, 'missing_pads',
                        lambda st: st.get('pads', len(st['open'])))

    def fake_scope(src, nets, rules, workdir=None, passes=1, mode='publish',
                   tactic='freerouting', log=print):
        seen.append(tactic)
        if tactic == 'freerouting':
            return {'ok': False, 'error': 'FR crashed (NPE)', 'seconds': 1.0}
        return {'ok': True, 'board': str(cand), 'seconds': 2.0,
                'reasons': [], 'step': {'carried': 0, 'touched': 7}}

    monkeypatch.setattr(repair_loop.router, 'route_scope', fake_scope)
    r = repair_loop.repair_loop(board, {}, rounds=2, workdir=tmp_path / 'w',
                                log=lambda *a: None)
    rod = r['rounds'][0]
    assert seen[:2] == ['freerouting', 'embedded']
    assert rod['route']['tactic'] == 'embedded'
    assert rod['measure'][0] == 3 and rod['after']['measure'][0] == 2
    assert rod['tactics'][0]['ok'] is False
    assert rod['tactics'][1]['adopted'] is True
    assert r['final_board'] == str(cand)
    # second round: the remaining open net is routed by the next candidate
    assert r['rounds'][1]['n_open'] == 1


def test_chain_rejects_a_candidate_without_descent(monkeypatch, tmp_path):
    board = tmp_path / 'b.kicad_pcb'
    board.write_text('(kicad_pcb)', encoding='utf-8')
    cand = tmp_path / 'cand.kicad_pcb'
    cand.write_text('(kicad_pcb)', encoding='utf-8')
    same = {'digest': 'd1', 'open': ['M', 'P'], 'items': []}
    monkeypatch.setattr(repair_loop.S, 'extract',
                        lambda p, rules: dict(same))
    monkeypatch.setattr(repair_loop, '_report',
                        lambda st, rules: _report(st['open']))
    monkeypatch.setattr(repair_loop, 'open_nets',
                        lambda st, rep=None: list(st['open']))
    monkeypatch.setattr(repair_loop, 'missing_pads',
                        lambda st: st.get('pads', len(st['open'])))

    def fake_scope(src, nets, rules, workdir=None, passes=1, mode='publish',
                   tactic='freerouting', log=print):
        return {'ok': True, 'board': str(cand), 'seconds': 1.0,
                'reasons': [], 'step': {'carried': 0, 'touched': 1}}

    monkeypatch.setattr(repair_loop.router, 'route_scope', fake_scope)
    r = repair_loop.repair_loop(board, {}, rounds=1, workdir=tmp_path / 'w',
                                log=lambda *a: None)
    rod = r['rounds'][0]
    assert 'no tactic produced a strict descent' in rod['reason']
    assert all(t.get('adopted') is None for t in rod['tactics'])
    assert r['final_board'] == str(tmp_path / 'w' / 'b.kicad_pcb')


def test_embedded_backend_gets_the_board_clearance(monkeypatch, tmp_path):
    """The in-process router must be told the board's fab rule, not a
    hard-coded 0.25 mm centre-line assumption."""
    from fluxpcb import routing
    from fluxpcb.proof import router

    script = tmp_path / 'embedded.py'
    script.write_text('', encoding='utf-8')
    calls = {}

    def fake_pairs(kipy, pcb, log=print):
        return {'pairs': ['N|0|0|0|1|1|0'], 'ratsnest': 1}

    def fake_kipy(kipy, script_path, *args, timeout=600):
        calls['args'] = args
        return 0, 'ROUTES_ADDED=1 SKIPPED=0'

    monkeypatch.setattr(routing, 'ratsnest_state', fake_pairs)
    monkeypatch.setattr(routing, '_kipy', fake_kipy)
    monkeypatch.setattr(routing, '_EMBEDDED', script)
    work = tmp_path / 'w'
    work.mkdir()
    dst = work / 'x.kicad_pcb'
    dst.write_text('', encoding='utf-8')
    res = router._route_backend('embedded', 'kicad-cli', 'kipy', dst, work,
                                {'clearance': 101700}, 1, lambda *a: None)
    assert res['ok']
    cls = json.loads((work / 'route_classes.json').read_text(encoding='utf-8'))
    assert abs(cls['clearance_mm'] - 0.1017) < 1e-9
    assert calls['args'][1].endswith('ratsnest_pairs.txt')


def test_keep_tactic_maps_to_the_embedded_backend():
    from fluxpcb.proof import router
    assert router._tactic_base('embedded_keep') == 'embedded'
    assert router._tactic_base('freerouting_keep') == 'freerouting'
    assert router._tactic_base('embedded') == 'embedded'
    assert router._tactic_base('freerouting') == 'freerouting'


def test_pad_level_progress_is_a_descent():
    """Equal obligations, fewer missing pads: adopted.  The net-level count
    alone would have called this a fixed point."""
    obl = [{'kernel': 'lean', 'status': 'unknown', 'delta_nm': None}]
    assert repair.terminates(obl, obl, 10, 9)
    assert not repair.terminates(obl, obl, 9, 10)
    assert not repair.terminates(obl, obl, 9, 9)


def test_obligation_count_dominates_the_pad_key():
    """A closed net wins even when another open net got worse: the pad key
    only breaks ties, so a new obligation can never hide behind it."""
    one = [{'kernel': 'lean', 'status': 'unknown', 'delta_nm': None}]
    assert repair.terminates(one * 2, one, 1, 100)
    assert not repair.terminates(one, one * 2, 100, 1)


def test_new_violation_ids_names_only_added_items():
    """The referee's refusal is constructive: only items the candidate added
    are eligible for deletion; pre-existing copper is never touched."""
    report = {'violations': [
        {'severity': 'error', 'items': [{'uuid': 'a'}, {'uuid': 'new1'}]},
        {'severity': 'warning', 'items': [{'uuid': 'warn'}]},
        {'severity': 'error', 'items': [{'uuid': 'pre'}]}]}
    assert repair_loop._new_violation_ids(report, {'a', 'pre'}) == {'new1'}
    assert repair_loop._new_violation_ids({'violations': []}, {'a'}) == set()
