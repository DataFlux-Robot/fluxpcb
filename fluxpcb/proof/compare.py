"""Pareto comparison of certificates by their proven process margins.

Two layouts are not ranked by a weighted score (there is no weight to tune):
each certificate carries a margin vector, and one design dominates another
when every shared predicate's margin is at least as large and one is larger.
Anything else is incomparable, and that is a real answer.
"""
from __future__ import annotations

import json


def margins_of(cert):
    m = (cert.get('margins') or {}).get('margins') or {}
    return {k: v.get('margin_nm') for k, v in m.items()
            if v.get('margin_nm') is not None}


def compare(a, b):
    """Relation between two certificates' margin vectors."""
    ma, mb = margins_of(a), margins_of(b)
    common = sorted(set(ma) & set(mb))
    only_a = sorted(set(ma) - set(mb))
    only_b = sorted(set(mb) - set(ma))
    if not common:
        rel = 'incomparable'
    else:
        ge = all(ma[k] >= mb[k] for k in common)
        gt = any(ma[k] > mb[k] for k in common)
        le = all(ma[k] <= mb[k] for k in common)
        lt = any(ma[k] < mb[k] for k in common)
        if ge and gt:
            rel = 'a_dominates'
        elif le and lt:
            rel = 'b_dominates'
        elif not gt and not lt:
            rel = 'equal_on_common'
        else:
            rel = 'incomparable'
    return {'relation': rel, 'common': common, 'only_a': only_a,
            'only_b': only_b, 'a': ma, 'b': mb,
            'a_box': (a.get('margins') or {}).get('box'),
            'b_box': (b.get('margins') or {}).get('box')}


def compare_files(pa, pb):
    a = json.loads(open(pa, encoding='utf-8').read())
    b = json.loads(open(pb, encoding='utf-8').read())
    return compare(a, b)
