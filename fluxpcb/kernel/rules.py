# -*- coding: utf-8 -*-
"""Board State Kernel — rules.py: the ONE rule table (T2).

The 'pieced-together DRC model' defect class (three verifier
misalignments fixed one-by-one: 0.17->0.15, uniform->smd_smd, via
barrel missing). Manual mirroring of rule constants keeps leaking.
Corpus cures, all absorbed here:
- kipilot-mcp:  parse the rules file and REPORT parse status — never
                guess silently
- ByTaymur/PcbAi: LOAD PROBE — KiCad silently drops a corrupt rules
                file and DRC returns 0; proof-of-loading beats trust
- DaCapo7:      write-then-readback (a rule that reports success but
                does not persist is a rule that does not exist)
- stackup-eda:  one source of truth + a drift check against consumers

Every geometric check (geomir verifier, commit_gate, routegrid margins)
consumes load_rules() — no hand-copied constants anywhere.
"""
from __future__ import annotations

import json
from pathlib import Path

# explicit fallbacks (never silent: status says where each value came from)
_FALLBACK = {
    "clearance": 0.15,
    "clearance_smd": 0.0375,
    "track_width": 0.2,
    "via_dia": 0.5,
    "via_drill": 0.25,
}


def load_rules(pcb_path) -> dict:
    """One merged rule table with provenance.

    Sources, in order: .kicad_pro (board.design_settings +
    net_settings.classes Default), .kicad_dru (custom constraint
    overrides), then explicit fallbacks. The returned dict always
    carries 'parse' = {source per key, errors[]} — the consumer can
    assert the rules it needs really exist (the load-probe contract).
    """
    out = dict(_FALLBACK)
    parse = {"source": {k: "fallback" for k in _FALLBACK}, "errors": []}
    pro = pcb_path.with_suffix(".kicad_pro")
    try:
        data = json.loads(pro.read_text(encoding="utf-8"))
        board = data.get("board", {})
        ds = board.get("design_settings", {}) or {}
        rules = ds.get("rules", {}) or {}
        v = rules.get("min_clearance")
        if isinstance(v, (int, float)):
            out["clearance"] = float(v)
            parse["source"]["clearance"] = "kicad_pro:min_clearance"
        # Default netclass carries track/via geometry
        ns = (data.get("net_settings", {}) or {}).get("classes", []) or []
        for cls in ns:
            if cls.get("name") == "Default":
                for key, field in (("clearance", "clearance"),
                                   ("track_width", "track_width"),
                                   ("via_dia", "via_diameter"),
                                   ("via_drill", "via_drill")):
                    v = cls.get(field)
                    if isinstance(v, (int, float)):
                        out[key] = float(v)
                        parse["source"][key] = f"kicad_pro:Default.{field}"
                v = cls.get("smd_to_smd_clearance")
                if isinstance(v, (int, float)):
                    out["clearance_smd"] = float(v)
                    parse["source"]["clearance_smd"] = \
                        "kicad_pro:Default.smd_to_smd_clearance"
                break
    except (OSError, ValueError) as e:
        parse["errors"].append(f"kicad_pro: {e}")
    dru = pcb_path.with_suffix(".kicad_dru")
    if dru.exists():
        try:
            import re
            txt = dru.read_text(encoding="utf-8", errors="replace")
            # (rule X (constraint clearance (min 0.2mm)))
            for m in re.finditer(
                    r"\(constraint\s+(clearance|track_width|hole_size)"
                    r"\s*\(min\s+([\d.]+)\s*mm\)\)", txt):
                kind, val = m.group(1), float(m.group(2))
                key = {"clearance": "clearance",
                       "track_width": "track_width",
                       "hole_size": "via_drill"}[kind]
                out[key] = val
                parse["source"][key] = f"kicad_dru:{kind}"
        except (OSError, ValueError) as e:
            parse["errors"].append(f"kicad_dru: {e}")
    out["parse"] = parse
    return out


def verify_loaded(rules: dict, required=("clearance", "clearance_smd",
                                         "via_dia")) -> list:
    """The LOAD PROBE (ByTaymur): a rule the consumer needs must have a
    real source — fallback-sourced required rules are surfaced as
    warnings, never silently accepted as 'the DRC truth'."""
    bad = []
    for key in required:
        src = rules.get("parse", {}).get("source", {}).get(key, "?")
        if src == "fallback":
            bad.append(f"{key}: only fallback ({rules[key]}mm) — "
                       f"board files did not provide it")
    return bad


def readback(pro_path: Path, expect: dict) -> list:
    """DaCapo7 write-then-readback: confirm values actually persisted.
    Returns mismatches ([] = persisted)."""
    try:
        data = json.loads(pro_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return [f"readback failed: {e}"]
    bad = []
    for dotted, val in expect.items():
        cur = data
        try:
            for part in dotted.split("."):
                cur = cur[part]
            if abs(float(cur) - float(val)) > 1e-9:
                bad.append(f"{dotted}: wrote {val}, file has {cur}")
        except (KeyError, TypeError, ValueError):
            bad.append(f"{dotted}: missing after write")
    return bad
