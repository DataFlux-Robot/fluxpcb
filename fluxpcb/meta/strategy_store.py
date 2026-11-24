"""strategy_store — per-board arm selection (Component 1, channel A).

Running several arms on the same boards shows which arm suits which board.
A selector turns that into a rule a board can be matched against BEFORE
routing, from placement features only: a rule that looked at the routing
outcome would be an oracle, not a selector.

A rule is conservative on purpose.  It needs a minimum number of boards in
its cluster and must clean a minimum number of boards beyond the default
arm inside that cluster; its gain is in-sample and is labelled so.  Whether
the selector generalises is decided by the held-out confirmation, not here.
"""
from __future__ import annotations

import hashlib
import json
import os

MIN_SUPPORT = 4      # boards a cluster rule needs before it is proposed
MIN_GAIN = 2         # boards it must clean beyond the default arm


def cluster_key(features: dict) -> str:
    """Coarse, placement-only signature of a board."""
    if not features:
        return 'L?_unk'
    layers = features.get('copper_layers', '?')
    density = features.get('pads_per_mm2')
    if density is None:
        bin_ = 'unk'
    elif density < 0.1:
        bin_ = 'low'
    elif density < 0.5:
        bin_ = 'mid'
    else:
        bin_ = 'high'
    return f'L{layers}_{bin_}'


def _wall(p) -> float:
    return float(getattr(p, 'wall_seconds', 0) or 0)


def fit_selector(packets: list, min_support: int = MIN_SUPPORT,
                 min_gain: int = MIN_GAIN) -> dict:
    """Default arm plus cluster rules, fitted on the given packets."""
    boards = {}
    for p in packets:
        if p.status not in ('ok', 'budget') and p.outcome != 'clean':
            continue
        b = boards.setdefault(p.board, {'features': p.features or {},
                                        'arms': {}, 'clean': set()})
        b['arms'][p.arm] = p
        if p.outcome == 'clean':
            b['clean'].add(p.arm)

    def best(scope):
        tally = {}
        for name in boards if scope is None else scope:
            info = boards[name]
            for arm, p in info['arms'].items():
                t = tally.setdefault(arm, [0, 0.0, 0])
                t[0] += 1 if arm in info['clean'] else 0
                t[1] += _wall(p)
                t[2] += 1
        return max(tally, key=lambda a: (tally[a][0],
                                         -tally[a][1] / max(1, tally[a][2]), a)) \
            if tally else None

    default = best(None)
    clusters = {}
    for name, info in boards.items():
        clusters.setdefault(cluster_key(info['features']), []).append(name)
    rules = []
    for key in sorted(clusters):
        names = clusters[key]
        if len(names) < min_support:
            continue
        arm = best(names)
        d_clean = sum(1 for n in names if default in boards[n]['clean'])
        r_clean = sum(1 for n in names if arm in boards[n]['clean'])
        if arm != default and r_clean - d_clean >= min_gain:
            rules.append({'key': key, 'arm': arm, 'support': len(names),
                          'gain': r_clean - d_clean,
                          'clean_in_cluster': {'default': d_clean,
                                               'rule': r_clean},
                          'in_sample': True})
    return {'default': default, 'rules': rules,
            'fitted_on': len(boards),
            'in_sample': _in_sample(boards, default, rules)}


def _in_sample(boards, default, rules):
    rule_arm = {r['key']: r['arm'] for r in rules}
    d = s = o = 0
    for name, info in boards.items():
        clean = info['clean']
        d += 1 if default in clean else 0
        s += 1 if rule_arm.get(cluster_key(info['features']), default) in clean else 0
        o += 1 if clean else 0
    return {'default_clean': d, 'selector_clean': s, 'oracle': o,
            'boards': len(boards)}


def choose(selector: dict, features: dict) -> str:
    """The arm a board's placement features select."""
    if not selector or not selector.get('default'):
        return None
    key = cluster_key(features)
    for rule in selector.get('rules') or []:
        if rule['key'] == key:
            return rule['arm']
    return selector['default']


class StrategyStore:
    """Persists selectors so a later run can select without refitting."""

    def __init__(self, store_dir: str = '.strategy_store'):
        self.store_dir = store_dir
        if store_dir:
            os.makedirs(store_dir, exist_ok=True)

    @staticmethod
    def digest(selector: dict) -> str:
        body = json.dumps(selector or {}, sort_keys=True,
                          separators=(',', ':'), default=str)
        return hashlib.sha256(body.encode('utf-8')).hexdigest()[:12]

    def save(self, strategy: dict, board_features: dict = None) -> str:
        """Store a selector (or a plain strategy dict) and return its key."""
        if isinstance(strategy, dict) and 'default' in strategy:
            selector = strategy
        else:
            selector = {'default': None, 'rules': [], 'legacy': strategy,
                        'for': board_features}
        key = self.digest(selector)
        path = os.path.join(self.store_dir, f'selector_{key}.json')
        with open(path, 'w', encoding='utf-8') as fh:
            json.dump(dict(selector, _key=key), fh, indent=1,
                      ensure_ascii=False, default=str)
        return key

    def load(self) -> dict:
        """The most recently written selector."""
        if not self.store_dir or not os.path.isdir(self.store_dir):
            return {}
        files = sorted((os.path.join(self.store_dir, f)
                        for f in os.listdir(self.store_dir)
                        if f.startswith('selector_') and f.endswith('.json')),
                       key=os.path.getmtime)
        if not files:
            return {}
        try:
            with open(files[-1], encoding='utf-8') as fh:
                return json.load(fh)
        except (OSError, ValueError):
            return {}

    def load_for(self, board_features: dict) -> dict:
        """{'arm', 'rule', 'selector'} for a board, from the stored selector."""
        selector = self.load()
        if not selector:
            return None
        key = cluster_key(board_features)
        arm = choose(selector, board_features)
        rule = next((r['key'] for r in selector.get('rules') or []
                     if r['key'] == key), None)
        return {'arm': arm, 'rule': rule or 'default',
                'selector': selector.get('_key')}
