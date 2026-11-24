"""FailurePacket — the structured signal C1 optimizes against.

One packet per board run.  Everything comes from evidence already
produced by the bench (claims/residue/txn/plan/scope); nothing is
re-derived, so the packet and the verdict cannot disagree.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field

ROUTING_TYPES = frozenset({
    'clearance', 'track_width', 'shorting_items', 'unconnected',
    'via_dangling', 'track_dangling', 'via_diameter', 'annular_width',
    'hole_clearance', 'hole_to_hole', 'copper_edge_clearance',
})
LIBRARY_TYPES = frozenset({
    'lib_footprint_issues', 'lib_footprint_mismatch',
    'footprint_type_mismatch', 'courtyard', 'padstack',
})
SILK_TYPES = frozenset({
    'silk_over_copper', 'silk_overlap', 'silk_edge_clearance',
    'text_height', 'text_thickness',
})


def axis_of(vtype: str) -> str:
    if vtype in ROUTING_TYPES:
        return 'routing'
    if vtype in LIBRARY_TYPES:
        return 'library'
    if vtype in SILK_TYPES:
        return 'silk'
    return 'other'


@dataclass
class FailurePacket:
    board: str
    split: str = ''
    plan_digest: str = ''
    policy_digest: str = ''
    capacity_verdict: str = 'unknown'
    residue: dict = field(default_factory=dict)
    claims: dict = field(default_factory=dict)
    txn: list = field(default_factory=list)
    blockers: list = field(default_factory=list)
    feasibility: dict = field(default_factory=dict)
    operator_trace: list = field(default_factory=list)
    axis: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return asdict(self)

    def digest(self) -> str:
        body = json.dumps(self.as_dict(), sort_keys=True,
                          separators=(',', ':'))
        return hashlib.sha256(body.encode('utf-8')).hexdigest()

    @classmethod
    def from_cert(cls, board: str, cert: dict, split: str = '') -> "FailurePacket":
        claims = cert.get('claims_after') or cert.get('claims_before') or {}
        residue = cert.get('residue') or {}
        txn = (cert.get('txn') or {})
        entries = txn.get('log') or []
        feasibility = {}
        blockers = []
        capacity = (cert.get('capacity') or {}).get('verdict')
        if capacity == 'infeasible':
            feasibility['capacity_infeasible'] = True
        trace = []
        for e in entries:
            if not isinstance(e, dict):
                continue
            act = e.get('action') or {}
            for op in act.get('operators_run') or []:
                trace.append({'capability': op,
                              'decision': e.get('decision')})
            why = e.get('why') or ''
            if 'blocked' in why:
                blockers.append({'why': why})
        for e in (cert.get('ledger') or []):
            if not isinstance(e, dict):
                continue
            if e.get('operator') == 'maze_escape':
                feasibility['maze_grid_paths'] = e.get('grid_paths', 0)
                feasibility['maze_exact_rejected'] = e.get(
                    'exact_rejected', 0)
                if e.get('first_blocker'):
                    blockers.append(e['first_blocker'])
                trace.append({'capability': 'maze_escape',
                              'added': e.get('added', 0)})
        axis = {}
        counts = claims.get('type_counts') or {}
        for t, n in counts.items():
            axis[axis_of(t)] = axis.get(axis_of(t), 0) + n
        opens = int(claims.get('n_unconnected') or 0)
        if opens and 'unconnected' not in counts:
            axis['routing'] = axis.get('routing', 0) + opens
        return cls(
            board=board, split=split,
            plan_digest=cert.get('plan_digest') or '',
            policy_digest=cert.get('policy_digest') or '',
            capacity_verdict=capacity or 'unknown',
            residue=residue, claims=claims, txn=entries,
            blockers=blockers, feasibility=feasibility,
            operator_trace=trace, axis=axis,
        )


def gradient(packets: list) -> dict:
    """Aggregate packets into the counts C1 proposes against."""
    out = {'boards': len(packets), 'axis': {}, 'feasibility': {},
           'blockers': 0, 'ops': {}, 'residue': {}, 'worst': []}
    for p in packets:
        c = p.claims or {}
        for key, vtype in (('opens', 'unconnected'),
                           ('shorts', 'shorting_items'),
                           ('clearance', 'clearance'),
                           ('width', 'track_width')):
            n = ((c.get('type_counts') or {}).get(vtype, 0)
                 + (c.get('n_unconnected', 0) if vtype == 'unconnected'
                    and vtype not in (c.get('type_counts') or {})
                    else 0))
            if n:
                out['residue'][key] = out['residue'].get(key, 0) + n
        for k, n in (p.axis or {}).items():
            out['axis'][k] = out['axis'].get(k, 0) + n
        for k, v in (p.feasibility or {}).items():
            if v:
                out['feasibility'][k] = out['feasibility'].get(k, 0) + 1
        out['blockers'] += len(p.blockers or [])
        for t in (p.operator_trace or []):
            op = t.get('capability')
            if op:
                out['ops'][op] = out['ops'].get(op, 0) + 1
        clean = (p.claims or {}).get('total') == 0
        if not clean:
            out['worst'].append({'board': p.board,
                                 'axis': p.axis,
                                 'capacity': p.capacity_verdict,
                                 'blockers': len(p.blockers or [])})
    out['worst'].sort(key=lambda w: (-sum(w['axis'].values()),
                                     w['board']))
    out['worst'] = out['worst'][:8]
    return out
