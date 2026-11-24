"""Plan builder for Component 4.

Reads a board, proves capacity, names the escape sub-problem (fine-pitch
footprints) and the nets that must be pre-routed inside those regions,
and returns a content-addressed RoutingPlan.  It never routes: the
executor consumes the plan.
"""
from __future__ import annotations

import hashlib
import itertools
import json
import math
from pathlib import Path

from .capacity import capacity_claim
from .plan import CapacityClaim, RoutingPlan


def sha256_file(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def board_rules(board) -> dict:
    """Same single source the repair operators use, without importing them."""
    rules = {"min_track_width": 0.2, "min_clearance": 0.2,
             "min_via_drill": 0.3}
    try:
        ds = board.GetDesignSettings()
        rules["min_track_width"] = ds.m_TrackMinWidth / 1e6 or 0.2
        rules["min_clearance"] = ds.m_MinClearance / 1e6 or 0.2
        rules["min_via_drill"] = ds.m_MinThroughDrill / 1e6 or 0.3
    except Exception:
        pass
    pro = Path(board.GetFileName()).with_suffix(".kicad_pro")
    try:
        data = json.loads(pro.read_text(encoding="utf-8"))
        classes = (data.get("net_settings", {}) or {}).get("classes", []) or []
        vals = [c for c in classes if isinstance(c, dict)]
        if vals:
            rules["min_track_width"] = max(
                [rules["min_track_width"]]
                + [float(c.get("track_width") or 0) for c in vals])
            rules["min_clearance"] = max(
                [rules["min_clearance"]]
                + [float(c.get("clearance") or 0) for c in vals])
    except (OSError, ValueError):
        pass
    return rules


def rule_digest(board_path) -> str:
    pro = Path(board_path).with_suffix(".kicad_pro")
    try:
        from ..contract.rules import RuleModel
        return RuleModel.from_project(pro).digest()
    except Exception:
        try:
            return sha256_file(pro)
        except OSError:
            return "no-project"


def _fine_pitch_regions(board) -> list:
    """Escape sub-problems: dense footprints at <= 1.0 mm pitch."""
    import math

    regions = []
    for fp in board.GetFootprints():
        pads = list(fp.Pads())
        if len(pads) < 16:
            continue
        pos = [(p.GetPosition().x / 1e6, p.GetPosition().y / 1e6)
               for p in pads]
        best = float("inf")
        for i in range(len(pos)):
            for j in range(i + 1, len(pos)):
                d = math.hypot(pos[i][0] - pos[j][0],
                               pos[i][1] - pos[j][1])
                if 0.01 < d < best:
                    best = d
        if math.isinf(best) or best > 1.0:
            continue
        xs = [p[0] for p in pos]
        ys = [p[1] for p in pos]
        cx = (min(xs) + max(xs)) / 2.0
        cy = (min(ys) + max(ys)) / 2.0
        radius = math.hypot(max(xs) - min(xs), max(ys) - min(ys)) / 2.0 + 1.0
        nets = sorted({p.GetNetname() for p in pads if p.GetNetname()})
        regions.append({
            "footprint": fp.GetReference(),
            "center": [round(cx, 3), round(cy, 3)],
            "radius_mm": round(radius, 3),
            "pitch_mm": round(best / 0.05) * 0.05,
            "pads": len(pads),
            "nets": nets,
        })
    return regions


def _short_pair_nets(board, max_mm: float, max_pads: int = 40) -> list:
    """Nets that have a close pad pair, most local first.

    The second pre-route rule.  Escape regions (fine-pitch footprints) are
    rare on real boards, so a plan that only pre-routes them changes
    nothing almost everywhere.  Committing the most local connections
    before the global router is the general form of the same idea: the
    plan decides first what is easy and constrained, the router fills the
    rest around it.
    """
    by_net = {}
    for fp in board.GetFootprints():
        for pad in fp.Pads():
            n = pad.GetNetname()
            if not n:
                continue
            q = pad.GetPosition()
            by_net.setdefault(n, []).append((q.x / 1e6, q.y / 1e6))
    out = []
    for n, pts in by_net.items():
        if len(pts) < 2 or len(pts) > max_pads:
            continue
        best = min(math.hypot(a[0] - b[0], a[1] - b[1])
                   for a, b in itertools.combinations(pts, 2))
        if best <= max_mm:
            out.append((n, best))
    out.sort(key=lambda t: (round(t[1], 3), t[0]))
    return out


def build_plan(board_path, budgets: dict = None,
               pre_pair_mm: float = None) -> RoutingPlan:
    """Build the typed plan for one board (no mutation).

    `pre_pair_mm` adds the short-connection rule to the pre-route list;
    None keeps the escape-region rule only.
    """
    import pcbnew

    path = str(board_path)
    board = pcbnew.LoadBoard(path)
    rules = board_rules(board)
    cap = capacity_claim(board, rules)

    regions = _fine_pitch_regions(board)
    pre = []
    if cap.verdict != "infeasible":
        for region in regions:
            for net in region["nets"]:
                if net not in pre:
                    pre.append(net)
        if pre_pair_mm is not None:
            for net, _d in _short_pair_nets(board, pre_pair_mm):
                if net not in pre:
                    pre.append(net)
        pre = pre[:24]

    order = [n for n in pre]
    remaining = []
    for fp in board.GetFootprints():
        for pad in fp.Pads():
            n = pad.GetNetname()
            if n and n not in order and n not in remaining:
                remaining.append(n)
    order += remaining

    claims = [{
        "id": "capacity",
        "verdict": cap.verdict,
        "evidence": {k: cap.evidence.get(k) for k in
                     ("footprint", "pitch_mm", "pad_gap_mm",
                      "traces_per_channel", "required_rings",
                      "capacity_rings") if k in cap.evidence},
    }]
    if regions:
        claims.append({
            "id": "escape_regions",
            "count": len(regions),
            "nets": sum(len(r["nets"]) for r in regions),
            "pre_routed": len(pre),
        })

    return RoutingPlan(
        board_sha=sha256_file(path),
        rule_digest=rule_digest(path),
        capacity=cap,
        layer_plan={},
        escape_regions=regions,
        net_order=order,
        pre_routed_nets=pre,
        budgets=budgets or {"replan_max": 4, "ripup_max": 8,
                            "maze_max_seconds": 30},
        claims=claims,
    )


def plan_for_feasibility(board_path) -> dict:
    """Cheap L0 check: capacity verdict only (no net enumeration)."""
    import pcbnew

    board = pcbnew.LoadBoard(str(board_path))
    cap = capacity_claim(board, board_rules(board))
    return {"verdict": cap.verdict,
            "evidence": cap.evidence,
            "suggestions": list(cap.suggestions)}
