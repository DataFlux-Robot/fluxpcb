"""The fab capability profile — data with a date, a source and tiers.

A manufacturing check needs numbers, and numbers about a factory go
stale.  This module keeps them in a JSON file that says when it was
retrieved and where each number came from, and answers the two questions
a designer actually asks:

    tier_for('clearance', 0.08)   -> below the limit: not manufacturable
    drop_tier_advice({...})       -> which parameter holds the board in a
                                     price tier, and what to raise it to

Nothing here invents a value.  A parameter missing from the profile is
reported as missing (``known(param)`` is False) rather than defaulted.

    load(path=None) -> FabProfile
    active_profile(project_dir=None) -> FabProfile   (project override wins)
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

_HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_PATH = os.path.join(_HERE, 'data', 'jlc_2026-11-25.json')


@dataclass(frozen=True)
class FabProfile:
    profile_id: str
    vendor: str
    retrieved: str
    sources: tuple = ()
    assumption: str = ''
    parameters: dict = field(default_factory=dict)
    tiers: tuple = ()
    notes: tuple = ()
    decides_tier: tuple = ()
    path: str = ''

    # ------------------------------------------------------------- read
    @property
    def age_days(self) -> float:
        """How old the retrieval date is, in days (negative if unknown)."""
        import time
        try:
            then = time.mktime(time.strptime(self.retrieved, '%Y-%m-%d'))
            return (time.time() - then) / 86400.0
        except Exception:
            return -1.0

    def known(self, param: str) -> bool:
        return param in self.parameters

    def limits(self) -> dict:
        """The hard limits, in the names the DFM checks use."""
        out = {}
        for p, v in self.parameters.items():
            lim = v.get('limit_mm')
            if lim is not None:
                out[p] = float(lim)
        return out

    def dfm_limits(self) -> dict:
        """Limits under the keys ipc2221_dfm / inspector read."""
        lim = self.limits()
        alias = {
            'clearance': 'min_clearance',
            'track_width': 'min_track_width',
            'via_drill': 'min_drill',
            'via_diameter': 'min_via_diameter',
            'annular_ring': 'min_annular_ring',
            'hole_to_hole': 'min_hole_to_hole',
            'copper_edge': 'min_copper_edge',
        }
        out = {v: lim[k] for k, v in alias.items() if k in lim}
        if 'board_thickness_mm' in lim:
            out['min_board_thickness'] = lim['board_thickness_mm']
        panel = self.parameters.get('panel_mm') or {}
        for k in ('width_mm', 'height_mm', 'frame_mm', 'gap_mm'):
            if panel.get(k) is not None:
                out['panel_' + k] = float(panel[k])
        return out

    def tier_for(self, param: str, value_mm: float) -> dict:
        """Where one measured value sits: standard / advanced / below_limit."""
        p = self.parameters.get(param)
        if not p:
            return {'param': param, 'value_mm': value_mm, 'tier': 'unknown',
                    'known': False,
                    'why': f'{param} is not in profile {self.profile_id}; '
                           f'no value is assumed'}
        lim = float(p['limit_mm'])
        rec = float(p.get('recommended_mm', lim))
        below = value_mm < lim
        if below:
            tier = 'not_manufacturable'
        elif value_mm < rec:
            tier = 'advanced'
        else:
            tier = 'standard'
        return {'param': param, 'value_mm': value_mm, 'limit_mm': lim,
                'recommended_mm': rec, 'tier': tier, 'known': True,
                'below_limit': below, 'needs_raise_mm': round(max(0.0, rec - value_mm), 4),
                'source': (self.sources[0]['url'] if self.sources else ''),
                'retrieved': self.retrieved}

    def board_tier(self, values: dict) -> dict:
        """The board's tier, and the parameters that hold it there."""
        rows = [self.tier_for(p, float(v)) for p, v in values.items()
                if v is not None]
        known = [r for r in rows if r.get('known')]
        if not known:
            return {'tier': 'unknown', 'deciding': [],
                    'unknown_params': [r['param'] for r in rows]}
        order = {'standard': 0, 'advanced': 1, 'not_manufacturable': 2}
        worst = max(known, key=lambda r: order[r['tier']])
        deciding = [r for r in known if r['tier'] == worst['tier']]
        return {'tier': worst['tier'], 'deciding': deciding,
                'advanced': [r['param'] for r in known
                             if r['tier'] == 'advanced'],
                'below_limit': [r['param'] for r in known
                                if r['tier'] == 'not_manufacturable'],
                'unknown_params': [r['param'] for r in rows if not r.get('known')]}

    def drop_tier_advice(self, values: dict) -> list:
        """What to change to drop one price tier, in numbers."""
        t = self.board_tier(values)
        out = []
        for r in t.get('deciding') or []:
            if r['tier'] == 'advanced':
                out.append({'param': r['param'],
                            'action': f"raise {r['param']} from "
                                      f"{r['value_mm']} to {r['recommended_mm']} mm",
                            'measured_mm': r['value_mm'],
                            'target_mm': r['recommended_mm'],
                            'gain': 'advanced -> standard',
                            'source': r.get('source'), 'retrieved': r.get('retrieved')})
            elif r['tier'] == 'not_manufacturable':
                out.append({'param': r['param'],
                            'action': f"raise {r['param']} from "
                                      f"{r['value_mm']} to at least {r['limit_mm']} mm",
                            'measured_mm': r['value_mm'],
                            'target_mm': r['limit_mm'],
                            'gain': 'not manufacturable -> manufacturable',
                            'source': r.get('source'), 'retrieved': r.get('retrieved')})
        return out

    def stale(self, days: float = 180.0) -> bool:
        age = self.age_days
        return age < 0 or age > days

    def as_dict(self) -> dict:
        return {'profile_id': self.profile_id, 'vendor': self.vendor,
                'retrieved': self.retrieved, 'age_days': round(self.age_days, 1),
                'sources': list(self.sources), 'assumption': self.assumption,
                'parameters': self.parameters, 'tiers': list(self.tiers),
                'decides_tier': list(self.decides_tier),
                'path': self.path}


