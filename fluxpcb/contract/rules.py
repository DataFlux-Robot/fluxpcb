"""rules — the PCB rule model as a finite table, and its congruence diff.

A rule model is, for the constraints a Specctra DSN can carry:

    net              -> (track width nm, clearance nm)
    <default / none> -> (track width nm, clearance nm)
    via padstack     -> (copper diameter nm, drill nm)

That domain is finite.  Therefore the congruence of two models is decidable
by exhaustion, and the diff below *is* a proof for L1; the only remaining
question is the quantization policy (L3), which ``quant`` handles.

Commercial flows keep congruence by sharing an executable (KiCad's PNS calls
``DRC_ENGINE::EvalRules``).  A translation flow like ours cannot share code,
so it must share the *table* and prove it.  This module is that proof.
"""
from __future__ import annotations

import fnmatch
import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Optional

from .quant import NM_PER_UM, mm_to_nm, parse_um
from .sexpr import (direct_children, find_node, match_pairs, node_name,
                    tokenize, unquote)

# One nanometre: project values are exact at KiCad's internal resolution.
PROJECT_RES_NM = 1.0


def _is_via_padstack(name: str) -> bool:
    n = unquote(name)
    return bool(re.match(r'(?i)^via(?:\[|_|$)', n))


@dataclass(frozen=True)
class ClassRule:
    name: str
    nets: tuple = ()
    width_nm: float = 0.0
    clearance_nm: float = 0.0
    via_d_nm: Optional[float] = None
    via_drill_nm: Optional[float] = None


