"""FailurePacket — the structured evidence the meta loops read.

One packet per cell (board x arm).  Everything comes from what the run
already recorded: the stage logs, the operator ledgers, the waiver-free
score.  Nothing is re-derived, so the packet and the verdict cannot
disagree.
"""
from __future__ import annotations

import hashlib
import json
import os
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


def new_types(why: str) -> list:
    """['clearance'] out of "new violation types: ['clearance']"."""
    if not why or not why.startswith('new violation types'):
        return []
    if ':' not in why:
        return []
    text = why.split(':', 1)[1].strip()
    try:
        value = json.loads(text.replace("'", '"'))
    except ValueError:
        return [text.strip("[] '")]
    return [str(v) for v in value] if isinstance(value, list) else [text]


def stages_of(cert: dict) -> list:
    """Stage logs of a cert, in the current format or the older one."""
    stages = cert.get('repair')
    if stages is None:
        txn = cert.get('txn') or {}
        stages = ([{'stage': 'repair', 'mode': 'batch', 'log': txn.get('log') or []}]
                  if txn else [])
    return [s for s in (stages or []) if isinstance(s, dict)]


@dataclass
class FailurePacket:
    board: str
    set: str = ''
    arm: str = ''
    arm_digest: str = ''
    plan_digest: str = ''
    policy_digest: str = ''
    capacity_verdict: str = 'unknown'
    outcome: str = ''
    status: str = ''
    score: dict = field(default_factory=dict)
    claims: dict = field(default_factory=dict)
    residue: dict = field(default_factory=dict)
    rounds: list = field(default_factory=list)
    operator_trace: list = field(default_factory=list)
    blockers: list = field(default_factory=list)
    feasibility: dict = field(default_factory=dict)
    denials: list = field(default_factory=list)
    errors: list = field(default_factory=list)
    features: dict = field(default_factory=dict)
    wall_seconds: float = 0.0
    instrument_ok: bool = True
    instrument_problems: list = field(default_factory=list)
    axis: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return asdict(self)

    def digest(self) -> str:
        body = json.dumps(self.as_dict(), sort_keys=True,
                          separators=(',', ':'), default=str)
        return hashlib.sha256(body.encode('utf-8')).hexdigest()[:16]

    # -- construction -------------------------------------------------
    @classmethod
    def from_cert(cls, board: str, cert: dict, split: str = '',
                  cell: dict = None) -> 'FailurePacket':
        """Build from a run cert and, when present, the scored cell."""
        cell = cell or {}
        cert = cert if isinstance(cert, dict) else {}
        claims = cert.get('claims_after') or cert.get('claims_before') or {}
        counts = claims.get('type_counts') or {}
        axis = {}
        for t, n in counts.items():
            axis[axis_of(t)] = axis.get(axis_of(t), 0) + n
        opens = int(claims.get('n_unconnected') or 0)
        if opens and 'unconnected' not in counts:
            axis['routing'] = axis.get('routing', 0) + opens

        rounds, trace, blockers, errors = [], [], [], []
        feas = {}
        for st in stages_of(cert):
            stage = st.get('stage') or 'repair'
            if st.get('errors'):
                errors.append(stage)
            for e in st.get('log') or []:
                if not isinstance(e, dict):
                    continue
                act = e.get('action') or {}
                why = e.get('why') or ''
                ops = act.get('operators_run') or []
                rounds.append({
                    'stage': stage, 'mode': st.get('mode'),
                    'round': e.get('round'), 'decision': e.get('decision'),
                    'why': why, 'operators': ops,
                    'repaired': act.get('repaired', 0),
                    'failed': act.get('failed', 0),
                    'new_types': new_types(why),
                })
                for entry in act.get('ledger') or []:
                    if not isinstance(entry, dict):
                        continue
                    trace.append(dict(entry, stage=stage, round=e.get('round'),
                                      decision=e.get('decision')))
                    if entry.get('first_blocker'):
                        blockers.append(dict(entry['first_blocker'],
                                             stage=stage, board=board))
                    if entry.get('operator') == 'maze_escape':
                        feas['maze_grid_paths'] = (
                            feas.get('maze_grid_paths', 0)
                            + int(entry.get('grid_paths') or 0))
                        feas['maze_exact_rejected'] = (
                            feas.get('maze_exact_rejected', 0)
                            + int(entry.get('exact_rejected') or 0))

        harness = cert.get('harness') or {}
        denials = list(harness.get('denials') or [])
        capacity = (cert.get('capacity') or {}).get('verdict')
        if capacity == 'infeasible':
            feas['capacity_infeasible'] = True
        pre = cert.get('pre_route') or {}
        if pre:
            feas['pre_route_drawn'] = pre.get('drawn_count', 0)
            if pre.get('rejected'):
                feas['pre_route_rejected'] = pre.get('drc_types') or True
        surv = cert.get('pre_route_survivors')
        if surv:
            feas['pre_route_surviving'] = surv.get('surviving', 0)
        if (cert.get('route') or {}).get('timed_out'):
            feas['router_timeout'] = True

        return cls(
            board=board, set=split,
            arm=(cell.get('arm') or (cert.get('arm') or {}).get('name') or ''),
            arm_digest=cell.get('arm_digest') or cert.get('arm_digest') or '',
            plan_digest=cert.get('plan_digest') or '',
            policy_digest=(cell.get('arm_digest')
                           or cert.get('policy_digest') or ''),
            capacity_verdict=capacity or 'unknown',
            outcome=cell.get('outcome') or cert.get('board_worst') or '',
            status=cell.get('status') or '',
            score=cell.get('score') or {},
            claims=claims, residue=cert.get('residue') or {},
            rounds=rounds, operator_trace=trace, blockers=blockers,
            feasibility=feas,
            denials=denials, errors=errors,
            features=cell.get('features') or cert.get('features') or {},
            wall_seconds=float(cell.get('wall_seconds')
                               or cert.get('wall_seconds') or 0.0),
            instrument_ok=bool(cell.get('instrument_ok', True)),
            instrument_problems=list(cell.get('instrument_problems') or []),
            axis=axis,
        )

    @classmethod
    def from_cell(cls, cell: dict, run: str) -> 'FailurePacket':
        """Load the cell's cert from `run` and build the packet."""
        cert = {}
        rel = cell.get('cert')
        if rel:
            try:
                with open(os.path.join(run, rel), encoding='utf-8') as fh:
                    cert = json.load(fh)
            except (OSError, ValueError):
                cert = {}
        return cls.from_cert(cell.get('board', ''), cert,
                             split=cell.get('set', ''), cell=cell)