def load(path=None) -> FabProfile:
    p = Path(path or DEFAULT_PATH)
    with open(p, encoding='utf-8') as fh:
        d = json.load(fh)
    d.setdefault('profile_id', p.stem)
    d.setdefault('vendor', '?')
    d.setdefault('retrieved', '')
    d['sources'] = tuple(d.get('sources') or ())
    d['tiers'] = tuple(d.get('tiers') or ())
    d['notes'] = tuple(d.get('notes') or ())
    d['decides_tier'] = tuple(d.get('decides_tier') or ())
    d['path'] = str(p)
    fields = {k: d[k] for k in ('profile_id', 'vendor', 'retrieved', 'sources',
                                'assumption', 'parameters', 'tiers', 'notes',
                                'decides_tier', 'path') if k in d}
    return FabProfile(**fields)


@lru_cache(maxsize=8)
def _cached(path: str) -> FabProfile:
    return load(path)


def active_profile(project_dir=None) -> FabProfile:
    """The project's own fab_profile.json when it has one, else the default."""
    if project_dir is not None:
        for name in ('fab_profile.json', os.path.join('pcb', 'fab_profile.json')):
            p = Path(project_dir) / name
            if p.exists():
                return _cached(str(p))
    return _cached(DEFAULT_PATH)


def check_profile(profile: FabProfile = None) -> list:
    """Problems with a profile (used by tests and the manufacturing gate)."""
    prof = profile or active_profile()
    out = []
    if not prof.retrieved:
        out.append('profile has no retrieval date')
    elif prof.stale():
        out.append(f'profile {prof.retrieved} is {prof.age_days:.0f} days old')
    if not prof.sources:
        out.append('profile cites no source')
    for name in ('clearance', 'track_width', 'via_drill'):
        if not prof.known(name):
            out.append(f'profile lacks {name}')
    for p, v in prof.parameters.items():
        if 'limit_mm' in v and v.get('recommended_mm') is not None:
            if float(v['recommended_mm']) < float(v['limit_mm']):
                out.append(f'{p}: recommended below limit')
    return out
