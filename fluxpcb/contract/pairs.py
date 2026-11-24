"""pairs — Rule-Spec pairs (declarative <-> executable <-> validation).

Following the AuXSpec pattern: a rule is only *covered* when it has
  * a declarative predicate (what the standard/JLC text says),
  * an executable check (a DRC type that can fail), and
  * a validation that the two agree on a boundary domain.

A pair without validation is `mapped`, not `validated`: the DRC type exists
but we have not shown it fires exactly when the predicate says it should.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional


@dataclass(frozen=True)
class RuleSpecPair:
    rule_id: str
    declarative: str
    kicad_types: tuple
    direction: str = 'min'          # min: measured >= limit
    evidence: str = ''              # mutation op / oracle that validates it
    predicate: Optional[Callable[[float, float], bool]] = None

    def declared_ok(self, measured: float, limit: float) -> bool:
        if self.predicate is not None:
            return bool(self.predicate(measured, limit))
        return measured >= limit if self.direction == 'min' else measured <= limit

    def executable_ok(self, drc_types) -> bool:
        """True iff no mapped DRC type is present."""
        return not (set(self.kicad_types) & set(drc_types or ()))


def _min(a, b):
    return a >= b


_SEED = [
    RuleSpecPair('kicad.clearance',
                 'copper-to-copper gap >= netclass clearance',
                 ('clearance',), evidence='mutation:move', predicate=_min),
    RuleSpecPair('kicad.track_width',
                 'track width >= netclass/board minimum',
                 ('track_width',), evidence='mutation:thin', predicate=_min),
    RuleSpecPair('kicad.hole_clearance',
                 'hole-to-copper gap >= min_hole_clearance',
                 ('hole_clearance',), evidence='oracle:jlc', predicate=_min),
    RuleSpecPair('kicad.hole_to_hole',
                 'hole-to-hole gap >= min_hole_to_hole',
                 ('hole_to_hole',), evidence='probe:hole_to_hole', predicate=_min),
    RuleSpecPair('kicad.copper_edge_clearance',
                 'copper-to-board-edge gap >= min_copper_edge_clearance',
                 ('copper_edge_clearance', 'edge_clearance'),
                 evidence='probe:copper_edge_clearance', predicate=_min),
    RuleSpecPair('kicad.min_via_annular_width',
                 'via annular ring >= min_via_annular_width',
                 ('annular_width',), evidence='oracle:jlc', predicate=_min),
    RuleSpecPair('kicad.via_drill',
                 'via drill >= min_through_drill',
                 ('drill_out_of_range', 'hole_size'),
                 evidence='mutation:via_drill', predicate=_min),
    RuleSpecPair('kicad.track_dangling',
                 'no track ends without a connection',
                 ('track_dangling',), evidence=''),
    RuleSpecPair('kicad.via_dangling',
                 'no via ends without a connection',
                 ('via_dangling',), evidence=''),
    RuleSpecPair('kicad.shorting_items',
                 'no copper connects two different nets',
                 ('shorting_items',), evidence='mutation:short'),
]


@dataclass
class RulePairRegistry:
    pairs: dict

    @classmethod
    def default(cls) -> 'RulePairRegistry':
        return cls({p.rule_id: p for p in _SEED})

    def ids(self) -> set:
        return set(self.pairs)

    def statuses(self, mutation_summary: dict = None,
                 oracle_summary: dict = None) -> dict:
        """Classify each pair from evidence files.

        mutation_summary: {op: {'crossed': n, 'killed': n}}
        oracle_summary:   {'detected_types': {drc_type: count}}
        """
        mutation_summary = mutation_summary or {}
        detected = (oracle_summary or {}).get('detected_types', {})
        out = {}
        for rid, p in self.pairs.items():
            st = 'mapped'
            ev = p.evidence or ''
            if ev.startswith('mutation:'):
                op = ev.split(':', 1)[1]
                m = mutation_summary.get(op) or {}
                crossed = m.get('crossed', 0)
                killed = m.get('killed', 0)
                gen = m.get('generated', 0)
                if op in ('delete', 'short'):
                    # categorical mutants: every generated fault is a fault
                    if gen > 0 and killed >= gen:
                        st = 'validated'
                    elif gen > 0:
                        st = 'blind_spot'
                elif crossed > 0 and killed >= crossed:
                    st = 'validated'
                elif crossed > 0:
                    st = 'blind_spot'
            elif ev.startswith('oracle:'):
                if any(detected.get(t, 0) > 0 for t in p.kicad_types):
                    st = 'validated'
                else:
                    st = 'mapped'
            elif ev.startswith('probe:'):
                rule = ev.split(':', 1)[1]
                probe = (oracle_summary or {}).get('pair_probe', {}).get(rule) or {}
                if probe.get('ok', 0) > 0 and probe.get('MS') == 1.0:
                    st = 'validated'
                elif probe.get('ok', 0) > 0:
                    st = 'blind_spot'
                else:
                    st = 'mapped'
            if rid == 'kicad.via_drill' and mutation_summary.get('via_drill', {}).get('crossed', 0) == 0:
                st = 'mapped'  # no board declared an annulus/drill rule to cross
            out[rid] = st
        return out

    def report(self, mutation_summary=None, oracle_summary=None) -> dict:
        st = self.statuses(mutation_summary, oracle_summary)
        validated = [k for k, v in st.items() if v == 'validated']
        mapped = [k for k, v in st.items() if v == 'mapped']
        blind = [k for k, v in st.items() if v == 'blind_spot']
        return {'pairs': st, 'validated': len(validated),
                'mapped': len(mapped), 'blind_spot': len(blind),
                'validated_ids': sorted(validated),
                'mapped_ids': sorted(mapped),
                'blind_spot_ids': sorted(blind)}