@dataclass
class RuleModel:
    """Effective rules, in nanometres, from one source."""
    source: str
    default: ClassRule
    classes: dict = field(default_factory=dict)
    via_padstacks: dict = field(default_factory=dict)  # name -> (d_nm, drill_nm)
    resolution_nm: float = PROJECT_RES_NM
    out_of_model: list = field(default_factory=list)

    # ---- effective per-net table -------------------------------------

    def net_rules(self) -> dict:
        table = {}
        for cls in self.classes.values():
            for net in cls.nets:
                table[net] = (cls.width_nm, cls.clearance_nm)
        return table

    def all_nets(self) -> set:
        return set(self.net_rules())

    def via_extremes(self):
        ds = [v[0] for v in self.via_padstacks.values() if v and v[0]]
        drs = [v[1] for v in self.via_padstacks.values() if v and v[1]]
        ds += [c.via_d_nm for c in self.classes.values() if c.via_d_nm]
        drs += [c.via_drill_nm for c in self.classes.values() if c.via_drill_nm]
        return (max(ds) if ds else None, max(drs) if drs else None)

    def digest(self) -> str:
        payload = {
            'source': self.source,
            'default': [self.default.width_nm, self.default.clearance_nm],
            'nets': {n: [round(v[0], 3), round(v[1], 3)]
                     for n, v in sorted(self.net_rules().items())},
            'vias': {k: [round(v[0], 3), round(v[1], 3)]
                     for k, v in sorted(self.via_padstacks.items())},
            'class_names': sorted(self.classes),
        }
        blob = json.dumps(payload, sort_keys=True, separators=(',', ':'))
        return hashlib.sha256(blob.encode()).hexdigest()

    # ---- extraction from a .kicad_pro --------------------------------

    @classmethod
    def from_project(cls, pro: dict, net_names=None,
                     source: str = 'kicad_pro') -> 'RuleModel':
        """Build the effective table from a project's net settings.

        ``net_names`` (from the DSN or pcbnew) lets ``netclass_patterns``
        be resolved to concrete nets.  Without it, patterns are recorded as
        out-of-model rather than silently ignored — a skip that says which
        key, never a default.
        """
        ns = (pro or {}).get('net_settings', {}) or {}
        raw = ns.get('classes', []) or []
        classes = {}
        for c in raw:
            name = c.get('name') or 'Default'
            classes[name] = ClassRule(
                name=name,
                nets=tuple(n for n in (c.get('nets') or []) if n),
                width_nm=mm_to_nm(c.get('track_width') if c.get('track_width')
                                  is not None else 0.25),
                clearance_nm=mm_to_nm(c.get('clearance') if c.get('clearance')
                                      is not None else 0.2),
                via_d_nm=(mm_to_nm(c['via_diameter'])
                          if c.get('via_diameter') is not None else None),
                via_drill_nm=(mm_to_nm(c['via_drill'])
                              if c.get('via_drill') is not None else None),
            )

        default_name = 'Default' if 'Default' in classes else (
            next(iter(classes)) if classes else 'Default')
        if default_name not in classes:
            classes[default_name] = ClassRule(name=default_name,
                                              width_nm=mm_to_nm(0.25),
                                              clearance_nm=mm_to_nm(0.2))

        out = []
        # explicit assignments: {net: class} (dict) or list of dicts
        assignments = ns.get('netclass_assignments') or {}
        if isinstance(assignments, dict):
            assign_items = assignments.items()
        else:
            assign_items = ((a.get('net'), a.get('netclass') or a.get('class'))
                            for a in assignments if isinstance(a, dict))
        for net, cls_name in assign_items:
            if net and cls_name in classes and net not in classes[cls_name].nets:
                c = classes[cls_name]
                classes[cls_name] = ClassRule(
                    c.name, tuple(c.nets) + (net,), c.width_nm, c.clearance_nm,
                    c.via_d_nm, c.via_drill_nm)

        # netclass_patterns resolved against known nets
        patterns = ns.get('netclass_patterns') or []
        if patterns:
            if net_names is None:
                out.append('netclass_patterns unresolved (no net list supplied)')
            else:
                names = list(net_names)
                for p in patterns:
                    pat = p.get('pattern')
                    cname = p.get('netclass')
                    if not pat or cname not in classes:
                        continue
                    for net in names:
                        if fnmatch.fnmatchcase(net, pat) or net == pat:
                            c = classes[cname]
                            if net not in c.nets:
                                classes[cname] = ClassRule(
                                    c.name, tuple(c.nets) + (net,), c.width_nm,
                                    c.clearance_nm, c.via_d_nm, c.via_drill_nm)

        # Nets the DSN knows but no class claims fall to the default class.
        if net_names is not None:
            claimed = {n for c in classes.values() for n in c.nets}
            leftovers = [n for n in net_names if n not in claimed]
            if leftovers:
                c = classes[default_name]
                classes[default_name] = ClassRule(
                    c.name, tuple(c.nets) + tuple(leftovers), c.width_nm,
                    c.clearance_nm, c.via_d_nm, c.via_drill_nm)

        return cls(source=source, default=classes[default_name],
                   classes=classes, out_of_model=out)

    # ---- extraction from a Specctra DSN ------------------------------

    @classmethod
    def from_dsn(cls, text: str, source: str = 'dsn') -> 'RuleModel':
        toks = tokenize(text)
        match = match_pairs(toks)
        pcb = find_node(toks, 'pcb', match=match)
        if pcb is None:
            raise ValueError('not a DSN: no (pcb ...) node')

        res_nm = PROJECT_RES_NM
        rn = find_node(toks, 'resolution', root=pcb, match=match)
        if rn is not None:
            vals = [t[1] for t in toks[rn + 1:match[rn]] if t[0] in ('atom', 'string')]
            if len(vals) >= 2:
                unit, value = vals[0], vals[1]
                try:
                    per_unit = {'um': NM_PER_UM, 'mm': 1_000_000.0,
                                'mil': 25400.0, 'inch': 25_400_000.0}.get(unit, NM_PER_UM)
                    res_nm = per_unit / float(value)
                except (ValueError, ZeroDivisionError):
                    res_nm = PROJECT_RES_NM

        # pcb-level default rule (net-less objects: pads with no net etc.)
        default = ClassRule(name='<default>')
        rule = find_node(toks, 'rule', root=pcb, match=match)
        if rule is not None:
            w, c, vd, vdr = cls._rule_values(toks, match, rule)
            default = ClassRule(name='<default>', width_nm=w or 0.0,
                                clearance_nm=c or 0.0,
                                via_d_nm=vd, via_drill_nm=vdr)

        classes = {}
        network = find_node(toks, 'network', root=pcb, match=match)
        if network is not None:
            for ci in direct_children(toks, match, network):
                if node_name(toks, ci) != 'class':
                    continue
                cr = cls._parse_class(toks, match, ci)
                if cr is not None:
                    classes[cr.name] = cr

        vias = {}
        for i, t in enumerate(toks):
            if t[0] != 'open' or node_name(toks, i) != 'padstack':
                continue
            name = toks[i + 2][1] if i + 2 < len(toks) else ''
            if not _is_via_padstack(name):
                continue
            d = dr = None
            shape = find_node(toks, 'shape', root=i, match=match)
            if shape is not None:
                for ch in direct_children(toks, match, shape):
                    if node_name(toks, ch) != 'circle':
                        continue
                    nums = [x[1] for x in toks[ch + 1:match[ch]]
                            if x[0] == 'atom']
                    if len(nums) >= 2:
                        d = parse_um(nums[-1])
                        break
            drill = find_node(toks, 'drill', root=i, match=match)
            if drill is not None:
                nums = [x[1] for x in toks[drill + 2:match[drill]]
                        if x[0] == 'atom']
                if nums:
                    dr = parse_um(nums[0])
            if dr is None:
                # KiCad encodes the via drill ONLY in the padstack name;
                # the copper diameter lives in (shape (circle ...)).
                m = re.search(r'_(\d+(?:\.\d+)?):(\d+(?:\.\d+)?)_um$',
                              unquote(name))
                if m:
                    dr = parse_um(m.group(2))
            if d is not None or dr is not None:
                vias[unquote(name)] = (d or 0.0, dr or 0.0)

        return cls(source=source, default=default, classes=classes,
                   via_padstacks=vias, resolution_nm=res_nm)

    @classmethod
    def _parse_class(cls, toks, match, ci) -> Optional[ClassRule]:
        name = unquote(toks[ci + 2][1]) if ci + 2 < len(toks) else None
        if not name:
            return None
        nets = []
        k = ci + 3
        end = match[ci]
        while k < end:
            t = toks[k]
            if t[0] == 'open':
                k = match[k] + 1
                continue
            val = unquote(t[1])
            if val == 'BASE':
                # next atom is the base net; it is a class member
                if k + 1 < end and toks[k + 1][0] in ('atom', 'string'):
                    nets.append(unquote(toks[k + 1][1]))
                    k += 2
                    continue
                k += 1
                continue
            nets.append(val)
            k += 1
        body = cls._class_rule_from_node(toks, match, ci, name=name,
                                         nets=tuple(nets))
        return body

    @classmethod
    def _rule_values(cls, toks, match, rule):
        width = clearance = None
        via_d = via_drill = None
        for ch in direct_children(toks, match, rule):
            nm = node_name(toks, ch)
            nums = [x[1] for x in toks[ch + 2:match[ch]] if x[0] == 'atom']
            if nm == 'width' and nums:
                width = parse_um(nums[0])
            elif nm == 'clearance' and nums:
                clearance = parse_um(nums[0])
            elif nm == 'via' and len(nums) >= 2:
                via_d, via_drill = parse_um(nums[0]), parse_um(nums[1])
        return width, clearance, via_d, via_drill

    @classmethod
    def _class_rule_from_node(cls, toks, match, node, name, nets) -> ClassRule:
        width = clearance = via_d = via_drill = None
        rule = find_node(toks, 'rule', root=node, match=match)
        if rule is not None:
            width, clearance, via_d, via_drill = cls._rule_values(
                toks, match, rule)
        return ClassRule(name=name, nets=tuple(nets),
                         width_nm=width or 0.0, clearance_nm=clearance or 0.0,
                         via_d_nm=via_d, via_drill_nm=via_drill)

    # ---- congruence ---------------------------------------------------

    def diff(self, other: 'RuleModel') -> 'RuleDiff':
        tol = max(self.resolution_nm, other.resolution_nm, 0.0) + 0.5
        a, b = self.net_rules(), other.net_rules()
        mism = []
        for net in sorted(set(a) | set(b)):
            if net not in a:
                mism.append({'kind': 'net_missing_project', 'net': net,
                             'dsn': list(b[net])})
            elif net not in b:
                mism.append({'kind': 'net_missing_dsn', 'net': net,
                             'project': list(a[net])})
            else:
                dw = abs(a[net][0] - b[net][0])
                dc = abs(a[net][1] - b[net][1])
                if dw > tol or dc > tol:
                    mism.append({'kind': 'value', 'net': net,
                                 'project': list(a[net]), 'dsn': list(b[net]),
                                 'delta_nm': [round(dw, 3), round(dc, 3)]})
        # default (net-less) rule
        dw = abs(self.default.width_nm - other.default.width_nm)
        dc = abs(self.default.clearance_nm - other.default.clearance_nm)
        default_ok = dw <= tol and dc <= tol and (
            self.default.clearance_nm > 0 and other.default.clearance_nm > 0)
        if not default_ok:
            mism.append({'kind': 'default_rule',
                         'project': [self.default.width_nm, self.default.clearance_nm],
                         'dsn': [other.default.width_nm, other.default.clearance_nm]})
        # via geometry
        va, vb = self.via_extremes(), other.via_extremes()
        via_ok = True
        for pa, pb, label in ((va[0], vb[0], 'via_diameter'),
                              (va[1], vb[1], 'via_drill')):
            if pa is not None and pb is not None and abs(pa - pb) > tol:
                via_ok = False
                mism.append({'kind': label, 'project': pa, 'dsn': pb})
        return RuleDiff(ok=not mism, tolerance_nm=tol, mismatches=mism,
                        project_nets=len(a), dsn_nets=len(b),
                        out_of_model=list(self.out_of_model))


@dataclass
class RuleDiff:
    ok: bool
    tolerance_nm: float
    mismatches: list
    project_nets: int
    dsn_nets: int
    out_of_model: list = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            'ok': self.ok,
            'tolerance_nm': self.tolerance_nm,
            'project_nets': self.project_nets,
            'dsn_nets': self.dsn_nets,
            'mismatch_count': len(self.mismatches),
            'mismatches': self.mismatches[:50],
            'out_of_model': self.out_of_model,
        }

    def summary(self, limit: int = 4) -> str:
        if self.ok:
            return f'rule tables congruent ({self.project_nets} nets)'
        kinds = {}
        for m in self.mismatches:
            kinds[m['kind']] = kinds.get(m['kind'], 0) + 1
        body = ', '.join(f'{k}={v}' for k, v in sorted(kinds.items()))
        sample = '; '.join(
            f"{m.get('net', '')}:{m['kind']}" for m in self.mismatches[:limit])
        return f'rule mismatch ({body}): {sample}'
