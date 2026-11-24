"""Via geometry that the board's own DSN can carry.

A via an operator draws has to satisfy two masters.  DRC wants at least the
project's minimum via diameter; the router wants a padstack the exported
DSN actually defines, and it names padstacks after the netclass via
("Via[0-1]_600:400_um").  A via with any other size is not in the DSN
library, and the next Freerouting pass on that board dies with
"via padstack ... not found" before routing anything.

So: read the padstacks the DSN defines and pick the smallest one that also
clears the DRC minima.  Only when no padstack does (a project whose minimum
is larger than its own netclass via) fall back to a DRC-first size, and say
so in `source`.
"""
from __future__ import annotations

import os
import re

_PADSTACK = re.compile(r'\(padstack\s+"(Via\[[^\]]*\]_(\d+):(\d+)_um)"')


def dsn_via_padstacks(dsn_path) -> list:
    """[(diameter_mm, drill_mm, name)] defined by a DSN file."""
    if not dsn_path or not os.path.exists(dsn_path):
        return []
    try:
        text = open(dsn_path, encoding='utf-8', errors='replace').read()
    except OSError:
        return []
    out = []
    for name, dia, drill in _PADSTACK.findall(text):
        item = (int(dia) / 1000.0, int(drill) / 1000.0, name)
        if item not in out:
            out.append(item)
    return out


def via_geometry(board, rules: dict) -> tuple:
    """(diameter_mm, drill_mm, source) for a via on this board."""
    want_dia = float(rules.get('via_dia') or rules.get('via_diameter') or 0.0)
    want_drill = float(rules.get('via_drill') or 0.0)
    min_dia = min_drill = 0.0
    try:
        ds = board.GetDesignSettings()
        min_dia = ds.m_ViasMinSize / 1e6
        min_drill = ds.m_MinThroughDrill / 1e6
    except Exception:
        pass
    need_dia = max(want_dia, min_dia, 0.0)
    need_drill = max(want_drill, min_drill, 0.0)

    dsn = os.path.join(os.path.dirname(board.GetFileName()), 'board.dsn')
    cands = dsn_via_padstacks(dsn)
    ok = [c for c in cands if c[0] >= need_dia and c[1] >= need_drill]
    if ok:
        dia, drill, name = min(ok, key=lambda c: (c[0], c[1]))
        return dia, drill, 'dsn:' + name
    # DRC-first fallback: the project's minimum exceeds every padstack it
    # defines, so a DSN via would fail DRC.  The board cannot then be
    # re-routed by Freerouting until the DSN is patched; the caller's
    # reroute stage will report that as an action error.
    dia = need_dia or 0.8
    drill = need_drill or 0.4
    if cands and not need_dia:
        dia, drill, _ = max(cands, key=lambda c: (c[0], c[1]))
    drill = min(drill, dia - 0.1)
    return dia, drill, 'drc-first'
