"""Error models — induced failure classes with counterfactuals.

The observation side of the loop: failure certificates (verifiers.certs)
are binned on their NUMERICAL conditions into error models.  Each model
says, in numbers, what must be true for a board of this kind to pass —
the counterfactual that decides which tool or knowledge to seek.  The
abstraction step (an LLM naming a model) is optional and must cite
example ids; the bins never come from an LLM.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field

from ..verifiers.certs import is_failure, outcome_ok

# ---------------------------------------------------------------- bins
SHORTFALL_BANDS = ((0.02, 'lt20um'), (0.05, '20-50um'), (0.1, '50-100um'),
                   (0.2, '100-200um'), (None, 'gt200um'))


def shortfall_band(mm):
    if mm is None:
        return 'unknown'
    for limit, name in SHORTFALL_BANDS:
        if limit is None or mm < limit:
            return name
    return 'gt200um'


def gap_class(why):
    """Free-space class of a failed attempt's geometry."""
    sealed = bool(why.get('sealed'))
    margin = why.get('margin_mm')
    if sealed:
        return 'sealed'
    if margin is None:
        return 'unknown'
    if margin < 0:
        return 'no_gap'
    return 'tight' if margin < 0.1 else 'open'


def blocker_kind(why):
    return (why.get('blocker') or {}).get('kind') or 'unknown'


def budget_class(why):
    v = why.get('budget') or {}
    return 'budget' if v.get('exhausted') else 'fine'


def density_bin(features):
    d = (features or {}).get('pads_per_mm2')
    if d is None:
        return 'unk'
    return 'low' if d < 0.1 else ('mid' if d < 0.5 else 'high')


def conditions_of(entry: dict) -> tuple:
    """The exact condition tuple an attempt is binned on.

    (outcome, violation class, shortfall band, gap class, blocker kind,
     budget class, layers, density bin).  The outcome is part of the bin:
    a no_path attempt and a verify_rejected attempt are different facts.
    """
    why = entry.get('why_not') or {}
    att = entry.get('attempt') or {}
    feats = entry.get('features') or {}
    cls = att.get('class') or 'untyped'
    return (entry.get('outcome'), cls,
            shortfall_band(why.get('short_by_mm') if
                           why.get('short_by_mm') is not None
                           else (why.get('margin_mm') if
                                 (why.get('margin_mm') is not None
                                  and why['margin_mm'] < 0) else None)),
            gap_class(why), blocker_kind(why), budget_class(why),
            str(feats.get('copper_layers') or '?'),
            density_bin(feats))


# ---------------------------------------------------------------- model
@dataclass
class ErrorModel:
    conditions: tuple
    name: str = ''
    counterfactual: str = ''
    capability: dict = field(default_factory=dict)   # tool -> {trials, wins}
    examples: list = field(default_factory=list)     # cert ids + boards
    support: int = 0
    needs_new_capability: bool = False
    status: str = 'open'                              # open | retired
    first_seen: str = ''
    last_seen: str = ''

    def key(self) -> str:
        return json.dumps(self.conditions, separators=(',', ':'))

    def model_id(self) -> str:
        body = json.dumps(self.conditions, separators=(',', ':'),
                          default=str)
        return hashlib.sha256(body.encode('utf-8')).hexdigest()[:12]

    def as_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> 'ErrorModel':
        return cls(**{k: d.get(k) for k in (
            'conditions', 'name', 'counterfactual', 'capability',
            'examples', 'support', 'needs_new_capability', 'status',
            'first_seen', 'last_seen')})

    def zero_reach(self, trials: int = 2) -> bool:
        """No tool has a confirmed win and at least one has been tried."""
        tried = sum(c.get('trials', 0) for c in self.capability.values())
        wins = sum(c.get('wins', 0) for c in self.capability.values())
        return tried >= trials and wins == 0

    def record(self, op, outcome, at):
        c = self.capability.setdefault(op, {'trials': 0, 'wins': 0})
        c['trials'] += 1
        c['wins'] += 1 if outcome == 'ok' else 0
        self.last_seen = at
        self.first_seen = self.first_seen or at


