"""register — the declared rule register, with stable IDs.

`R_om` must be computed over an *identity list*, not a hardcoded count.
This module declares every rule the system knows about, records which
carrier in the rule model represents it, and computes the gap.

Seeded from three observed sources (no invented KiCad header):
  * `boards/rules/*.json` — real KiCad rule names and design-setting keys
  * KiCad DRC types observed in DRC reports / residue (clearance, width, ...)
  * `knowhow_constraints.json:jlc_limits` — the fab capability minima

An entry is *represented* only if the DSN/rule model can carry its value
AND a gate reads it; everything else is a GAP that must be reported, not
silently dropped.
"""
from __future__ import annotations

import glob
import hashlib
import json
import os
from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class RuleSpec:
    rule_id: str
    kicad_type: str
    source: str
    dsn_carrier: Optional[str] = None
    unit: str = 'mm'
    gate: str = 'drc_clean'
    severity: str = 'error'
    note: str = ''


# ---------------------------------------------------------------------------
# Declared inventory.  `dsn_carrier` is non-None only for the five families
# the Specctra rule model can actually carry.
# ---------------------------------------------------------------------------

_SEED = [
    # --- representable by the DSN/rule model (5 families) -----------------
    RuleSpec('kicad.clearance', 'clearance', 'kicad_pro:net_settings.classes',
             'class.rule.clearance'),
    RuleSpec('kicad.track_width', 'track_width', 'kicad_pro:net_settings.classes',
             'class.rule.width'),
    RuleSpec('kicad.default_rule', 'default_rule', 'kicad_pro:net_settings.classes',
             'pcb.rule'),
    RuleSpec('kicad.via_diameter', 'via_diameter', 'kicad_pro:net_settings.classes',
             'padstack.shape.circle'),
    RuleSpec('kicad.via_drill', 'via_drill', 'kicad_pro:net_settings.classes',
             'padstack.drill'),
    # --- design settings observed in boards/rules/*.json ------------------
    RuleSpec('kicad.min_clearance', 'clearance', 'kicad_pro:design_settings.rules'),
    RuleSpec('kicad.min_track_width', 'track_width', 'kicad_pro:design_settings.rules'),
    RuleSpec('kicad.min_hole_clearance', 'hole_clearance',
             'kicad_pro:design_settings.rules'),
    RuleSpec('kicad.min_hole_to_hole', 'hole_to_hole',
             'kicad_pro:design_settings.rules'),
    RuleSpec('kicad.min_copper_edge_clearance', 'copper_edge_clearance',
             'kicad_pro:design_settings.rules'),
    RuleSpec('kicad.min_via_annular_width', 'annular_width',
             'kicad_pro:design_settings.rules'),
    RuleSpec('kicad.min_via_diameter', 'via_diameter',
             'kicad_pro:design_settings.rules'),
    RuleSpec('kicad.min_through_hole_diameter', 'through_hole_diameter',
             'kicad_pro:design_settings.rules'),
    RuleSpec('kicad.min_microvia_diameter', 'microvia_diameter',
             'kicad_pro:design_settings.rules'),
    RuleSpec('kicad.min_microvia_drill', 'microvia_drill',
             'kicad_pro:design_settings.rules'),
    RuleSpec('kicad.min_resolved_spokes', 'starved_thermal',
             'kicad_pro:design_settings.rules'),
    RuleSpec('kicad.min_text_height', 'text_height',
             'kicad_pro:design_settings.rules'),
    RuleSpec('kicad.min_text_thickness', 'text_thickness',
             'kicad_pro:design_settings.rules'),
    # --- DRC types observed in reports / residue / operators --------------
    RuleSpec('kicad.padstack', 'padstack', 'kicad:drc'),
    RuleSpec('kicad.courtyard', 'courtyard', 'kicad:drc'),
    RuleSpec('kicad.silk_edge_clearance', 'silk_edge_clearance', 'kicad:drc'),
    RuleSpec('kicad.silk_over_copper', 'silk_over_copper', 'kicad:drc'),
    RuleSpec('kicad.silk_overlap', 'silk_overlap', 'kicad:drc'),
    RuleSpec('kicad.solder_mask_bridge', 'solder_mask_bridge', 'kicad:drc'),
    RuleSpec('kicad.footprint_type_mismatch', 'footprint_type_mismatch', 'kicad:drc'),
    RuleSpec('kicad.lib_footprint_mismatch', 'lib_footprint_mismatch', 'kicad:drc'),
    RuleSpec('kicad.track_dangling', 'track_dangling', 'kicad:drc'),
    RuleSpec('kicad.via_dangling', 'via_dangling', 'kicad:drc'),
    RuleSpec('kicad.shorting_items', 'shorting_items', 'kicad:drc'),
    RuleSpec('kicad.zones_intersect', 'zones_intersect', 'kicad:drc'),
    RuleSpec('kicad.starved_thermal', 'starved_thermal', 'kicad:drc'),
    RuleSpec('kicad.connection_width', 'connection_width', 'kicad:drc'),
    RuleSpec('kicad.isolated_copper', 'isolated_copper', 'kicad:drc'),
    # --- fab capability minima (JLC) --------------------------------------
    RuleSpec('jlc.clearance', 'clearance', 'knowhow:jlc_limits'),
    RuleSpec('jlc.track_width', 'track_width', 'knowhow:jlc_limits'),
    RuleSpec('jlc.via_drill', 'via_drill', 'knowhow:jlc_limits'),
    RuleSpec('jlc.via_diameter', 'via_diameter', 'knowhow:jlc_limits'),
]


