"""Refutation -> obligation, and a well-founded measure for the repair loop.

A denial is not a message to a human: it is a constructive delta.  Each
non-proven claim becomes an obligation (what failed, on which items, by how
much), and the pair

    (number of obligations, sorted negative margins)

is a lexicographic measure over integers: (obligations, missing pads, sorted
negative margins).  The pad key sees partial progress inside an open net —
GND losing eight of its eighteen islands is not a net closure, but it is
strictly less work than before — and it stops a candidate from robbing one
open net to pay another with the net-level count unchanged.

This module does not change the gate; it makes the gate's output usable by a
planner and gives the loop a termination argument to check.
"""
from __future__ import annotations

import re

_ID = re.compile(r'[0-9a-f]{8}-[0-9a-f-]{27}|[gh]\d+')


def _ids(text):
    return sorted(set(_ID.findall(text or '')))


def obligations(report):
    """Non-proven claims from a gate report, as constructive obligations."""
    out = []
    for c in (report.get('lean') or {}).get('claims', []):
        if c.get('status') in ('proven', 'carried', 'foreign'):
            continue
        out.append({'claim': c['id'], 'kernel': 'lean',
                    'status': c.get('status'), 'witness': c.get('witness', ''),
                    'targets': _ids(c.get('witness')), 'delta_nm': None})
    for c in (report.get('fab') or {}).get('claims', []):
        if c.get('status') == 'proven':
            continue
        out.append({'claim': c['id'], 'kernel': 'fab',
                    'status': c.get('status'), 'witness': c.get('witness', ''),
                    'targets': _ids(c.get('witness')), 'delta_nm': None})
    for c in (report.get('domain') or {}).get('claims', []):
        if c.get('status') in ('derived', 'disabled'):
            continue
        targets = []
        ev_in = (c.get('evidence') or {}).get('inputs') or {}
        for k in ('net', 'p_net', 'n_net'):
            v = c.get(k) or ev_in.get(k)
            if v and v not in targets:
                targets.append(str(v))
        delta_nm = None
        pred = c.get('pred', '')
        if pred in ('signal.skew', 'signal.coupled') and c.get('measured') is not None:
            skew = c.get('skew_mm') if pred == 'signal.coupled' else c.get('measured')
            lim = c.get('limit') or ev_in.get('max_skew_mm')
            if skew is not None and lim is not None and skew > lim:
                delta_nm = int(round((skew - lim) * 1e6))
        elif pred in ('power.drop', 'power.drop_path', 'power.j') and c.get('measured') is not None:
            lim = c.get('limit')
            if lim is not None and c['measured'] > lim:
                delta_nm = int(round((c['measured'] - lim) * 1000))
        out.append({'claim': c['id'], 'kernel': 'domain', 'pred': pred,
                    'status': c.get('status'), 'witness': c.get('witness', ''),
                    'targets': targets, 'delta_nm': delta_nm})
    return out


def margin_obligations(report):
    """Negative margins from process.margins() as obligations with a delta."""
    out = []
    for pred, m in ((report or {}).get('margins') or {}).items():
        if m.get('margin_nm', 0) < 0:
            out.append({'claim': f'MARGIN:{pred}', 'kernel': 'process',
                        'status': 'refuted', 'witness': m.get('witness', ''),
                        'targets': _ids(m.get('witness')), 'delta_nm':
                            -int(m['margin_nm'])})
    return out


def measure(obligations, missing_pads=None):
    """Lexicographic (obligations, missing pads, sorted negative deltas).

    `missing_pads` is optional so the gate/step callers that only have the
    obligation list keep working; with it, the second key is the pad-level
    progress of the same state."""
    deltas = sorted((o.get('delta_nm') or 0) for o in obligations
                    if o.get('delta_nm') is not None)
    pads = 0 if missing_pads is None else int(missing_pads)
    return (len(obligations), pads, tuple(deltas))


def terminates(before, after, pads_before=None, pads_after=None):
    """A step is progress when the measure strictly decreases."""
    return measure(after, pads_after) < measure(before, pads_before)


def summary(obligations):
    from collections import Counter
    return {'n': len(obligations),
            'by_kernel': dict(Counter(o['kernel'] for o in obligations)),
            'targets': sorted({t for o in obligations for t in o['targets']})}
