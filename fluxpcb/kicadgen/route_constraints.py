# -*- coding: utf-8 -*-
"""Single source of routing tunables — Z2 wiring.

knowhow_constraints.json is the ONLY place routing constants live;
routegrid/fluxrouter call load() at startup. Kept pcbnew-free so plain
pytest can verify the file parses and drifts are caught (K15 found the
JSON unreferenced with three drifted duplicates of every value).
"""
from __future__ import annotations

import json
from pathlib import Path

DEFAULTS = {
    "grid": {
        "resolution_mm": 0.16,
        "pad_margin_mm": 0.30,
        # Z3: worst-case constructive bound is CLEAR+w/2+w/2+diag-slack
        # ~= w+0.29, BUT BGA escape channels (0.8mm pitch parallel tracks)
        # leave exactly 0.4mm half-spacing — a constructive margin blocks
        # EVERY cell in the channel (fc: nopath 42/43, routed 0). The
        # veto+retry loop (clear_exact + history penalty) is the residual
        # mechanism; margin stays permissive.
        "track_margin_mm": 0.15,
        "clearance_mm": 0.17,
        "via_annulus_mm": 0.25,
        "via_drill_mm": 0.125,        # radius; 0.25mm drill diameter
        "hole_keep_mm": 0.28,
        "edge_keep_mm": 0.55,
    },
    "track_widths": {"critical_mm": 0.15, "normal_mm": 0.2, "power_mm": 0.4},
    "class_weights": {"critical": 0.8, "normal": 1.0, "power": 1.3},
    "layer_prefs": {
        "critical": {"F": 0.85, "B": 1.15},
        "long_signal_gt_mm": 15.0,
        "long_signal": {"F": 1.15, "B": 0.85},
        "default": {"F": 1.0, "B": 1.0},
    },
    "via": {"cost": 8.0, "min_diameter_mm": 0.5, "min_drill_mm": 0.25,
            "hole_to_hole_mm": 0.25},
    "negotiation": {
        # VTR/VPR schedule (Z2): iter1 ignores congestion (pure shortest
        # path), then geometric 1.3x growth to a cap — the 1995 x1.6
        # doubling oscillates; Zha&Li FPGA'22 + VTR defaults.
        "pfac_first_iter": 0.0,
        "pfac_initial": 0.5,
        "pfac_growth": 1.3,
        "pfac_max": 1000.0,
        "h_increment": 0.4,
        "physical_fail_penalty": 5.0,
        "max_iterations": 20,
    },
    "stackup_6l": {
        "signal_layers": ["F.Cu", "In1.Cu", "In4.Cu", "B.Cu"],
        "power_layers": {"In2.Cu": "3V3", "In3.Cu": "5V"},
        "ground_pour_layers": ["F.Cu", "B.Cu", "In1.Cu", "In4.Cu"],
        # soft preferred-direction bias on inner signal layers (UG583)
        "preferred_direction": {"In1.Cu": "horizontal", "In4.Cu": "vertical"},
    },
}

_JSON_CANDIDATES = [
    Path(__file__).resolve().parent.parent / "knowhow_constraints.json",
    Path(__file__).resolve().parent / "knowhow_constraints.json",
]


def load(path=None) -> dict:
    """Merged constraint dict: DEFAULTS updated by the JSON file (deep)."""
    import sys
    if path:
        candidates = [Path(path)]
    else:
        candidates = _JSON_CANDIDATES
        path = candidates[0]
    data = {}
    try:
        for cand in candidates:
            try:
                data = json.loads(cand.read_text(encoding="utf-8"))
                break
            except OSError:
                continue
        else:
            raise OSError("no candidate readable")
    except (OSError, ValueError) as e:
        print(f"[constraints] WARN {Path(path).name} unreadable ({e}); "
              f"using built-in defaults", file=sys.stderr)
    merged = json.loads(json.dumps(DEFAULTS))       # deep copy
    for k, v in data.items():
        if isinstance(v, dict) and isinstance(merged.get(k), dict):
            merged[k].update(v)
        else:
            merged[k] = v
    return merged