@dataclass
class RuleRegister:
    specs: dict = field(default_factory=dict)

    @classmethod
    def default(cls, boards_rules_glob: str = None,
                knowhow_path: str = None) -> 'RuleRegister':
        reg = cls({s.rule_id: s for s in _SEED})
        # Add any observed rule name the seed missed (honest, not silent).
        if boards_rules_glob:
            for path in glob.glob(boards_rules_glob):
                try:
                    d = json.load(open(path, encoding='utf-8'))
                except Exception:
                    continue
                for name in (d.get('rule_severities') or {}):
                    rid = 'kicad.' + name
                    if rid not in reg.specs:
                        reg.specs[rid] = RuleSpec(rid, name, 'boards/rules')
                for name in (d.get('rules') or {}):
                    rid = 'kicad.' + name
                    if rid not in reg.specs:
                        reg.specs[rid] = RuleSpec(rid, name,
                                                  'kicad_pro:design_settings.rules')
        if knowhow_path and os.path.exists(knowhow_path):
            try:
                d = json.load(open(knowhow_path, encoding='utf-8'))
                for name, val in (d.get('jlc_limits') or {}).items():
                    # Only limit tables are rules; skip prose keys.
                    if not isinstance(val, dict):
                        continue
                    rid = 'jlc.' + name
                    if rid not in reg.specs:
                        reg.specs[rid] = RuleSpec(rid, name, 'knowhow:jlc_limits')
            except Exception:
                pass
        return reg

    def digest(self) -> str:
        payload = json.dumps({k: [v.kicad_type, v.source, v.dsn_carrier,
                                  v.gate, v.severity]
                              for k, v in sorted(self.specs.items())},
                             sort_keys=True, separators=(',', ':'))
        return hashlib.sha256(payload.encode()).hexdigest()

    def declared_ids(self) -> set:
        return set(self.specs)

    def represented_ids(self, model) -> set:
        """IDs whose DSN carrier is present in the effective rule model."""
        carriers = set()
        if model is not None:
            if getattr(model, 'classes', None):
                carriers.update({'class.rule.clearance', 'class.rule.width'})
            if getattr(model, 'default', None) is not None:
                if getattr(model.default, 'clearance_nm', 0) or \
                        getattr(model.default, 'width_nm', 0):
                    carriers.add('pcb.rule')
            for v in (getattr(model, 'via_padstacks', {}) or {}).values():
                if v:
                    carriers.add('padstack.shape.circle')
                    if v[1]:
                        carriers.add('padstack.drill')
        return {rid for rid, s in self.specs.items() if s.dsn_carrier in carriers}

    def gap_ids(self, model) -> set:
        return self.declared_ids() - self.represented_ids(model)

    def report(self, model) -> dict:
        declared = self.declared_ids()
        represented = self.represented_ids(model)
        gap = declared - represented
        return {
            'declared': len(declared),
            'represented': len(represented),
            'gap': len(gap),
            'R_om': round(1 - len(represented) / max(1, len(declared)), 3),
            'declared_ids': sorted(declared),
            'represented_ids': sorted(represented),
            'gap_ids': sorted(gap),
            'digest': self.digest(),
        }
