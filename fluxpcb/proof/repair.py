"""Refutation -> obligation, and a well-founded measure for the repair loop.

A denial is not a message to a human: it is a constructive delta.  Each
non-proven claim becomes an obligation (what failed, on which items, by how
much), and the pair

    (number of obligations, sorted negative margins)

is a lexicographic measure over integers.  A committed step that lowers it
is progress; a loop that terminates only on an empty obligation set is a
descent over a well-founded order, not a hope.

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
        out.append({'claim': c['id'], 'kernel': 'domain',
                    'status': c.get('status'), 'witness': c.get('witness', ''),
                    'targets': [], 'delta_nm': None})
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


def measure(obligations):
    """Lexicographic (count, sorted negative deltas) over integers."""
    deltas = sorted((o.get('delta_nm') or 0) for o in obligations
                    if o.get('delta_nm') is not None)
    return (len(obligations), tuple(deltas))


def terminates(before, after):
    """A step is progress when the measure strictly decreases."""
    return measure(after) < measure(before)


def summary(obligations):
    from collections import Counter
    return {'n': len(obligations),
            'by_kernel': dict(Counter(o['kernel'] for o in obligations)),
            'targets': sorted({t for o in obligations for t in o['targets']})}