def load_packets(run: str, cells: list = None) -> list:
    """Packets for every cell of a run (or of the given cells)."""
    if cells is None:
        from .distinguish import load_cells
        cells = load_cells(run)
    return [FailurePacket.from_cell(c, run) for c in cells]


def residue_class(packet: 'FailurePacket') -> list:
    """Violation classes still on the board after everything ran."""
    if packet.outcome == 'clean':
        return []
    out = []
    score = packet.score or {}
    if score.get('unconnected'):
        out.append('unconnected')
    return out + sorted(score.get('errorsByType') or {})


def gradient(packets: list) -> dict:
    """Aggregate packets into the counts a proposer argues from."""
    out = {'cells': len(packets), 'outcome': {}, 'axis': {}, 'residue': {},
           'feasibility': {}, 'rounds': {}, 'ops': {}, 'worst': []}
    for p in packets:
        out['outcome'][p.outcome] = out['outcome'].get(p.outcome, 0) + 1
        for k, n in (p.axis or {}).items():
            out['axis'][k] = out['axis'].get(k, 0) + n
        for c in residue_class(p):
            out['residue'][c] = out['residue'].get(c, 0) + 1
        for k, v in (p.feasibility or {}).items():
            if v:
                out['feasibility'][k] = out['feasibility'].get(k, 0) + 1
        for r in p.rounds or []:
            key = f"{r['stage']}:{r['decision']}"
            out['rounds'][key] = out['rounds'].get(key, 0) + 1
            for op in r['operators']:
                out['ops'][op] = out['ops'].get(op, 0) + 1
    for p in packets:
        if p.outcome != 'clean' and p.status in ('ok', 'budget'):
            score = p.score or {}
            n = (sum((score.get('errorsByType') or {}).values())
                 + (score.get('unconnected') or 0)) if score else 10 ** 6
            out['worst'].append({'board': p.board, 'arm': p.arm,
                                 'outcome': p.outcome, 'residue': n,
                                 'residue_class': residue_class(p)})
    out['worst'].sort(key=lambda w: (-w['residue'], w['board'], w['arm']))
    out['worst'] = out['worst'][:12]
    return out