def counterfactual_of(conditions: tuple, sample_why: dict) -> str:
    """Deterministic counterfactual from the bin, not from an LLM."""
    outcome, cls, band, gap, blocker, budget, layers, density = conditions
    parts = []
    w = sample_why or {}
    if gap == 'sealed':
        r = w.get('via_sites') or {}
        nearest = min((l.get('nearest_mm') for l in r.get('layers', [])
                       if l.get('nearest_mm') is not None), default=None)
        parts.append('anchor sealed: no legal via in the searched rings'
                     + (f'; nearest legal site {nearest} mm away' if nearest
                        is not None else '; needs a different layer or the '
                        'blocking net must move'))
    if band and band != 'unknown':
        by = w.get('short_by_mm') if w.get('short_by_mm') is not None \
            else -(w.get('margin_mm') or 0)
        if by is not None and by > 0:
            parts.append(f'corridor short by {band} ({by:.4f} mm): the '
                         f'blocking {blocker} must move >= {by:.4f} mm or '
                         'the path needs a narrower track')
    wmax = w.get('w_max_mm')
    if wmax is not None and wmax > 0:
        parts.append(f'a track <= {wmax:.3f} mm would fit')
    if budget == 'budget':
        parts.append('search hit its budget: geometry may still allow a path')
    if not parts:
        parts.append(f'unknown: {cls} with no measured geometry')
    return '; '.join(parts)


def induce(certs: list, existing: dict = None, at: str = '',
           max_examples: int = 6) -> list:
    """Cluster failure certificates into error models.

    `existing` is {model_id: ErrorModel}; models accumulate across calls,
    so the same conditions keep their capability history.
    """
    models = {k: v for k, v in (existing or {}).items()}
    fresh = set()
    for entry in certs:
        if outcome_ok(entry):
            # A success has no failure conditions to bin on: it must say
            # which model it addressed (the tool-evolution stage knows).
            mid = (entry.get('attempt') or {}).get('model_id')
            if mid and mid in models:
                m = models[mid]
                m.record(entry.get('op', '?'), 'ok', at)
                m.needs_new_capability = m.zero_reach()
            continue
        if not is_failure(entry):
            continue
        cond = conditions_of(entry)
        mid = json.dumps(cond, separators=(',', ':'))
        mid = hashlib.sha256(mid.encode('utf-8')).hexdigest()[:12]
        m = models.get(mid) or ErrorModel(conditions=cond)
        m.record(entry.get('op', '?'), entry.get('outcome'), at)
        m.support += 1
        m.counterfactual = m.counterfactual or counterfactual_of(
            cond, entry.get('why_not'))
        ex = {'cert_id': entry.get('cert_id'), 'board': entry.get('board')}
        if ex not in m.examples:
            m.examples.append(ex)
        m.examples = m.examples[:max_examples]
        m.needs_new_capability = m.zero_reach()
        models[mid] = m
        fresh.add(mid)
    out = [models[k] for k in sorted(models, key=lambda k: -models[k].support)]
    for m in out:
        m.needs_new_capability = m.zero_reach()
    return out


def to_memory_entries(models: list, at: str) -> list:
    return [dict(model.as_dict(), at=at) for model in models]


def from_memory_entries(entries: list) -> dict:
    """{model_id: ErrorModel} from memory 'error_model' entries (latest wins)."""
    out = {}
    for e in entries:
        m = ErrorModel.from_dict(e)
        if m.model_id() not in out:
            out[m.model_id()] = m
    return out


def capability_matrix(models: list) -> dict:
    """model_id -> {tool: {trials, wins}} for the tool evolution stage."""
    return {m.model_id(): m.capability for m in models}


def gradient_check(records: list) -> dict:
    """Did the predicted direction of a change come true?

    records: [{'model_id', 'field', 'prediction': 'up'|'down',
               'before': float, 'after': float}, ...]
    Returns per-field hit rate; a mapping with a hit rate no better than
    chance gets retired, not tuned.
    """
    per = {}
    for r in records:
        key = (r.get('model_id'), r.get('field'))
        hit = ((r.get('after', 0) > r.get('before', 0)) ==
               (r.get('prediction') == 'up'))
        p = per.setdefault(key, {'hits': 0, 'trials': 0})
        p['trials'] += 1
        p['hits'] += 1 if hit else 0
    return {f'{k[0]}:{k[1]}': {'hits': v['hits'], 'trials': v['trials'],
                               'rate': round(v['hits'] / v['trials'], 2),
                               'retire': (v['trials'] >= 4
                                          and v['hits'] <= v['trials'] / 2)}
            for k, v in per.items()}
