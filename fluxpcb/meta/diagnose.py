"""Diagnosis — from execution traces to the variable that is responsible.

HarnessPro's first stage: read the trajectories of one arm and report
which failure patterns occur, on how many boards, with what evidence — and,
for each pattern, which typed variable could be responsible.  That mapping
is the critic a proposer argues from; it never changes behaviour by itself.

Fail-closed: if any packet carries an instrument problem, the diagnosis
says so and a proposer must return nothing.  Tuning parameters around a
broken measurement optimises the bug.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .feedback import FailurePacket, residue_class


@dataclass
class Pattern:
    name: str
    count: int                                   # cells that show it
    boards: list = field(default_factory=list)   # [(board, arm)]
    evidence: dict = field(default_factory=dict)
    variables: list = field(default_factory=list)  # [(name, hint)]


def _add(found: dict, name: str, packet: FailurePacket, hint: str,
         variables: list, **evidence):
    p = found.setdefault(name, Pattern(name=name, count=0))
    p.count += 1
    p.boards.append((packet.board, packet.arm))
    p.variables = _merge(p.variables, variables)
    for k, v in evidence.items():
        bucket = p.evidence.setdefault(k, {})
        bucket[str(v)] = bucket.get(str(v), 0) + 1
    del hint


def _merge(a: list, b: list) -> list:
    names = {n for n, _ in a}
    return a + [(n, h) for n, h in b if n not in names]


def diagnose(packets: list) -> dict:
    """{'instrument_ok': bool, 'instrument_problems': [...], 'patterns': [...]}"""
    problems = sorted({f'{p.board}:{p.arm}: {msg}' for p in packets
                       for msg in (p.instrument_problems or [])})
    broken = sorted({f'{p.board}:{p.arm}: action error in {stage}'
                     for p in packets for stage in (p.errors or [])})
    found: dict = {}

    for p in packets:
        if p.outcome == 'clean' or p.status not in ('ok', 'budget'):
            continue
        for r in p.rounds or []:
            ops = r['operators']
            if r['decision'] == 'rollback' and r['new_types'] and r['repaired']:
                _add(found, 'drc_rejected_verified', p, '',
                     _variables_for_new_types(r['new_types'], ops),
                     stage=r['stage'], types=','.join(sorted(r['new_types'])),
                     ops=','.join(ops))
            elif (r['decision'] == 'rollback' and r['repaired']
                  and not r['new_types']):
                _add(found, 'repaired_without_gain', p, '',
                     [('nudge_steps_um', 'add a finer step'),
                      ('width_cascade', 'add a step above the board minimum')],
                     stage=r['stage'], ops=','.join(ops))
            if ('widen_narrow_tracks' in ops and r['repaired']
                    and 'clearance' in (r['new_types'] or [])):
                _add(found, 'widen_then_clearance', p, '',
                     [('blind_widen', 'False: re-check clearance before widening')],
                     stage=r['stage'])
        feas = p.feasibility or {}
        if (feas.get('maze_grid_paths') or 0) > 0 and (
                feas.get('maze_exact_rejected', 0)
                >= feas.get('maze_grid_paths') or 0):
            _add(found, 'maze_exact_rejected', p, '',
                 [('maze_grid_extra_mm', 'raise: grid cells too close to copper'),
                  ('maze_max_expand', 'raise: the search may be cut short')],
                 rejected=feas.get('maze_exact_rejected'))
        for e in p.operator_trace or []:
            op = e.get('operator')
            if op == 'connect_unconnected' and not e.get('added') and e.get('failed'):
                _add(found, 'connect_empty', p, '',
                     [('corridor_offsets_mm', 'extend with larger detours'),
                      ('via_slot_radii_mm', 'extend with larger slots'),
                      ('jitter_steps_mm', 'extend with larger moves')],
                     failed=e.get('failed'))
            if op == 'nudge_clearance' and not e.get('moved') and e.get('unsupported'):
                _add(found, 'nudge_empty', p, '',
                     [('nudge_steps_um', 'add finer steps'),
                      ('nudge_cap', 'raise')],
                     unsupported=e.get('unsupported'))
            if op == 'short_rip_reroute' and not e.get('rerouted') and e.get('unsupported'):
                _add(found, 'rip_empty', p, '', [('rip_cap', 'raise')],
                     unsupported=e.get('unsupported'))
        if feas.get('pre_route_rejected'):
            _add(found, 'pre_route_rejected', p, '',
                 [('preroute_pair_mm', 'lower: only the most local connections'),
                  ('preroute_max_nets', 'lower')],
                 types=','.join(feas['pre_route_rejected'])
                 if isinstance(feas['pre_route_rejected'], list) else 'unreadable')
        if any('budget exhausted' in d for d in (p.denials or [])):
            _add(found, 'budget_exhausted', p, '', [],
                 arm_field='repair_rounds')
        for cls in residue_class(p):
            _add(found, f'residue:{cls}', p, '', _RESIDUE.get(cls, []),
                 arm=p.arm)

    patterns = sorted(found.values(), key=lambda q: (-q.count, q.name))
    return {'instrument_ok': not problems and not broken,
            'instrument_problems': problems + broken,
            'patterns': patterns,
            'cells': len(packets),
            'failing_cells': sum(1 for p in packets if p.outcome != 'clean')}


def _variables_for_new_types(types: list, ops: list) -> list:
    out = []
    if 'clearance' in types:
        out += [('pad_clearance_strict', 'True: judge track-vs-pad at the netclass clearance'),
                ('nudge_steps_um', 'add finer steps'),
                ('blind_widen', 'False: widening opened a clearance gap')]
    if 'track_dangling' in types or 'via_dangling' in types:
        out.append(('connect_cap', 'lower: cap which opens are attempted'))
    if 'via_diameter' in types or 'hole_clearance' in types or 'hole_to_hole' in types:
        # via geometry comes from the DSN; a violation here is a code or
        # project inconsistency, not a parameter to tune
        out.append(('__instrument__', 'via geometry left the DSN padstacks'))
    del ops
    return out


_RESIDUE = {
    'unconnected': [('connect_cap', 'raise'), ('maze_cap', 'raise'),
                    ('maze_max_expand', 'raise')],
    'clearance': [('nudge_cap', 'raise'), ('nudge_steps_um', 'add finer steps')],
    'shorting_items': [('rip_cap', 'raise')],
    'track_width': [],
    'copper_edge_clearance': [('preroute_pair_mm', 'lower')],
}
