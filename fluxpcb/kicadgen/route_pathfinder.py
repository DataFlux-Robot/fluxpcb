# -*- coding: utf-8 -*-
"""route_pathfinder — 6-layer fanout-aware PathFinder router with exact-nm verification.

Designed as a fast, deterministic, crash-free proof-carrying routing backend
for multi-layer BGA boards (e.g. STM32H743 TFBGA240 flight controller) where
headless Freerouting either times out or crashes and greedy L/A* stalls.

Architecture:
  1. Rules-driven primitives: derives legal track width, via diameter, drill,
     annular ring, hole-to-hole and edge keepouts from the board's exact rules.
  2. Exact integer-nm spatial-hashed verifier (ExactBoard): mirrors
     fluxpcb.kernels.fab's exact integer geometry (segments, circles, pad
     polygons, drills, outline) so committed copper is fab-clean by construction.
  3. Phase 1 — BGA & fine-pitch fanout:
       * When dogbone fits under active rules, invokes bga_escape_solver (MCMF)
         for violation-free dogbone escape of signal + power balls.
       * When board rules mathematically forbid dogbone (e.g. 0.15 mm clearance
         + 0.45/0.50 mm via on 0.80 mm BGA), bridges adjacent same-net power
         balls on F.Cu, escapes outer-ring balls straight out on F.Cu, and
         places legal via-in-pad (VIPPO) on interior/power balls.
       * Computes continuous exact-verified escaped ports for every pad (0.5mm
         LGA, 0.8mm BGA, 1.27mm THT headers) and every existing same-net via/track
         vertex so the grid router connects whole islands rather than boxed pads.
  4. Phase 2 — 6-layer multi-island PathFinder negotiated congestion:
       * BGA-aligned 0.20 mm grid (0.80 mm pitch = exact 4 cells; diagonal 45 deg
         hops between inner-layer vias have exact +100 um margin).
       * Active-cell sparse history updates (100x faster than full-grid scans).
       * Partial rip-up of conflicted nets only; alternating H/V preferred dirs.
       * Multi-source to multi-target 3D A* merges islands into Steiner-like trees.
  5. Phase 3 — Exact-nm legalization with reversible per-net occupancy:
       * Committed nets live in ExactBoard (tagged `net:<name>`) and `occ/via_occ`,
         leaving `fixed_owner` purely for immutable pads & protected copper.
  6. Phase 4 — Bounded local blocker-eviction (rip-and-swap) + residual VIPPO/via
     rescue:
       * For any residual open net, finds the 1-3 blocking routed nets via soft
         A*, atomically evicts them, routes the open net cleanly, and re-routes
         the evicted nets around it (rolling back byte-cleanly if any fails).

Usage (inside KiCad Python):
    python route_pathfinder.py <board.kicad_pcb> <config.json>
"""
from __future__ import annotations

import array
import heapq
import json
import math
import random
import sys
import time
from collections import defaultdict
from pathlib import Path

import pcbnew

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from fluxpcb.proof import geom as _geom  # noqa: E402

MM = 1_000_000
_PTH = getattr(pcbnew, "PAD_ATTRIB_PTH",
               getattr(pcbnew, "PAD_ATTRIB_THROUGH_HOLE", 3))
_NPTH = getattr(pcbnew, "PAD_ATTRIB_NPTH", 2)


# ---------------------------------------------------------------- exact-nm math
def _frac_lt(n1: int, d1: int, n2: int, d2: int) -> bool:
    return n1 * d2 < n2 * d1


def _seg_seg_d2(a1, a2, b1, b2):
    if _geom.segs_intersect(a1, a2, b1, b2):
        return (0, 1)
    best = _geom.pt_seg_d2(a1, b1, b2)
    for p, u, v in ((a2, b1, b2), (b1, a1, a2), (b2, a1, a2)):
        d = _geom.pt_seg_d2(p, u, v)
        if _frac_lt(d[0], d[1], best[0], best[1]):
            best = d
    return best


def _pt_poly_d2(p, pts):
    if _geom.pt_in_poly(p, pts):
        return (0, 1)
    best = None
    n = len(pts)
    for k in range(n):
        d = _geom.pt_seg_d2(p, pts[k], pts[(k + 1) % n])
        if best is None or _frac_lt(d[0], d[1], best[0], best[1]):
            best = d
    return best if best is not None else (0, 1)


def _seg_poly_d2(a, b, w, pts):
    if _geom.seg_poly(a, b, w, pts):
        return (0, 1)
    best = _pt_poly_d2(a, pts)
    d = _pt_poly_d2(b, pts)
    if _frac_lt(d[0], d[1], best[0], best[1]):
        best = d
    for v in pts:
        d = _geom.pt_seg_d2(v, a, b)
        if _frac_lt(d[0], d[1], best[0], best[1]):
            best = d
    return best


def _dist_ok(num: int, den: int, extra_nm: int, rule_nm: int) -> bool:
    need = 2 * rule_nm + extra_nm
    return 4 * num >= need * need * den


def _shape_bbox(sh):
    k = sh[0]
    if k == 0:      # seg: (0, x1, y1, x2, y2, w)
        hw = (sh[5] + 1) // 2
        return (min(sh[1], sh[3]) - hw, min(sh[2], sh[4]) - hw,
                max(sh[1], sh[3]) + hw, max(sh[2], sh[4]) + hw)
    if k == 1:      # circle: (1, cx, cy, r)
        return (sh[1] - sh[3], sh[2] - sh[3], sh[1] + sh[3], sh[2] + sh[3])
    pts = sh[1]     # poly: (2, ((x,y), ...))
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return (min(xs), min(ys), max(xs), max(ys))


def _pad_shape_nm(pad, lid):
    p = pad.GetPosition()
    sz = pad.GetSize()
    if pad.GetShape() == pcbnew.PAD_SHAPE_CIRCLE:
        return (1, int(p.x), int(p.y), int(sz.x) // 2)
    try:
        poly = pad.GetEffectivePolygon(lid)
        if poly.OutlineCount() > 0:
            ch = poly.Outline(0)
            pts = tuple((int(ch.CPoint(k).x), int(ch.CPoint(k).y))
                        for k in range(ch.PointCount()))
            if len(pts) >= 3:
                return (2, pts)
    except Exception:
        pass
    cx, cy = int(p.x), int(p.y)
    hw, hh = int(sz.x) / 2.0, int(sz.y) / 2.0
    ang = math.radians(pad.GetOrientation().AsDegrees())
    ca, sa = math.cos(ang), math.sin(ang)
    pts = tuple((int(round(cx + sx * hw * ca - sy * hh * sa)),
                 int(round(cy + sx * hw * sa + sy * hh * ca)))
                for sx, sy in ((1, 1), (-1, 1), (-1, -1), (1, -1)))
    return (2, pts)


def _shapes_touch_nm(a, b) -> bool:
    """Exact integer touch check (matches Lean Geom.shapeTouch)."""
    ka, kb = a[0], b[0]
    if ka == 0 and kb == 0:
        return _geom.seg_seg((a[1], a[2]), (a[3], a[4]), a[5],
                             (b[1], b[2]), (b[3], b[4]), b[5])
    if ka == 0 and kb == 1:
        return _geom.circle_seg((b[1], b[2], b[3]), (a[1], a[2]), (a[3], a[4]), a[5])
    if ka == 1 and kb == 0:
        return _geom.circle_seg((a[1], a[2], a[3]), (b[1], b[2]), (b[3], b[4]), b[5])
    if ka == 1 and kb == 1:
        dx, dy = a[1] - b[1], a[2] - b[2]
        r = a[3] + b[3]
        return dx * dx + dy * dy <= r * r
    if ka == 0 and kb == 2:
        return _geom.seg_poly((a[1], a[2]), (a[3], a[4]), a[5], b[1])
    if ka == 2 and kb == 0:
        return _geom.seg_poly((b[1], b[2]), (b[3], b[4]), b[5], a[1])
    if ka == 1 and kb == 2:
        return _geom.circle_poly((a[1], a[2], a[3]), b[1])
    if ka == 2 and kb == 1:
        return _geom.circle_poly((b[1], b[2], b[3]), a[1])
    return _geom.poly_poly(a[1], b[1])


# ---------------------------------------------------------------- ExactBoard
class ExactBoard:
    """Spatial-hashed exact-nm copper + hole + outline verifier."""
    CELL_NM = 1_000_000

    def __init__(self, n_layers: int, clear_nm: int, hole_nm: int, edge_nm: int,
                 outline: list[tuple[int, int]],
                 net_clear_nm: dict[int, int] | None = None,
                 hole_clear_nm: int = 0):
        self.nl = n_layers
        self.clear_nm = clear_nm
        self.net_clear_nm: dict[int, int] = dict(net_clear_nm or {})
        self.max_net_clear_nm = max([clear_nm] + list(self.net_clear_nm.values()))
        self.hole_nm = hole_nm
        self.hole_clear_nm = hole_clear_nm
        self.edge_nm = edge_nm
        self.outline = outline
        self.outline_edges = (list(zip(outline, outline[1:] + outline[:1]))
                              if len(outline) >= 3 else [])
        self.items: dict[int, tuple] = {}
        self.buckets: list[dict[tuple[int, int], list[int]]] = [
            defaultdict(list) for _ in range(n_layers)]
        self.holes: dict[int, tuple] = {}
        self.hole_buckets: dict[tuple[int, int], list[int]] = defaultdict(list)
        self._next_id = 1
        self._by_owner: dict[str, list[int]] = defaultdict(list)
        self._holes_by_owner: dict[str, list[int]] = defaultdict(list)

    def add_item(self, net_code: int, layers: tuple[int, ...], shape: tuple,
                 owner: str = "fixed") -> int:
        iid = self._next_id
        self._next_id += 1
        bb = _shape_bbox(shape)
        self.items[iid] = (net_code, layers, shape, bb, owner)
        self._by_owner[owner].append(iid)
        c = self.CELL_NM
        x0, y0, x1, y1 = bb[0] // c, bb[1] // c, bb[2] // c, bb[3] // c
        for li in layers:
            bk = self.buckets[li]
            for gx in range(x0, x1 + 1):
                for gy in range(y0, y1 + 1):
                    bk[(gx, gy)].append(iid)
        return iid

    def add_hole(self, cx: int, cy: int, drill_nm: int, net_code: int,
                 is_pth: bool = False, owner: str = "fixed") -> int:
        hid = self._next_id
        self._next_id += 1
        self.holes[hid] = (cx, cy, drill_nm, net_code, is_pth, owner)
        self._holes_by_owner[owner].append(hid)
        c = self.CELL_NM
        r = drill_nm // 2
        for gx in range((cx - r) // c, (cx + r) // c + 1):
            for gy in range((cy - r) // c, (cy + r) // c + 1):
                self.hole_buckets[(gx, gy)].append(hid)
        return hid

    def remove_owner(self, owner: str) -> None:
        c = self.CELL_NM
        for iid in self._by_owner.pop(owner, ()):
            it = self.items.pop(iid, None)
            if it is None:
                continue
            _, layers, _, bb, _ = it
            for li in layers:
                bk = self.buckets[li]
                for gx in range(bb[0] // c, bb[2] // c + 1):
                    for gy in range(bb[1] // c, bb[3] // c + 1):
                        lst = bk.get((gx, gy))
                        if lst and iid in lst:
                            lst.remove(iid)
        for hid in self._holes_by_owner.pop(owner, ()):
            h = self.holes.pop(hid, None)
            if h is None:
                continue
            cx, cy, drill_nm = h[0], h[1], h[2]
            r = drill_nm // 2
            for gx in range((cx - r) // c, (cx + r) // c + 1):
                for gy in range((cy - r) // c, (cy + r) // c + 1):
                    lst = self.hole_buckets.get((gx, gy))
                    if lst and hid in lst:
                        lst.remove(hid)

    def _edge_ok_pt(self, px: int, py: int, hw: int) -> bool:
        if not self.outline_edges:
            return True
        if self.edge_nm <= 0:
            # When copper_edge_clearance is unconstrained (0), allow castellated /
            # board-edge pads whose centers lie slightly on or outside Edge.Cuts.
            if _geom.pt_in_poly((px, py), self.outline):
                return True
            tol2 = 1_500_000 * 1_500_000
            for a, b in self.outline_edges:
                num, den = _geom.pt_seg_d2((px, py), a, b)
                if num <= tol2 * den:
                    return True
            return False
        if not _geom.pt_in_poly((px, py), self.outline):
            return False
        need = self.edge_nm + hw
        need2 = need * need
        for a, b in self.outline_edges:
            num, den = _geom.pt_seg_d2((px, py), a, b)
            if num < need2 * den:
                return False
        return True

    def seg_blockers(self, x1: int, y1: int, x2: int, y2: int, w: int,
                     li: int, net_code: int, ignore_owner: str | None = None,
                     find_all: bool = False) -> list[str]:
        hw = (w + 1) // 2
        if not self._edge_ok_pt(x1, y1, hw) or not self._edge_ok_pt(x2, y2, hw):
            return ["__edge__"]
        nc_rule = max(self.clear_nm, self.net_clear_nm.get(net_code, 0))
        reach = hw + max(nc_rule, self.max_net_clear_nm)
        bx0 = min(x1, x2) - reach
        by0 = min(y1, y2) - reach
        bx1 = max(x1, x2) + reach
        by1 = max(y1, y2) + reach
        c = self.CELL_NM
        bk = self.buckets[li]
        seen = set()
        found = []
        a, b = (x1, y1), (x2, y2)
        if self.hole_clear_nm > 0:
            h_reach = hw + self.hole_clear_nm + 1_000_000
            seen_h = set()
            for gx in range((min(x1, x2) - h_reach) // c, (max(x1, x2) + h_reach) // c + 1):
                for gy in range((min(y1, y2) - h_reach) // c, (max(y1, y2) + h_reach) // c + 1):
                    for hid in self.hole_buckets.get((gx, gy), ()):
                        if hid in seen_h:
                            continue
                        seen_h.add(hid)
                        h = self.holes.get(hid)
                        if h is None:
                            continue
                        hx, hy, hdr, hnc, _, hown = h
                        if hown == ignore_owner or (hnc != 0 and hnc == net_code):
                            continue
                        num, den = _geom.pt_seg_d2((hx, hy), a, b)
                        if not _dist_ok(num, den, w + hdr, self.hole_clear_nm):
                            if not find_all:
                                return [hown]
                            if hown not in found:
                                found.append(hown)
        for gx in range(bx0 // c, bx1 // c + 1):
            for gy in range(by0 // c, by1 // c + 1):
                for iid in bk.get((gx, gy), ()):
                    if iid in seen:
                        continue
                    seen.add(iid)
                    it = self.items.get(iid)
                    if it is None:
                        continue
                    inc, _, sh, ibb, own = it
                    if inc == net_code or own == ignore_owner:
                        continue
                    if ibb[2] < bx0 or ibb[0] > bx1 or ibb[3] < by0 or ibb[1] > by1:
                        continue
                    rule = max(nc_rule, self.net_clear_nm.get(inc, 0))
                    hit = False
                    k = sh[0]
                    if k == 0:
                        num, den = _seg_seg_d2(a, b, (sh[1], sh[2]), (sh[3], sh[4]))
                        hit = not _dist_ok(num, den, w + sh[5], rule)
                    elif k == 1:
                        num, den = _geom.pt_seg_d2((sh[1], sh[2]), a, b)
                        hit = not _dist_ok(num, den, w + 2 * sh[3], rule)
                    else:
                        num, den = _seg_poly_d2(a, b, w, sh[1])
                        hit = not _dist_ok(num, den, w, rule)
                    if hit:
                        if not find_all:
                            return [own]
                        if own not in found:
                            found.append(own)
        return found

    def seg_clear(self, x1: int, y1: int, x2: int, y2: int, w: int,
                  li: int, net_code: int, ignore_owner: str | None = None):
        bl = self.seg_blockers(x1, y1, x2, y2, w, li, net_code,
                               ignore_owner=ignore_owner, find_all=False)
        return (not bl), (bl[0] if bl else None)

    def via_blockers(self, cx: int, cy: int, r: int, drill_nm: int, net_code: int,
                     ignore_owner: str | None = None,
                     find_all: bool = False) -> list[str]:
        if not self._edge_ok_pt(cx, cy, r):
            return ["__edge__"]
        found = []
        c = self.CELL_NM
        h_reach = drill_nm // 2 + self.hole_nm + 1_000_000
        seen_h = set()
        for gx in range((cx - h_reach) // c, (cx + h_reach) // c + 1):
            for gy in range((cy - h_reach) // c, (cy + h_reach) // c + 1):
                for hid in self.hole_buckets.get((gx, gy), ()):
                    if hid in seen_h:
                        continue
                    seen_h.add(hid)
                    h = self.holes.get(hid)
                    if h is None:
                        continue
                    hx, hy, hdr, hnc, _, hown = h
                    if hown == ignore_owner:
                        continue
                    dx, dy = cx - hx, cy - hy
                    min_c = (drill_nm + hdr) // 2 + self.hole_nm
                    if dx * dx + dy * dy < min_c * min_c:
                        if not find_all:
                            return [hown]
                        if hown not in found:
                            found.append(hown)
        nc_rule = max(self.clear_nm, self.net_clear_nm.get(net_code, 0))
        reach = r + max(nc_rule, self.max_net_clear_nm)
        bx0, by0, bx1, by1 = cx - reach, cy - reach, cx + reach, cy + reach
        for li in range(self.nl):
            bk = self.buckets[li]
            seen = set()
            for gx in range(bx0 // c, bx1 // c + 1):
                for gy in range(by0 // c, by1 // c + 1):
                    for iid in bk.get((gx, gy), ()):
                        if iid in seen:
                            continue
                        seen.add(iid)
                        it = self.items.get(iid)
                        if it is None:
                            continue
                        inc, _, sh, ibb, own = it
                        if inc == net_code or own == ignore_owner:
                            continue
                        if ibb[2] < bx0 or ibb[0] > bx1 or ibb[3] < by0 or ibb[1] > by1:
                            continue
                        rule = max(nc_rule, self.net_clear_nm.get(inc, 0))
                        hit = False
                        k = sh[0]
                        if k == 0:
                            num, den = _geom.pt_seg_d2((cx, cy), (sh[1], sh[2]), (sh[3], sh[4]))
                            hit = not _dist_ok(num, den, 2 * r + sh[5], rule)
                        elif k == 1:
                            dx, dy = cx - sh[1], cy - sh[2]
                            need = r + sh[3] + rule
                            hit = dx * dx + dy * dy < need * need
                        else:
                            num, den = _pt_poly_d2((cx, cy), sh[1])
                            hit = not _dist_ok(num, den, 2 * r, rule)
                        if hit:
                            if not find_all:
                                return [own]
                            if own not in found:
                                found.append(own)
        return found

    def via_clear(self, cx: int, cy: int, r: int, drill_nm: int, net_code: int,
                  ignore_owner: str | None = None):
        bl = self.via_blockers(cx, cy, r, drill_nm, net_code,
                               ignore_owner=ignore_owner, find_all=False)
        return (not bl), (bl[0] if bl else None)


# ---------------------------------------------------------------- board helpers
def _read_pro_rules_nm(pcb_path: Path) -> dict[str, int]:
    """Read exact design rules and Default netclass from companion .kicad_pro (nm)."""
    pro = pcb_path.with_suffix(".kicad_pro")
    if not pro.exists():
        return {}
    try:
        data = json.loads(pro.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    out: dict[str, int] = {}
    rules = (data.get("board") or {}).get("design_settings", {}).get("rules") or {}
    sev = (data.get("board") or {}).get("design_settings", {}).get("rule_severities") or {}
    keymap = (
        ("clearance", "min_clearance"),
        ("track_min", "min_track_width"),
        ("via_min", "min_via_diameter"),
        ("drill_min", "min_through_hole_diameter"),
        ("annular", "min_via_annular_width"),
        ("edge", "min_copper_edge_clearance"),
        ("hole_to_hole", "min_hole_to_hole"),
    )
    for dst, src in keymap:
        if src in rules and rules[src] is not None:
            try:
                out[dst] = int(round(float(rules[src]) * MM))
            except (ValueError, TypeError):
                pass
    # Only enforce min_hole_clearance when the project treats hole_clearance as an error
    if sev.get("hole_clearance") == "error" and rules.get("min_hole_clearance"):
        try:
            out["hole_clearance"] = int(round(float(rules["min_hole_clearance"]) * MM))
        except (ValueError, TypeError):
            pass
    # Also respect Default netclass & per-netclass clearances (KiCad enforces max(classA, classB))
    ns = data.get("net_settings") or {}
    cls_clear: dict[str, int] = {}
    net_clear_exact: dict[str, int] = {}
    net_clear_patterns: list[tuple[str, int]] = []
    for nc in (ns.get("classes") or []):
        cname = nc.get("name") or ""
        try:
            c_val = int(round(float(nc.get("clearance") or 0) * MM))
        except (ValueError, TypeError):
            c_val = 0
        if cname and c_val > 0:
            cls_clear[cname] = c_val
        if cname == "Default":
            for dst, src in (("nc_clearance", "clearance"),
                             ("nc_track", "track_width"),
                             ("nc_via_diam", "via_diameter"),
                             ("nc_via_drill", "via_drill")):
                if src in nc and nc[src] is not None:
                    try:
                        val = int(round(float(nc[src]) * MM))
                        if val > 0:
                            out[dst] = val
                    except (ValueError, TypeError):
                        pass
        for nm in (nc.get("nets") or []):
            if nm and c_val > 0:
                net_clear_exact[nm] = max(net_clear_exact.get(nm, 0), c_val)
    for nm, cnames in (ns.get("netclass_assignments") or {}).items():
        if isinstance(cnames, str):
            cnames = [cnames]
        for cn in (cnames or []):
            c_val = cls_clear.get(cn, 0)
            if c_val > 0:
                net_clear_exact[nm] = max(net_clear_exact.get(nm, 0), c_val)
    for pat_obj in (ns.get("netclass_patterns") or []):
        pat = pat_obj.get("pattern") or ""
        cn = pat_obj.get("netclass") or ""
        c_val = cls_clear.get(cn, 0)
        if pat and c_val > 0:
            net_clear_patterns.append((pat, c_val))
    if net_clear_exact:
        out["net_clear_exact"] = net_clear_exact
    if net_clear_patterns:
        out["net_clear_patterns"] = net_clear_patterns
    return out


def _read_pro_via_min_nm(pcb_path: Path) -> int:
    return _read_pro_rules_nm(pcb_path).get("via_min", 0)


def _board_outline_nm(board) -> list[tuple[int, int]]:
    try:
        poly = pcbnew.SHAPE_POLY_SET()
        if board.GetBoardPolygonOutlines(poly, True) and poly.OutlineCount() > 0:
            ch = poly.Outline(0)
            return [(int(ch.CPoint(k).x), int(ch.CPoint(k).y))
                    for k in range(ch.PointCount())]
    except Exception:
        pass
    bb = board.GetBoardEdgesBoundingBox()
    x0, y0 = int(bb.GetX()), int(bb.GetY())
    x1, y1 = x0 + int(bb.GetWidth()), y0 + int(bb.GetHeight())
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]


def _extract_graphics_nm(shapes, lid_to_i):
    out = []
    for sp in shapes:
        if sp.GetClass() != "PCB_SHAPE":
            continue
        li = lid_to_i.get(sp.GetLayer())
        if li is None:
            continue
        w = int(sp.GetWidth() or 0)
        t = sp.GetShape()
        try:
            filled = sp.GetFillMode() != 0
        except Exception:
            filled = False
        try:
            if t == pcbnew.SHAPE_T_SEGMENT:
                a, c = sp.GetStart(), sp.GetEnd()
                out.append((li, (0, int(a.x), int(a.y), int(c.x), int(c.y), w)))
            elif t == pcbnew.SHAPE_T_RECT:
                a, c = sp.GetStart(), sp.GetEnd()
                x0, x1 = sorted((int(a.x), int(c.x)))
                y0, y1 = sorted((int(a.y), int(c.y)))
                pts = ((x0, y0), (x1, y0), (x1, y1), (x0, y1))
                if filled or not w:
                    out.append((li, (2, pts)))
                if w:
                    for k in range(4):
                        p, q = pts[k], pts[(k + 1) % 4]
                        out.append((li, (0, p[0], p[1], q[0], q[1], w)))
            elif t == pcbnew.SHAPE_T_CIRCLE:
                cc = sp.GetCenter()
                r = int(sp.GetRadius())
                if filled:
                    out.append((li, (1, int(cc.x), int(cc.y), r)))
                if w:
                    n = 36
                    for k in range(n):
                        a0, a1 = 2 * math.pi * k / n, 2 * math.pi * (k + 1) / n
                        out.append((li, (0, int(cc.x + r * math.cos(a0)),
                                         int(cc.y + r * math.sin(a0)),
                                         int(cc.x + r * math.cos(a1)),
                                         int(cc.y + r * math.sin(a1)), w)))
            elif t == pcbnew.SHAPE_T_POLY:
                poly = sp.GetPolyShape()
                if poly.OutlineCount() > 0:
                    ch = poly.Outline(0)
                    pts = tuple((int(ch.CPoint(k).x), int(ch.CPoint(k).y))
                                for k in range(ch.PointCount()))
                    if len(pts) >= 3:
                        if filled:
                            out.append((li, (2, pts)))
                        if w:
                            for k in range(len(pts)):
                                p, q = pts[k], pts[(k + 1) % len(pts)]
                                out.append((li, (0, p[0], p[1], q[0], q[1], w)))
            elif t == pcbnew.SHAPE_T_ARC:
                a, c = sp.GetStart(), sp.GetEnd()
                out.append((li, (0, int(a.x), int(a.y), int(c.x), int(c.y), w)))
        except Exception:
            continue
    return out


# ---------------------------------------------------------------- islands (exact)
def _compute_net_islands(net_items):
    """Exact copper connected components over a net's items (pads, tracks, vias)."""
    n = len(net_items)
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    bboxes = [_shape_bbox(it[2]) for it in net_items]
    for a in range(n):
        la, sha, bba = net_items[a][1], net_items[a][2], bboxes[a]
        for b in range(a + 1, n):
            if find(a) == find(b):
                continue
            lb, shb, bbb = net_items[b][1], net_items[b][2], bboxes[b]
            if bba[2] < bbb[0] or bbb[2] < bba[0] or bba[3] < bbb[1] or bbb[3] < bba[1]:
                continue
            if not any(l in lb for l in la):
                continue
            if _shapes_touch_nm(sha, shb):
                union(a, b)

    groups = defaultdict(list)
    for i in range(n):
        groups[find(i)].append(i)
    return [idxs for idxs in groups.values() if any(net_items[i][3] for i in idxs)]


# ---------------------------------------------------------------- PathFinder v2
class SixLayerPathFinder:
    """6-layer fanout-aware PathFinder + exact-nm legalizer + bounded rip-and-swap."""

    def __init__(self, board, pcb_path: Path | str, cfg: dict):
        self.board = board
        self.pcb_path = Path(pcb_path)
        self.cfg = cfg
        self.t0 = time.time()
        self.time_budget = float(cfg.get("time_budget_s", 120.0))
        self.max_iters = int(cfg.get("max_iters", 26))
        self.mode = str(cfg.get("mode", "keep")).lower()
        self.allow_vippo = cfg.get("allow_vippo", "auto")

        self.lids = list(board.GetEnabledLayers().CuStack())
        self.nl = len(self.lids)
        self.lid_to_i = {lid: i for i, lid in enumerate(self.lids)}
        self.all_li = tuple(range(self.nl))
        self.f_i = self.lid_to_i.get(pcbnew.F_Cu, 0)
        self.b_i = self.lid_to_i.get(pcbnew.B_Cu, self.nl - 1)

        # Rules (nm): merge explicit cfg["rules"] with companion .kicad_pro rules
        r = cfg.get("rules") or {}
        pro_r = _read_pro_rules_nm(self.pcb_path)

        def _pick_rule(key: str, default: int, allow_zero: bool = False) -> int:
            if key in r and r[key] is not None:
                v = int(r[key])
                if allow_zero or v > 0:
                    return v
            if key in pro_r and pro_r[key] is not None:
                v = int(pro_r[key])
                if allow_zero or v > 0:
                    return v
            return default

        self.clear_nm = max(_pick_rule("clearance", 150_000),
                            int(pro_r.get("nc_clearance", 0)) if not r.get("clearance") else 0)
        # KiCad DRC enforces min_track_width / min_via_diameter / min_through_hole_diameter,
        # NOT Default netclass authoring preferences (e.g. 0.70mm track or 0.889mm via).
        # Only fall back to Default netclass when the DRC rule floor is unconstrained (0).
        rule_track = _pick_rule("track_min", 0, allow_zero=True)
        if rule_track > 0:
            self.track_min_nm = rule_track
        else:
            nc_t = int(pro_r.get("nc_track", 0))
            self.track_min_nm = min(nc_t, 250_000) if nc_t > 0 else 150_000
        self.annular_nm = _pick_rule("annular", 100_000, allow_zero=True)
        rule_drill = _pick_rule("drill_min", 0, allow_zero=True)
        if rule_drill > 0:
            self.drill_min_nm = rule_drill
        else:
            nc_dr = int(pro_r.get("nc_via_drill", 0))
            self.drill_min_nm = nc_dr if nc_dr > 0 else 200_000
        self.edge_nm = _pick_rule("edge", 300_000, allow_zero=True)
        self.hole_nm = _pick_rule("hole_to_hole", 250_000, allow_zero=True)
        rule_via_min = max(int(r.get("via_min") or 0), int(pro_r.get("via_min") or 0))
        if rule_via_min > 0:
            self.via_min_nm = max(rule_via_min, 400_000)
        else:
            nc_vd = int(pro_r.get("nc_via_diam", 0))
            self.via_min_nm = max(nc_vd, 400_000) if nc_vd > 0 else 400_000

        # Legal geometry primitives (+5 um safety margin covers KiCad roundrect chord tol)
        self.safe_clear_nm = self.clear_nm + 5_000
        self.track_w_nm = self.track_min_nm + 2_000
        self.via_drill_nm = max(self.drill_min_nm, 200_000)
        self.via_diam_nm = max(self.via_min_nm,
                               self.via_drill_nm + 2 * max(self.annular_nm, 50_000))
        self.via_r_nm = (self.via_diam_nm + 1) // 2

        self.net_by_code = {}
        self.code_by_name = {}
        for nc in range(1, board.GetNetInfo().GetNetCount() + 1):
            n = board.FindNet(nc)
            if n and n.GetNetname():
                self.net_by_code[nc] = n
                self.code_by_name[n.GetNetname()] = nc

        # Resolve per-netclass clearance overrides from .kicad_pro (e.g. guide_W0.35 = 0.25mm)
        import fnmatch as _fnm
        self.net_clear_nm: dict[int, int] = {}
        nc_exact = pro_r.get("net_clear_exact") or {}
        nc_pats = pro_r.get("net_clear_patterns") or []
        for nm, nc in self.code_by_name.items():
            c_ov = nc_exact.get(nm, 0)
            for pat, cv in nc_pats:
                if _fnm.fnmatchcase(nm, pat):
                    c_ov = max(c_ov, cv)
            if c_ov > self.clear_nm:
                self.net_clear_nm[nc] = c_ov + 5_000
        hole_clr_raw = _pick_rule("hole_clearance", 0, allow_zero=True)
        self.hole_clear_nm = (hole_clr_raw + 5_000) if hole_clr_raw > 0 else 0

        self.outline = _board_outline_nm(board)
        xs = [p[0] for p in self.outline]
        ys = [p[1] for p in self.outline]
        pad_pad = 1_500_000 if self.edge_nm <= 0 else 0
        self.bx0, self.by0 = min(xs) - pad_pad, min(ys) - pad_pad
        self.bx1, self.by1 = max(xs) + pad_pad, max(ys) + pad_pad

        self.bgas = self._detect_bgas()
        if "grid_mm" in cfg and cfg["grid_mm"]:
            grid_mm = float(cfg["grid_mm"])
        else:
            span_mm = max(self.bx1 - self.bx0, self.by1 - self.by0) / MM
            if self.bgas or span_mm <= 90.0:
                grid_mm = 0.20
            elif span_mm <= 150.0:
                grid_mm = 0.25
            else:
                grid_mm = 0.32
        self.grid_nm = int(round(grid_mm * MM))
        if self.bgas:
            b0 = self.bgas[0]
            self.ox = b0["x0"] - ((b0["x0"] - self.bx0) // self.grid_nm + 1) * self.grid_nm
            self.oy = b0["y0"] - ((b0["y0"] - self.by0) // self.grid_nm + 1) * self.grid_nm
        else:
            self.ox = self.bx0
            self.oy = self.by0
        self.nx = (self.bx1 - self.ox) // self.grid_nm + 3
        self.ny = (self.by1 - self.oy) // self.grid_nm + 3
        self.nn = self.nx * self.ny

        self.pref_h = [i % 2 == 0 for i in range(self.nl)]

        self.zone_layers = set()
        self.plane_layer_nets: dict[int, str] = {}
        for z in board.Zones():
            zn = z.GetNetname() if hasattr(z, "GetNetname") else ""
            for lid in self.lids:
                if z.IsOnLayer(lid):
                    li = self.lid_to_i[lid]
                    self.zone_layers.add(li)
                    if zn and li != self.f_i and li != self.b_i:
                        self.plane_layer_nets[li] = zn
        for lname, netname in (cfg.get("plane_layers") or {}).items():
            lid = board.GetLayerID(str(lname))
            if lid in self.lid_to_i:
                li = self.lid_to_i[lid]
                self.zone_layers.add(li)
                self.plane_layer_nets[li] = str(netname)

        self.exact = ExactBoard(self.nl, self.safe_clear_nm,
                                self.hole_nm + (4_000 if self.hole_nm > 0 else 0),
                                self.edge_nm + (4_000 if self.edge_nm > 0 else 0),
                                self.outline,
                                net_clear_nm=self.net_clear_nm,
                                hole_clear_nm=self.hole_clear_nm)
        self.fixed_owner = [array.array("i", [0]) * self.nn for _ in range(self.nl)]
        self.fixed_via_owner = array.array("i", [0]) * self.nn

        self.occ = [array.array("h", [0]) * self.nn for _ in range(self.nl)]
        self.hist = [array.array("f", [0.0]) * self.nn for _ in range(self.nl)]
        self.via_occ = array.array("h", [0]) * self.nn
        self.via_hist = array.array("f", [0.0]) * self.nn

        self.track_halo = self._disk_offsets(
            self.track_w_nm + self.safe_clear_nm - 1)
        self.via_track_halo = self._disk_offsets(
            self.via_r_nm + self.track_w_nm // 2 + self.safe_clear_nm)
        self.via_via_halo = self._disk_offsets(
            max(self.via_diam_nm + self.safe_clear_nm,
                self.via_drill_nm + self.hole_nm + 4_000))

        # Domain synthesis specs lowered from Component 4/7 (diff pairs + power widths)
        self.diff_pairs_cfg: list[dict] = list(cfg.get("diff_pairs") or [])
        self.net_widths_nm: dict[int, int] = {}
        for nm, w_mm in (cfg.get("net_widths_mm") or {}).items():
            nc = self.code_by_name.get(nm)
            if nc and w_mm:
                w_nm = int(round(float(w_mm) * MM))
                if w_nm > self.track_w_nm:
                    self.net_widths_nm[nc] = w_nm
        self.diff_pair_stats: list[dict] = []
        self.power_widened_segs = 0
        self.plane_stitch_vias = 0

        self.added_segs = 0
        self.added_vias = 0
        self.fanout_vias = 0
        self.fanout_segs = 0
        self.swaps_ok = 0
        self.net_items_map = {}
        # Reversible records for Phase 3/4 committed nets
        self.committed_records: dict[str, dict] = {}
        self.bga_dogbone_ok = False
        self.bga_vippo_allowed = False

    def _disk_offsets(self, radius_nm: int) -> list[tuple[int, int]]:
        r_cells = int(math.ceil(radius_nm / self.grid_nm))
        out = []
        r2 = radius_nm * radius_nm
        for dx in range(-r_cells, r_cells + 1):
            for dy in range(-r_cells, r_cells + 1):
                if (dx * self.grid_nm) ** 2 + (dy * self.grid_nm) ** 2 <= r2:
                    out.append((dx, dy))
        return out

    def cell_xy(self, gx: int, gy: int) -> tuple[int, int]:
        return (self.ox + gx * self.grid_nm, self.oy + gy * self.grid_nm)

    def xy_cell(self, x: int, y: int) -> tuple[int, int]:
        return (int(round((x - self.ox) / self.grid_nm)),
                int(round((y - self.oy) / self.grid_nm)))

    def _detect_bgas(self) -> list[dict]:
        bgas = []
        for fp in self.board.GetFootprints():
            pads = list(fp.Pads())
            if len(pads) < 36:
                continue
            if not all(p.GetShape() == pcbnew.PAD_SHAPE_CIRCLE
                       and p.GetAttribute() != _PTH for p in pads):
                continue
            xs = sorted({int(p.GetPosition().x) for p in pads})
            ys = sorted({int(p.GetPosition().y) for p in pads})
            if len(xs) < 6 or len(ys) < 6:
                continue
            pitch = min(b - a for a, b in zip(xs, xs[1:]) if b - a > 10_000)
            pad_d = max(int(p.GetSize().x) for p in pads)
            bgas.append({
                "ref": str(fp.GetReference()),
                "fp": fp,
                "x0": xs[0], "y0": ys[0],
                "nx": round((xs[-1] - xs[0]) / pitch) + 1,
                "ny": round((ys[-1] - ys[0]) / pitch) + 1,
                "pitch": pitch, "pad_d": pad_d,
            })
        return bgas

    # ---- fixed grid marking
    def _mark_fixed_cell(self, li: int, gx: int, gy: int, nc: int):
        if gx < 0 or gy < 0 or gx >= self.nx or gy >= self.ny:
            return
        idx = gy * self.nx + gx
        arr = self.fixed_owner[li]
        cur = arr[idx]
        if cur == 0:
            arr[idx] = nc if nc > 0 else -1
        elif cur != nc:
            arr[idx] = -1

    def _mark_fixed_via_cell(self, gx: int, gy: int, nc: int):
        if gx < 0 or gy < 0 or gx >= self.nx or gy >= self.ny:
            return
        idx = gy * self.nx + gx
        cur = self.fixed_via_owner[idx]
        if cur == 0:
            self.fixed_via_owner[idx] = nc if nc > 0 else -1
        elif cur != nc:
            self.fixed_via_owner[idx] = -1

    def _rasterize_fixed_shape(self, layers: tuple[int, ...], sh: tuple,
                               nc: int, mark_via: bool = True):
        bb = _shape_bbox(sh)
        clr = max(self.safe_clear_nm, self.net_clear_nm.get(nc, 0))
        t_reach = self.track_w_nm // 2 + clr
        v_reach = self.via_r_nm + clr
        reach = max(t_reach, v_reach) if mark_via else t_reach
        gx0 = max(0, (bb[0] - reach - self.ox) // self.grid_nm)
        gx1 = min(self.nx - 1, (bb[2] + reach - self.ox) // self.grid_nm + 1)
        gy0 = max(0, (bb[1] - reach - self.oy) // self.grid_nm)
        gy1 = min(self.ny - 1, (bb[3] + reach - self.oy) // self.grid_nm + 1)
        k = sh[0]
        for gx in range(gx0, gx1 + 1):
            cx = self.ox + gx * self.grid_nm
            for gy in range(gy0, gy1 + 1):
                cy = self.oy + gy * self.grid_nm
                if k == 0:
                    num, den = _geom.pt_seg_d2((cx, cy), (sh[1], sh[2]), (sh[3], sh[4]))
                    hw = sh[5]
                    if not _dist_ok(num, den, hw + self.track_w_nm, clr):
                        for li in layers:
                            self._mark_fixed_cell(li, gx, gy, nc)
                    if mark_via and not _dist_ok(num, den, hw + self.via_diam_nm, clr):
                        self._mark_fixed_via_cell(gx, gy, nc)
                elif k == 1:
                    dx, dy = cx - sh[1], cy - sh[2]
                    d2 = dx * dx + dy * dy
                    rt = sh[3] + self.track_w_nm // 2 + clr
                    if d2 < rt * rt:
                        for li in layers:
                            self._mark_fixed_cell(li, gx, gy, nc)
                    if mark_via:
                        rv = sh[3] + self.via_r_nm + clr
                        if d2 < rv * rv:
                            self._mark_fixed_via_cell(gx, gy, nc)
                else:
                    num, den = _pt_poly_d2((cx, cy), sh[1])
                    if not _dist_ok(num, den, self.track_w_nm, clr):
                        for li in layers:
                            self._mark_fixed_cell(li, gx, gy, nc)
                    if mark_via and not _dist_ok(num, den, self.via_diam_nm, clr):
                        self._mark_fixed_via_cell(gx, gy, nc)

    def _rasterize_fixed_hole(self, cx: int, cy: int, drill_nm: int):
        r = (drill_nm + self.via_drill_nm) // 2 + self.hole_nm + 4_000
        r2 = r * r
        gx0 = max(0, (cx - r - self.ox) // self.grid_nm)
        gx1 = min(self.nx - 1, (cx + r - self.ox) // self.grid_nm + 1)
        gy0 = max(0, (cy - r - self.oy) // self.grid_nm)
        gy1 = min(self.ny - 1, (cy + r - self.oy) // self.grid_nm + 1)
        for gx in range(gx0, gx1 + 1):
            px = self.ox + gx * self.grid_nm
            for gy in range(gy0, gy1 + 1):
                py = self.oy + gy * self.grid_nm
                if (px - cx) ** 2 + (py - cy) ** 2 < r2:
                    self.fixed_via_owner[gy * self.nx + gx] = -1

    def _mark_board_edges(self):
        hw = self.track_w_nm // 2
        vr = self.via_r_nm
        need_t2 = (self.edge_nm + 4_000 + hw) ** 2 if self.edge_nm > 0 else 0
        need_v2 = (self.edge_nm + 4_000 + vr) ** 2 if self.edge_nm > 0 else 0
        out_tol2 = 1_500_000 * 1_500_000
        for gx in range(self.nx):
            cx = self.ox + gx * self.grid_nm
            for gy in range(self.ny):
                cy = self.oy + gy * self.grid_nm
                if not _geom.pt_in_poly((cx, cy), self.outline):
                    if self.edge_nm <= 0:
                        near_edge = any(
                            _geom.pt_seg_d2((cx, cy), a, b)[0] <= out_tol2 * _geom.pt_seg_d2((cx, cy), a, b)[1]
                            for a, b in self.exact.outline_edges
                        )
                        if near_edge:
                            continue
                    idx = gy * self.nx + gx
                    for li in range(self.nl):
                        self.fixed_owner[li][idx] = -1
                    self.fixed_via_owner[idx] = -1
                    continue
                if self.edge_nm <= 0:
                    continue
                for a, b in self.exact.outline_edges:
                    num, den = _geom.pt_seg_d2((cx, cy), a, b)
                    if num < need_v2 * den:
                        self.fixed_via_owner[gy * self.nx + gx] = -1
                    if num < need_t2 * den:
                        idx = gy * self.nx + gx
                        for li in range(self.nl):
                            self.fixed_owner[li][idx] = -1
                        break

    def _exact_open_net_codes(self) -> set[int]:
        """Exact geometric open-net detection before stripping (never relies on 1-hop conn)."""
        tmp = defaultdict(list)
        for fp in self.board.GetFootprints():
            for pad in fp.Pads():
                nc = int(pad.GetNetCode())
                if nc <= 0 or pad.GetAttribute() == _NPTH:
                    continue
                lays = tuple(self.lid_to_i[l] for l in self.lids if pad.IsOnLayer(l))
                if not lays:
                    continue
                sh = _pad_shape_nm(pad, self.lids[lays[0]])
                tmp[nc].append((len(tmp[nc]), lays, sh, True, {}))
        for t in self.board.GetTracks():
            nc = int(t.GetNetCode())
            if nc <= 0:
                continue
            if t.Type() == pcbnew.PCB_VIA_T:
                p = t.GetPosition()
                sh = (1, int(p.x), int(p.y), int(t.GetWidth(pcbnew.F_Cu)) // 2)
                tmp[nc].append((len(tmp[nc]), self.all_li, sh, False, {}))
            else:
                li = self.lid_to_i.get(t.GetLayer())
                if li is None:
                    continue
                s, e = t.GetStart(), t.GetEnd()
                sh = (0, int(s.x), int(s.y), int(e.x), int(e.y), int(t.GetWidth()))
                tmp[nc].append((len(tmp[nc]), (li,), sh, False, {}))
        open_ncs = set()
        for nc, items in tmp.items():
            if sum(1 for it in items if it[3]) >= 2:
                if len(_compute_net_islands(items)) >= 2:
                    open_ncs.add(nc)
        return open_ncs

    # ---- Phase 0: ingest board
    def ingest_board(self, target_nets: set[str] | None) -> list[str]:
        if self.mode == "full":
            for t in list(self.board.GetTracks()):
                self.board.Delete(t)
        elif self.mode == "rip_open":
            if target_nets:
                rip_ncs = {self.code_by_name[n] for n in target_nets if n in self.code_by_name}
            else:
                rip_ncs = self._exact_open_net_codes()
            for t in list(self.board.GetTracks()):
                if int(t.GetNetCode()) in rip_ncs:
                    self.board.Delete(t)

        self._mark_board_edges()

        bga_refs = {bg["ref"] for bg in self.bgas}
        net_items = defaultdict(list)
        anon_nc = -1000
        for fp in self.board.GetFootprints():
            ref = str(fp.GetReference())
            fp_pads = list(fp.Pads())
            if not fp_pads:
                continue
            fcx = sum(int(p.GetPosition().x) for p in fp_pads) // len(fp_pads)
            fcy = sum(int(p.GetPosition().y) for p in fp_pads) // len(fp_pads)
            xs_fp = [int(p.GetPosition().x) for p in fp_pads]
            ys_fp = [int(p.GetPosition().y) for p in fp_pads]
            span_x = max(xs_fp) - min(xs_fp)
            span_y = max(ys_fp) - min(ys_fp)
            for pad in fp_pads:
                nc = int(pad.GetNetCode())
                pos = pad.GetPosition()
                px, py = int(pos.x), int(pos.y)
                ds = pad.GetDrillSize()
                drill = int(max(ds.x, ds.y)) if (ds.x or ds.y) else -1
                is_npth = pad.GetAttribute() == _NPTH
                is_pth = pad.GetAttribute() == _PTH
                if is_npth:
                    if drill > 0:
                        self.exact.add_hole(px, py, drill, 0, is_pth=False, owner="fixed")
                        self._rasterize_fixed_hole(px, py, drill)
                    continue
                lays = tuple(self.lid_to_i[l] for l in self.lids if pad.IsOnLayer(l))
                if not lays:
                    continue
                sh = _pad_shape_nm(pad, self.lids[lays[0]])
                eff_nc = nc
                if eff_nc == 0:
                    anon_nc -= 1
                    eff_nc = anon_nc
                self.exact.add_item(eff_nc, lays, sh, owner="fixed")
                if drill > 0:
                    self.exact.add_hole(px, py, drill, eff_nc, is_pth=is_pth, owner="fixed")
                    self._rasterize_fixed_hole(px, py, drill)
                self._rasterize_fixed_shape(lays, sh, eff_nc, mark_via=True)
                if nc > 0:
                    dx, dy = px - fcx, py - fcy
                    bb = _shape_bbox(sh)
                    pw, ph = bb[2] - bb[0], bb[3] - bb[1]
                    if is_pth and len(fp_pads) >= 2:
                        if span_y > span_x * 2:
                            bcx = (self.bx0 + self.bx1) // 2
                            od = (1, 0) if px < bcx else (-1, 0)
                        elif span_x > span_y * 2:
                            bcy = (self.by0 + self.by1) // 2
                            od = (0, 1) if py < bcy else (0, -1)
                        else:
                            od = (1 if dx >= 0 else -1, 0) if abs(dx) >= abs(dy) else (0, 1 if dy >= 0 else -1)
                    elif pw > ph * 1.25 and abs(dx) > 50_000:
                        # Elongated horizontal LGA/SMD pad -> exit along X!
                        od = (1 if dx >= 0 else -1, 0)
                    elif ph > pw * 1.25 and abs(dy) > 50_000:
                        # Elongated vertical LGA/SMD pad -> exit along Y!
                        od = (0, 1 if dy >= 0 else -1)
                    else:
                        od = (1 if dx >= 0 else -1, 0) if abs(dx) >= abs(dy) else (0, 1 if dy >= 0 else -1)
                    idx = len(net_items[nc])
                    meta = {
                        "cx": px, "cy": py, "layers": lays, "ref": ref,
                        "is_pth": is_pth, "out_dir": od, "shape": sh,
                        "pad_num": str(pad.GetNumber()),
                        "is_bga": ref in bga_refs,
                    }
                    net_items[nc].append((idx, lays, sh, True, meta))

        for li, sh in _extract_graphics_nm(self.board.GetDrawings(), self.lid_to_i):
            anon_nc -= 1
            self.exact.add_item(anon_nc, (li,), sh, owner="fixed")
            self._rasterize_fixed_shape((li,), sh, anon_nc, mark_via=True)
        for fp in self.board.GetFootprints():
            for li, sh in _extract_graphics_nm(fp.GraphicalItems(), self.lid_to_i):
                anon_nc -= 1
                self.exact.add_item(anon_nc, (li,), sh, owner="fixed")
                self._rasterize_fixed_shape((li,), sh, anon_nc, mark_via=True)

        for t in self.board.GetTracks():
            nc = int(t.GetNetCode())
            eff_nc = nc if nc > 0 else (anon_nc - 1)
            if nc == 0:
                anon_nc -= 1
            if t.Type() == pcbnew.PCB_VIA_T:
                p = t.GetPosition()
                vx, vy = int(p.x), int(p.y)
                vr = int(t.GetWidth(pcbnew.F_Cu)) // 2
                vd = int(t.GetDrillValue())
                sh = (1, vx, vy, vr)
                self.exact.add_item(eff_nc, self.all_li, sh, owner="fixed")
                self.exact.add_hole(vx, vy, vd, eff_nc, is_pth=False, owner="fixed")
                self._rasterize_fixed_shape(self.all_li, sh, eff_nc, mark_via=True)
                self._rasterize_fixed_hole(vx, vy, vd)
                if nc > 0:
                    idx = len(net_items[nc])
                    net_items[nc].append((idx, self.all_li, sh, False,
                                          {"cx": vx, "cy": vy, "is_via": True}))
            else:
                li = self.lid_to_i.get(t.GetLayer())
                if li is None:
                    continue
                s, e = t.GetStart(), t.GetEnd()
                sh = (0, int(s.x), int(s.y), int(e.x), int(e.y), int(t.GetWidth()))
                self.exact.add_item(eff_nc, (li,), sh, owner="fixed")
                self._rasterize_fixed_shape((li,), sh, eff_nc, mark_via=True)
                if nc > 0:
                    idx = len(net_items[nc])
                    net_items[nc].append((idx, (li,), sh, False,
                                          {"x1": int(s.x), "y1": int(s.y),
                                           "x2": int(e.x), "y2": int(e.y),
                                           "is_via": False}))

        self.net_items_map = net_items

        open_names = []
        for nc, items in sorted(net_items.items()):
            if sum(1 for it in items if it[3]) < 2:
                continue
            nobj = self.net_by_code.get(nc)
            if not nobj:
                continue
            name = nobj.GetNetname()
            if target_nets and name not in target_nets:
                continue
            if len(_compute_net_islands(items)) >= 2:
                open_names.append(name)
        return open_names

    # ---- emit permanent copper (fanout) vs reversible net copper
    def _emit_seg_raw(self, x1: int, y1: int, x2: int, y2: int, w: int, li: int,
                      nc: int, owner: str, mark_fixed_grid: bool):
        if x1 == x2 and y1 == y2:
            return None
        t = pcbnew.PCB_TRACK(self.board)
        t.SetStart(pcbnew.VECTOR2I(int(x1), int(y1)))
        t.SetEnd(pcbnew.VECTOR2I(int(x2), int(y2)))
        t.SetWidth(int(w))
        t.SetLayer(self.lids[li])
        t.SetNet(self.net_by_code[nc])
        self.board.Add(t)
        sh = (0, x1, y1, x2, y2, w)
        self.exact.add_item(nc, (li,), sh, owner=owner)
        if mark_fixed_grid:
            self._rasterize_fixed_shape((li,), sh, nc, mark_via=True)
        idx = len(self.net_items_map[nc])
        item = (idx, (li,), sh, False,
                {"x1": x1, "y1": y1, "x2": x2, "y2": y2, "is_via": False, "owner": owner})
        self.net_items_map[nc].append(item)
        self.added_segs += 1
        return t

    def _emit_via_raw(self, cx: int, cy: int, nc: int, owner: str, mark_fixed_grid: bool):
        v = pcbnew.PCB_VIA(self.board)
        v.SetPosition(pcbnew.VECTOR2I(int(cx), int(cy)))
        v.SetWidth(int(self.via_diam_nm))
        v.SetDrill(int(self.via_drill_nm))
        v.SetNet(self.net_by_code[nc])
        self.board.Add(v)
        sh = (1, cx, cy, self.via_r_nm)
        self.exact.add_item(nc, self.all_li, sh, owner=owner)
        self.exact.add_hole(cx, cy, self.via_drill_nm, nc, is_pth=False, owner=owner)
        if mark_fixed_grid:
            self._rasterize_fixed_shape(self.all_li, sh, nc, mark_via=True)
            self._rasterize_fixed_hole(cx, cy, self.via_drill_nm)
        idx = len(self.net_items_map[nc])
        item = (idx, self.all_li, sh, False,
                {"cx": cx, "cy": cy, "is_via": True, "owner": owner})
        self.net_items_map[nc].append(item)
        self.added_vias += 1
        return v

    def _emit_fixed_seg(self, x1: int, y1: int, x2: int, y2: int, w: int, li: int, nc: int) -> bool:
        if x1 == x2 and y1 == y2:
            return True
        ok, _ = self.exact.seg_clear(x1, y1, x2, y2, w, li, nc)
        if not ok:
            return False
        self._emit_seg_raw(x1, y1, x2, y2, w, li, nc, owner="fixed", mark_fixed_grid=True)
        return True

    def _emit_fixed_via(self, cx: int, cy: int, nc: int) -> bool:
        ok, _ = self.exact.via_clear(cx, cy, self.via_r_nm, self.via_drill_nm, nc)
        if not ok:
            return False
        self._emit_via_raw(cx, cy, nc, owner="fixed", mark_fixed_grid=True)
        return True

    # ---- Phase 1: BGA fanout (MCMF dogbone OR smart VIPPO + power bridges)
    def run_bga_fanout(self, open_names: list[str]) -> None:
        if not self.bgas or not open_names:
            return
        open_set = set(open_names)
        for bg in self.bgas:
            pitch = bg["pitch"]
            pad_d = bg["pad_d"]
            diag = pitch / math.sqrt(2.0)
            g_dog = diag - pad_d / 2.0 - self.via_r_nm
            g_ch = (pitch - pad_d - self.track_w_nm) / 2.0
            dogbone_ok = (g_dog >= self.safe_clear_nm and g_ch >= self.safe_clear_nm)
            vippo_allowed = (self.allow_vippo is True or
                             (self.allow_vippo == "auto" and not dogbone_ok))
            self.bga_dogbone_ok = dogbone_ok
            self.bga_vippo_allowed = vippo_allowed
            if dogbone_ok:
                self._bga_mcmf_dogbone(bg, open_set)
            elif vippo_allowed:
                self._bga_smart_vippo(bg, open_set)

    def _bga_mcmf_dogbone(self, bg: dict, open_set: set[str]) -> None:
        try:
            import bga_escape_solver as bes
        except Exception:
            return
        rules = bes.Rules(
            track_w=self.track_w_nm / MM,
            clearance=self.safe_clear_nm / MM,
            via_pad=self.via_diam_nm / MM,
            via_drill=self.via_drill_nm / MM,
            hole_to_hole=(self.hole_nm + 4_000) / MM,
            power_stub_w=max(self.track_w_nm / MM, 0.13),
        )
        n_lower = max(1, self.nl - 1 - len(self.zone_layers - {self.f_i}))
        rep = bes.run(str(self.pcb_path), ref=bg["ref"], n_lower=n_lower,
                      mode="any", rules=rules, with_copper=True)
        cu = rep.get("copper") or {}
        other_layers = [i for i in range(self.nl) if i != self.f_i and i not in self.zone_layers]
        other_layers += [i for i in range(self.nl) if i != self.f_i and i in self.zone_layers]
        layer_map = {"F.Cu": self.f_i}
        for k, li in enumerate(other_layers):
            layer_map[f"ESC{k + 1}"] = li
        for v in cu.get("vias", []):
            net = v["net"]
            if net not in open_set or net not in self.code_by_name:
                continue
            nc = self.code_by_name[net]
            if self._emit_fixed_via(int(round(v["at"][0] * MM)), int(round(v["at"][1] * MM)), nc):
                self.fanout_vias += 1
        for tr in cu.get("tracks", []):
            net = tr["net"]
            if net not in open_set or net not in self.code_by_name:
                continue
            nc = self.code_by_name[net]
            li = layer_map.get(tr["layer"], self.f_i)
            w = max(self.track_w_nm, int(round(tr["width"] * MM)))
            if self._emit_fixed_seg(int(round(tr["a"][0] * MM)), int(round(tr["a"][1] * MM)),
                                    int(round(tr["b"][0] * MM)), int(round(tr["b"][1] * MM)),
                                    w, li, nc):
                self.fanout_segs += 1

    def _bga_smart_vippo(self, bg: dict, open_set: set[str]) -> None:
        fp = bg["fp"]
        pitch = bg["pitch"]
        x0, y0, nx, ny = bg["x0"], bg["y0"], bg["nx"], bg["ny"]
        grid_balls = {}
        for pad in fp.Pads():
            nc = int(pad.GetNetCode())
            if nc <= 0:
                continue
            nobj = self.net_by_code.get(nc)
            if not nobj:
                continue
            name = nobj.GetNetname()
            if name not in open_set:
                continue
            pos = pad.GetPosition()
            px, py = int(pos.x), int(pos.y)
            i = int(round((px - x0) / pitch))
            j = int(round((py - y0) / pitch))
            grid_balls[(i, j)] = (px, py, nc, name)

        # 1. Bridge orthogonally adjacent same-net balls on F.Cu (merges power islands!)
        for (i, j), (px, py, nc, name) in sorted(grid_balls.items()):
            for di, dj in ((1, 0), (0, 1)):
                nb = grid_balls.get((i + di, j + dj))
                if nb and nb[2] == nc:
                    if self._emit_fixed_seg(px, py, nb[0], nb[1], self.track_w_nm, self.f_i, nc):
                        self.fanout_segs += 1

        def cluster_has_via(nc, bx, by):
            for it in self.net_items_map.get(nc, ()):
                if not it[3] and it[4].get("is_via"):
                    if abs(it[4]["cx"] - bx) <= pitch and abs(it[4]["cy"] - by) <= pitch:
                        return True
            return False

        ball_uf = {k: k for k in grid_balls}

        def bfind(k):
            while ball_uf[k] != k:
                ball_uf[k] = ball_uf[ball_uf[k]]
                k = ball_uf[k]
            return k

        for (i, j), (_, _, nc, _) in grid_balls.items():
            for di, dj in ((1, 0), (0, 1)):
                nb = (i + di, j + dj)
                if nb in grid_balls and grid_balls[nb][2] == nc:
                    ra, rb = bfind((i, j)), bfind(nb)
                    if ra != rb:
                        ball_uf[ra] = rb

        bclusters = defaultdict(list)
        for ij in grid_balls:
            bclusters[bfind(ij)].append(ij)

        for _, members in sorted(bclusters.items(), key=lambda kv: len(kv[1])):
            px0, py0, nc, name = grid_balls[members[0]]
            is_power = any(h in name.upper() for h in ("GND", "3V3", "5V", "VDD", "VSS", "VCAP", "VDDA"))
            if len(members) == 1:
                i, j = members[0]
                ring = min(i, j, nx - 1 - i, ny - 1 - j)
                if ring == 0 and not is_power:
                    dirs = []
                    if j == 0: dirs.append((0, -1))
                    if j == ny - 1: dirs.append((0, 1))
                    if i == 0: dirs.append((-1, 0))
                    if i == nx - 1: dirs.append((1, 0))
                    escaped_f = False
                    for dx, dy in dirs:
                        ex = px0 + dx * pitch
                        ey = py0 + dy * pitch
                        if self._emit_fixed_seg(px0, py0, ex, ey, self.track_w_nm, self.f_i, nc):
                            self.fanout_segs += 1
                            escaped_f = True
                            break
                    if escaped_f:
                        continue
                # Interior balls OR single-ball power balls -> VIPPO!
                if not cluster_has_via(nc, px0, py0):
                    if self._emit_fixed_via(px0, py0, nc):
                        self.fanout_vias += 1
            else:
                chosen = []
                ordered = sorted(members, key=lambda ij: (
                    min(ij[0], ij[1], nx - 1 - ij[0], ny - 1 - ij[1]), ij))
                for ij in ordered:
                    bx, by, _, _ = grid_balls[ij]
                    if any(math.hypot(bx - cx, by - cy) < pitch * 3 for cx, cy in chosen):
                        continue
                    if self._emit_fixed_via(bx, by, nc):
                        self.fanout_vias += 1
                        chosen.append((bx, by))
                        if len(chosen) >= (3 if len(members) >= 12 else 2):
                            break

    # ---- Pad/Island Grid Access Ports (Continuous -> Grid Bridge, LGA-aware!)
    def _island_ports(self, nc: int, items: list, idxs: list[int],
                      ignore_owner: str | None = None) -> dict[tuple[int, int, int], tuple[int, int]]:
        """Map reachable grid nodes (li, gx, gy) -> exact continuous anchor (ax, ay).

        Crucial for 0.50 mm pitch LGA pads (U2..U6, J10): cells 0.2-0.4 mm from
        the pad sit inside adjacent pads' fixed_owner bloat, so we project straight
        outward up to 2.0 mm along cardinal/diagonal escape rays and verify every
        continuous bridge segment with ExactBoard!
        """
        ports = {}
        for idx in idxs:
            _, lays, sh, is_pad, meta = items[idx]
            if is_pad:
                cx, cy = meta["cx"], meta["cy"]
                gx0, gy0 = self.xy_cell(cx, cy)
                ox_d, oy_d = meta["out_dir"]

                # Ordered escape directions: primary outward normal first, then other cardinals
                cardinals = [
                    (ox_d, oy_d),
                    (-oy_d, ox_d),
                    (oy_d, -ox_d),
                    (-ox_d, -oy_d),
                ]
                diags = [(1, 1), (1, -1), (-1, 1), (-1, -1)]

                # Build candidate cells:
                # 1) Exact pad center cell
                # 2) Long outward rays (k=1..10 -> up to 2.0 mm) to escape fine-pitch pad bloat!
                # 3) Local 3x3 ring
                cands = [(gx0, gy0)]
                for dx, dy in cardinals:
                    for k in range(1, 11):
                        cands.append((gx0 + dx * k, gy0 + dy * k))
                for dx, dy in diags:
                    for k in range(1, 7):
                        cands.append((gx0 + dx * k, gy0 + dy * k))
                # Also try +/-1 perpendicular offset along primary ray (snaps off-grid 0.5mm pads!)
                px_d, py_d = -oy_d, ox_d
                for k in range(2, 9):
                    for s in (-1, 1):
                        cands.append((gx0 + ox_d * k + px_d * s, gy0 + oy_d * k + py_d * s))

                for li in lays:
                    escaped_cnt = 0
                    inner_cnt = 0
                    max_esc = 3 if meta["is_pth"] else 5
                    for gx, gy in cands:
                        if gx < 1 or gy < 1 or gx >= self.nx - 1 or gy >= self.ny - 1:
                            continue
                        key = (li, gx, gy)
                        if key in ports:
                            continue
                        fo = self.fixed_owner[li][gy * self.nx + gx]
                        if fo != 0 and fo != nc:
                            continue
                        is_escaped = (fo == 0)
                        if not is_escaped and inner_cnt >= 2:
                            continue
                        tx, ty = self.cell_xy(gx, gy)
                        if tx == cx and ty == cy:
                            ports[key] = (cx, cy)
                            if is_escaped:
                                escaped_cnt += 1
                            else:
                                inner_cnt += 1
                        else:
                            ok, _ = self.exact.seg_clear(cx, cy, tx, ty,
                                                         self.track_w_nm, li, nc,
                                                         ignore_owner=ignore_owner)
                            if ok:
                                ports[key] = (cx, cy)
                                if is_escaped:
                                    escaped_cnt += 1
                                else:
                                    inner_cnt += 1
                        if escaped_cnt >= max_esc:
                            break
            else:
                if meta.get("is_via"):
                    cx, cy = meta["cx"], meta["cy"]
                    gx, gy = self.xy_cell(cx, cy)
                    if 1 <= gx < self.nx - 1 and 1 <= gy < self.ny - 1:
                        tx, ty = self.cell_xy(gx, gy)
                        for li in lays:
                            fo = self.fixed_owner[li][gy * self.nx + gx]
                            if fo == 0 or fo == nc:
                                if tx == cx and ty == cy:
                                    ports[(li, gx, gy)] = (cx, cy)
                                else:
                                    ok, _ = self.exact.seg_clear(cx, cy, tx, ty,
                                                                 self.track_w_nm, li, nc,
                                                                 ignore_owner=ignore_owner)
                                    if ok:
                                        ports[(li, gx, gy)] = (cx, cy)
                else:
                    li = lays[0]
                    for ax, ay in ((meta["x1"], meta["y1"]), (meta["x2"], meta["y2"])):
                        gx, gy = self.xy_cell(ax, ay)
                        if 1 <= gx < self.nx - 1 and 1 <= gy < self.ny - 1:
                            fo = self.fixed_owner[li][gy * self.nx + gx]
                            if fo == 0 or fo == nc:
                                tx, ty = self.cell_xy(gx, gy)
                                if tx == ax and ty == ay:
                                    ports.setdefault((li, gx, gy), (ax, ay))
                                else:
                                    ok, _ = self.exact.seg_clear(ax, ay, tx, ty,
                                                                 self.track_w_nm, li, nc,
                                                                 ignore_owner=ignore_owner)
                                    if ok:
                                        ports.setdefault((li, gx, gy), (ax, ay))
        return ports

    # ---- PathFinder occupancy bookkeeping
    def _compute_route_footprint(self, track_cells, via_cells):
        nx, ny = self.nx, self.ny
        t_halo = self.track_halo
        vt_halo = self.via_track_halo
        vv_halo = self.via_via_halo
        touched_t = set()
        for li, gx, gy in track_cells:
            for dx, dy in t_halo:
                x2, y2 = gx + dx, gy + dy
                if 0 <= x2 < nx and 0 <= y2 < ny:
                    touched_t.add((li, y2 * nx + x2))
        touched_v = set()
        for gx, gy in via_cells:
            for dx, dy in vt_halo:
                x2, y2 = gx + dx, gy + dy
                if 0 <= x2 < nx and 0 <= y2 < ny:
                    idx = y2 * nx + x2
                    for li in range(self.nl):
                        touched_t.add((li, idx))
            for dx, dy in vv_halo:
                x2, y2 = gx + dx, gy + dy
                if 0 <= x2 < nx and 0 <= y2 < ny:
                    touched_v.add(y2 * nx + x2)
        return touched_t, touched_v

    def _apply_footprint(self, touched_t, touched_v, delta: int) -> None:
        occ = self.occ
        vocc = self.via_occ
        for li, idx in touched_t:
            occ[li][idx] += delta
        for idx in touched_v:
            vocc[idx] += delta

    def _apply_route_occ(self, route: dict, delta: int) -> None:
        if "_touched_t" not in route:
            tt, tv = self._compute_route_footprint(route["track_cells"], route["via_cells"])
            route["_touched_t"] = tt
            route["_touched_v"] = tv
        self._apply_footprint(route["_touched_t"], route["_touched_v"], delta)

    def _route_conflicts(self, route: dict) -> int:
        occ = self.occ
        vocc = self.via_occ
        c = 0
        for li, idx in route.get("_touched_t", ()):
            if occ[li][idx] > 1:
                c += 1
        for idx in route.get("_touched_v", ()):
            if vocc[idx] > 1:
                c += 1
        return c

    # ---- Multi-Source / Multi-Target 6-Layer A*
    def _astar_connect(self, nc: int, src_nodes: list[tuple[int, int, int]],
                       goal_set: set[tuple[int, int, int]],
                       pfac: float, strict: bool = False,
                       extra_blocked: set | None = None,
                       max_expansions: int = 120_000):
        if not src_nodes or not goal_set:
            return None
        gxs = [g[1] for g in goal_set]
        gys = [g[2] for g in goal_set]
        gx_min, gx_max = min(gxs), max(gxs)
        gy_min, gy_max = min(gys), max(gys)

        nx, ny, nl = self.nx, self.ny, self.nl
        f_own = self.fixed_owner
        fv_own = self.fixed_via_owner
        occ = self.occ
        hist = self.hist
        vocc = self.via_occ
        vhist = self.via_hist
        pref_h = self.pref_h
        zone_layers = self.zone_layers

        VIA_BASE = 8.0
        ZONE_PEN = 25.0
        H_WEIGHT = 1.15

        def heur(gx: int, gy: int) -> float:
            dx = gx_min - gx if gx < gx_min else (gx - gx_max if gx > gx_max else 0)
            dy = gy_min - gy if gy < gy_min else (gy - gy_max if gy > gy_max else 0)
            if dx < dy:
                return H_WEIGHT * (dy + 0.4142 * dx)
            return H_WEIGHT * (dx + 0.4142 * dy)

        heap = []
        gscore = {}
        came = {}
        cnt = 0
        for s in src_nodes:
            if s in goal_set:
                return [s]
            gscore[s] = 0.0
            cnt += 1
            heapq.heappush(heap, (heur(s[1], s[2]), cnt, s))

        steps = ((1, 0, 1.0, True), (-1, 0, 1.0, True),
                 (0, 1, 1.0, False), (0, -1, 1.0, False),
                 (1, 1, 1.4142, None), (1, -1, 1.4142, None),
                 (-1, 1, 1.4142, None), (-1, -1, 1.4142, None))

        expansions = 0
        found = None
        while heap:
            _, _, u = heapq.heappop(heap)
            if u in goal_set:
                found = u
                break
            cur_g = gscore[u]
            expansions += 1
            if expansions > max_expansions:
                break
            li, gx, gy = u
            arr_f = f_own[li]
            arr_o = occ[li]
            arr_h = hist[li]
            is_h_pref = pref_h[li]
            z_add = ZONE_PEN if li in zone_layers else 0.0

            for dx, dy, base_d, is_horiz in steps:
                x2, y2 = gx + dx, gy + dy
                if x2 < 1 or y2 < 1 or x2 >= nx - 1 or y2 >= ny - 1:
                    continue
                idx2 = y2 * nx + x2
                fo = arr_f[idx2]
                v = (li, x2, y2)
                if fo != 0 and fo != nc and v not in goal_set:
                    continue
                if extra_blocked and v in extra_blocked and v not in goal_set:
                    continue
                o2 = arr_o[idx2]
                if strict and o2 > 0 and v not in goal_set:
                    continue
                diag_pen = 0.0
                if dx and dy:
                    fo_a = arr_f[gy * nx + x2]
                    fo_b = arr_f[y2 * nx + gx]
                    if (fo_a != 0 and fo_a != nc) and (fo_b != 0 and fo_b != nc):
                        if li == self.f_i:
                            continue
                        diag_pen = 0.5
                    elif strict and (arr_o[gy * nx + x2] > 0 or arr_o[y2 * nx + gx] > 0):
                        continue
                d_mult = 1.0 if is_horiz is None else (0.92 if is_horiz == is_h_pref else 1.08)
                step_cost = (base_d * d_mult + diag_pen + z_add * 0.05) * (1.0 + arr_h[idx2]) * (1.0 + pfac * o2)
                ng = cur_g + step_cost
                if ng < gscore.get(v, 1e18):
                    gscore[v] = ng
                    came[v] = u
                    cnt += 1
                    heapq.heappush(heap, (ng + heur(x2, y2), cnt, v))

            idx = gy * nx + gx
            fvo = fv_own[idx]
            if fvo == 0 or fvo == nc:
                vo = vocc[idx]
                if not (strict and vo > 0):
                    v_cost = VIA_BASE * (1.0 + vhist[idx]) * (1.0 + pfac * vo)
                    for li2 in range(nl):
                        if li2 == li:
                            continue
                        v = (li2, gx, gy)
                        fo2 = f_own[li2][idx]
                        if fo2 != 0 and fo2 != nc and v not in goal_set:
                            continue
                        if extra_blocked and v in extra_blocked and v not in goal_set:
                            continue
                        if strict and occ[li2][idx] > 0 and v not in goal_set:
                            continue
                        ng = cur_g + v_cost + (ZONE_PEN if li2 in zone_layers else 0.0)
                        if ng < gscore.get(v, 1e18):
                            gscore[v] = ng
                            came[v] = u
                            cnt += 1
                            heapq.heappush(heap, (ng + heur(gx, gy), cnt, v))

        if found is None:
            return None
        path = []
        cur = found
        while cur in came:
            path.append(cur)
            cur = came[cur]
        path.append(cur)
        path.reverse()
        return path

    def _route_net_islands(self, nc: int, island_ports_list: list[dict],
                           pfac: float, strict: bool = False,
                           extra_blocked: set | None = None):
        if len(island_ports_list) < 2:
            return {"chains": [], "track_cells": set(), "via_cells": set(),
                    "complete": True}
        order = sorted(range(len(island_ports_list)),
                       key=lambda i: -len(island_ports_list[i]))
        tree_nodes = set(island_ports_list[order[0]].keys())
        tree_port_anchors = dict(island_ports_list[order[0]])
        remaining = [i for i in order[1:]]

        def centroid(pdict):
            if not pdict:
                return (0, 0)
            return (sum(k[1] for k in pdict) / len(pdict),
                    sum(k[2] for k in pdict) / len(pdict))

        cents = {i: centroid(island_ports_list[i]) for i in range(len(island_ports_list))}
        chains = []
        track_cells = set()
        via_cells = set()
        complete = True

        while remaining:
            best_i = None
            best_d = 1e18
            t_sample = list(tree_nodes)
            if len(t_sample) > 40:
                t_sample = t_sample[::max(1, len(t_sample) // 40)]
            for i in remaining:
                cx, cy = cents[i]
                d = min(abs(n[1] - cx) + abs(n[2] - cy) for n in t_sample)
                if d < best_d:
                    best_d = d
                    best_i = i
            remaining.remove(best_i)
            src_dict = island_ports_list[best_i]
            if not src_dict:
                complete = False
                continue
            path = self._astar_connect(
                nc, list(src_dict.keys()), tree_nodes,
                pfac=pfac, strict=strict, extra_blocked=extra_blocked)
            if path is None:
                complete = False
                continue
            start_anchor = src_dict.get(path[0])
            end_anchor = tree_port_anchors.get(path[-1])
            chains.append((path, start_anchor, end_anchor))
            for k, node in enumerate(path):
                track_cells.add(node)
                tree_nodes.add(node)
                if k + 1 < len(path) and path[k + 1][0] != node[0]:
                    via_cells.add((node[1], node[2]))
            tree_nodes.update(src_dict.keys())
            tree_port_anchors.update(src_dict)

        return {"chains": chains, "track_cells": track_cells,
                "via_cells": via_cells, "complete": complete}

    def _chains_to_geometry(self, chains):
        segs = []
        vias = set()
        track_cells = set()
        for path, start_anchor, end_anchor in chains:
            if not path:
                continue
            for node in path:
                track_cells.add(node)
            s0 = path[0]
            p0 = self.cell_xy(s0[1], s0[2])
            if start_anchor is not None and start_anchor != p0:
                segs.append((s0[0], start_anchor[0], start_anchor[1], p0[0], p0[1]))
            runs = []
            cur = [path[0]]
            for node in path[1:]:
                if node[0] != cur[-1][0]:
                    runs.append(cur)
                    vias.add((cur[-1][1], cur[-1][2]))
                    cur = [node]
                else:
                    cur.append(node)
            runs.append(cur)
            for run in runs:
                li = run[0][0]
                k = 0
                while k < len(run) - 1:
                    dx = run[k + 1][1] - run[k][1]
                    dy = run[k + 1][2] - run[k][2]
                    j = k + 1
                    while j < len(run) - 1:
                        if (run[j + 1][1] - run[j][1] == dx and
                                run[j + 1][2] - run[j][2] == dy):
                            j += 1
                        else:
                            break
                    a = self.cell_xy(run[k][1], run[k][2])
                    b = self.cell_xy(run[j][1], run[j][2])
                    segs.append((li, a[0], a[1], b[0], b[1]))
                    k = j
            se = path[-1]
            pe = self.cell_xy(se[1], se[2])
            if end_anchor is not None and end_anchor != pe:
                segs.append((se[0], pe[0], pe[1], end_anchor[0], end_anchor[1]))
        via_pts = [self.cell_xy(gx, gy) for gx, gy in sorted(vias)]
        return segs, via_pts, track_cells, vias

    # ---- Reversible net commit / uncommit (supports Phase 4 rip-and-swap!)
    def _commit_net_reversible(self, nm: str, nc: int, chains, append: bool = False) -> bool:
        segs, via_pts, track_cells, via_cells = self._chains_to_geometry(chains)
        if not segs and not via_pts:
            return False
        owner = f"net:{nm}"
        for vx, vy in via_pts:
            ok, _ = self.exact.via_clear(vx, vy, self.via_r_nm, self.via_drill_nm,
                                         nc, ignore_owner=owner)
            if not ok:
                return False
        for li, x1, y1, x2, y2 in segs:
            if x1 == x2 and y1 == y2:
                continue
            ok, _ = self.exact.seg_clear(x1, y1, x2, y2, self.track_w_nm, li,
                                         nc, ignore_owner=owner)
            if not ok:
                return False
        objs = []
        for vx, vy in via_pts:
            objs.append(self._emit_via_raw(vx, vy, nc, owner=owner, mark_fixed_grid=False))
        for li, x1, y1, x2, y2 in segs:
            if x1 == x2 and y1 == y2:
                continue
            obj = self._emit_seg_raw(x1, y1, x2, y2, self.track_w_nm, li, nc,
                                     owner=owner, mark_fixed_grid=False)
            if obj is not None:
                objs.append(obj)
        tt, tv = self._compute_route_footprint(track_cells, via_cells)
        self._apply_footprint(tt, tv, +1)
        if append and nm in self.committed_records:
            rec = self.committed_records[nm]
            rec["objs"].extend(objs)
            rec["segs"].extend(segs)
            rec["via_pts"].extend(via_pts)
            rec["touched_t_list"].append(tt)
            rec["touched_v_list"].append(tv)
        else:
            self.committed_records[nm] = {
                "nc": nc, "objs": objs, "segs": list(segs), "via_pts": list(via_pts),
                "touched_t_list": [tt], "touched_v_list": [tv],
            }
        return True

    def _uncommit_net(self, nm: str) -> dict | None:
        rec = self.committed_records.pop(nm, None)
        if rec is None:
            return None
        nc = rec["nc"]
        owner = f"net:{nm}"
        for tt, tv in zip(rec["touched_t_list"], rec["touched_v_list"]):
            self._apply_footprint(tt, tv, -1)
        self.exact.remove_owner(owner)
        for obj in rec["objs"]:
            self.board.Delete(obj)
        self.net_items_map[nc] = [
            it for it in self.net_items_map[nc] if it[4].get("owner") != owner]
        return rec

    def _restore_net(self, nm: str, rec: dict) -> None:
        nc = rec["nc"]
        owner = f"net:{nm}"
        new_objs = []
        for vx, vy in rec["via_pts"]:
            new_objs.append(self._emit_via_raw(vx, vy, nc, owner=owner, mark_fixed_grid=False))
        for li, x1, y1, x2, y2 in rec["segs"]:
            if x1 == x2 and y1 == y2:
                continue
            obj = self._emit_seg_raw(x1, y1, x2, y2, self.track_w_nm, li, nc,
                                     owner=owner, mark_fixed_grid=False)
            if obj is not None:
                new_objs.append(obj)
        for tt, tv in zip(rec["touched_t_list"], rec["touched_v_list"]):
            self._apply_footprint(tt, tv, +1)
        rec["objs"] = new_objs
        self.committed_records[nm] = rec

    # ---- Strict clean A* with exact-conflict cell blocking
    def _strict_route_net(self, nm: str, nc: int, max_retries: int = 3,
                          append: bool = False) -> bool:
        items = self.net_items_map.get(nc, [])
        isls = _compute_net_islands(items)
        if len(isls) < 2:
            return True
        owner = f"net:{nm}" if append else None
        ports_list = [self._island_ports(nc, items, isl, ignore_owner=owner) for isl in isls]
        valid_ports = [p for p in ports_list if p]
        if len(valid_ports) < 2:
            return False
        extra_blocked = set()
        for _ in range(max_retries):
            r = self._route_net_islands(nc, valid_ports, pfac=0.0, strict=True,
                                        extra_blocked=extra_blocked)
            if not r["chains"]:
                return False
            if self._commit_net_reversible(nm, nc, r["chains"], append=append):
                return len(_compute_net_islands(self.net_items_map[nc])) < 2
            added_block = False
            for path, sa, ea in r["chains"]:
                for k in range(len(path) - 1):
                    a, b = path[k], path[k + 1]
                    if a[0] != b[0]:
                        vx, vy = self.cell_xy(a[1], a[2])
                        ok, _ = self.exact.via_clear(vx, vy, self.via_r_nm,
                                                     self.via_drill_nm, nc,
                                                     ignore_owner=owner)
                        if not ok:
                            extra_blocked.add(a); extra_blocked.add(b)
                            added_block = True
                    else:
                        p1, p2 = self.cell_xy(a[1], a[2]), self.cell_xy(b[1], b[2])
                        ok, _ = self.exact.seg_clear(p1[0], p1[1], p2[0], p2[1],
                                                     self.track_w_nm, a[0], nc,
                                                     ignore_owner=owner)
                        if not ok:
                            extra_blocked.add(a); extra_blocked.add(b)
                            added_block = True
            if not added_block:
                break
        return False

    def _rescue_island_via(self, nm: str, nc: int) -> bool:
        """If an open island sits on a single-layer BGA/SMD pad without a via,
        try placing a legal VIPPO (for BGA when allowed), outward fanout via,
        or pocket-BFS escape via inside the island's reachable single-layer area."""
        items = self.net_items_map.get(nc, [])
        isls = _compute_net_islands(items)
        if len(isls) < 2:
            return False
        placed = False
        for isl in isls:
            if self._rescue_single_island_via(nc, self.net_items_map[nc], isl):
                placed = True
        return placed

    def _phase4_rip_and_swap(self, nm: str, nc: int, max_blockers: int = 5) -> bool:
        """Bounded local blocker eviction:
        Find 1-5 committed nets blocking `nm`, atomically evict them, route `nm`,
        then re-route every evicted net cleanly. Rolls back untouched if any fails."""
        items = self.net_items_map.get(nc, [])
        isls = _compute_net_islands(items)
        if len(isls) < 2:
            return True
        owner = f"net:{nm}"
        ports_list = [self._island_ports(nc, items, isl, ignore_owner=owner) for isl in isls]
        valid_ports = [p for p in ports_list if p]
        if len(valid_ports) < 2:
            return False

        # Soft A* across committed occ (pfac=40) to find minimal-blocker path
        r = self._route_net_islands(nc, valid_ports, pfac=40.0, strict=False)
        if not r["chains"]:
            return False
        segs, via_pts, _, _ = self._chains_to_geometry(r["chains"])
        blockers = set()
        hard_blocked = False
        for vx, vy in via_pts:
            for b in self.exact.via_blockers(vx, vy, self.via_r_nm, self.via_drill_nm,
                                             nc, ignore_owner=owner, find_all=True):
                if b.startswith("net:"):
                    blockers.add(b[4:])
                else:
                    hard_blocked = True
        for li, x1, y1, x2, y2 in segs:
            if x1 == x2 and y1 == y2:
                continue
            for b in self.exact.seg_blockers(x1, y1, x2, y2, self.track_w_nm, li,
                                             nc, ignore_owner=owner, find_all=True):
                if b.startswith("net:"):
                    blockers.add(b[4:])
                else:
                    hard_blocked = True
        blockers.discard(nm)
        if hard_blocked or not blockers or len(blockers) > max_blockers:
            return False
        # Do not evict giant power nets (>15 pads) for a 2-pin signal swap
        nm_pads = sum(1 for it in items if it[3])
        for bnm in list(blockers):
            if bnm not in self.committed_records:
                return False
            b_nc = self.committed_records[bnm]["nc"]
            b_pads = sum(1 for it in self.net_items_map.get(b_nc, ()) if it[3])
            if b_pads > 15 and nm_pads <= 4:
                return False

        # Atomically evict blockers
        evicted = {}
        for bnm in sorted(blockers):
            evicted[bnm] = self._uncommit_net(bnm)

        # Commit `nm` cleanly (try direct candidate first, else strict A*)
        nm_had_prev = nm in self.committed_records
        prev_nm_rec = self._uncommit_net(nm) if nm_had_prev else None
        ok_nm = self._commit_net_reversible(nm, nc, r["chains"])
        if not ok_nm:
            ok_nm = self._strict_route_net(nm, nc, max_retries=2)
        if ok_nm:
            ok_nm = len(_compute_net_islands(self.net_items_map[nc])) < 2

        re_routed = []
        all_ok = ok_nm
        if all_ok:
            for bnm in sorted(evicted.keys()):
                b_nc = evicted[bnm]["nc"]
                if not self._strict_route_net(bnm, b_nc, max_retries=3):
                    all_ok = False
                    break
                re_routed.append(bnm)

        if all_ok:
            self.swaps_ok += 1
            return True

        # Rollback completely!
        for bnm in re_routed:
            self._uncommit_net(bnm)
        self._uncommit_net(nm)
        if prev_nm_rec is not None:
            self._restore_net(nm, prev_nm_rec)
        for bnm, rec in evicted.items():
            if rec is not None:
                self._restore_net(bnm, rec)
        return False

    def _rescue_single_island_via(self, nc: int, items: list, isl: list[int]) -> bool:
        """Place a clean fanout via (VIPPO if allowed on BGA, outward stub+via,
        or pocket-BFS via inside the island's reachable single-layer corridor)."""
        if any(len(items[i][1]) > 1 for i in isl):
            return False
        for i in isl:
            if not items[i][3]:
                continue
            meta = items[i][4]
            cx, cy = meta["cx"], meta["cy"]
            if meta.get("is_bga") and self.bga_vippo_allowed:
                if self._emit_fixed_via(cx, cy, nc):
                    self.fanout_vias += 1
                    return True
            ox_d, oy_d = meta["out_dir"]
            dirs = [(ox_d, oy_d), (-oy_d, ox_d), (oy_d, -ox_d), (-ox_d, -oy_d),
                    (1, 1), (1, -1), (-1, 1), (-1, -1)]
            for dist_um in (600_000, 800_000, 1_000_000, 1_200_000, 1_500_000):
                for dx, dy in dirs:
                    norm = math.hypot(dx, dy)
                    vx = int(round(cx + dx / norm * dist_um))
                    vy = int(round(cy + dy / norm * dist_um))
                    ok_v, _ = self.exact.via_clear(vx, vy, self.via_r_nm, self.via_drill_nm, nc)
                    if not ok_v:
                        continue
                    ok_s, _ = self.exact.seg_clear(cx, cy, vx, vy, self.track_w_nm, meta["layers"][0], nc)
                    if not ok_s:
                        continue
                    if self._emit_fixed_via(vx, vy, nc):
                        self._emit_fixed_seg(cx, cy, vx, vy, self.track_w_nm, meta["layers"][0], nc)
                        self.fanout_vias += 1
                        self.fanout_segs += 1
                        return True

        # Fallback: Pocket-BFS inside the island's reachable single-layer area!
        # Handles ring-0 BGA balls escaped on F.Cu that got boxed in on F.Cu
        # before dropping a via to inner layers.
        ports = self._island_ports(nc, items, isl)
        if not ports:
            return False
        from collections import deque
        q = deque()
        parent = {}
        root_anchor = {}
        for node, anc in ports.items():
            q.append(node)
            parent[node] = None
            root_anchor[node] = anc
        visited_cnt = 0
        while q and visited_cnt < 160:
            node = q.popleft()
            visited_cnt += 1
            li, gx, gy = node
            vx, vy = self.cell_xy(gx, gy)
            ok_v, _ = self.exact.via_clear(vx, vy, self.via_r_nm, self.via_drill_nm, nc)
            if ok_v:
                # Reconstruct grid path from starting port to (li, gx, gy)
                path = []
                cur = node
                while cur is not None:
                    path.append(cur)
                    cur = parent[cur]
                path.reverse()
                anc = root_anchor[path[0]]
                segs, _, _, _ = self._chains_to_geometry([(path, anc, None)])
                all_s_ok = True
                for sli, x1, y1, x2, y2 in segs:
                    if x1 == x2 and y1 == y2:
                        continue
                    ok_s, _ = self.exact.seg_clear(x1, y1, x2, y2, self.track_w_nm, sli, nc)
                    if not ok_s:
                        all_s_ok = False
                        break
                if all_s_ok and self._emit_fixed_via(vx, vy, nc):
                    self.fanout_vias += 1
                    for sli, x1, y1, x2, y2 in segs:
                        if x1 == x2 and y1 == y2:
                            continue
                        if self._emit_fixed_seg(x1, y1, x2, y2, self.track_w_nm, sli, nc):
                            self.fanout_segs += 1
                    return True
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1),
                           (1, 1), (1, -1), (-1, 1), (-1, -1)):
                x2, y2 = gx + dx, gy + dy
                if x2 < 1 or y2 < 1 or x2 >= self.nx - 1 or y2 >= self.ny - 1:
                    continue
                nb = (li, x2, y2)
                if nb in parent:
                    continue
                fo = self.fixed_owner[li][y2 * self.nx + x2]
                if fo == 0 or fo == nc:
                    parent[nb] = node
                    q.append(nb)
        return False

    def _swap_single_island_to_tree(self, nm: str, nc: int, src_ports: dict,
                                    tree_ports: dict) -> list | None:
        """Evict 1-3 blocking signal nets so one isolated power island can
        connect to `tree_ports`, then re-route the evicted signal nets cleanly."""
        owner = f"net:{nm}"
        path = self._astar_connect(
            nc, list(src_ports.keys()), set(tree_ports.keys()),
            pfac=40.0, strict=False, max_expansions=60_000)
        if path is None:
            return None
        sa = src_ports.get(path[0])
        ea = tree_ports.get(path[-1])
        chains = [(path, sa, ea)]
        segs, via_pts, _, _ = self._chains_to_geometry(chains)
        blockers = set()
        hard_blocked = False
        for vx, vy in via_pts:
            for b in self.exact.via_blockers(vx, vy, self.via_r_nm, self.via_drill_nm,
                                             nc, ignore_owner=owner, find_all=True):
                if b.startswith("net:"):
                    blockers.add(b[4:])
                else:
                    hard_blocked = True
        for li, x1, y1, x2, y2 in segs:
            if x1 == x2 and y1 == y2:
                continue
            for b in self.exact.seg_blockers(x1, y1, x2, y2, self.track_w_nm, li,
                                             nc, ignore_owner=owner, find_all=True):
                if b.startswith("net:"):
                    blockers.add(b[4:])
                else:
                    hard_blocked = True
        blockers.discard(nm)
        if hard_blocked or not blockers or len(blockers) > 3:
            return None
        for bnm in blockers:
            if bnm not in self.committed_records:
                return None
            b_nc = self.committed_records[bnm]["nc"]
            if sum(1 for it in self.net_items_map.get(b_nc, ()) if it[3]) > 10:
                return None

        evicted = {bnm: self._uncommit_net(bnm) for bnm in sorted(blockers)}
        ok_p = self._commit_net_reversible(nm, nc, chains, append=True)
        if not ok_p:
            for bnm, rec in evicted.items():
                if rec is not None:
                    self._restore_net(bnm, rec)
            return None

        re_routed = []
        all_ok = True
        for bnm in sorted(evicted.keys()):
            b_nc = evicted[bnm]["nc"]
            if not self._strict_route_net(bnm, b_nc, max_retries=3):
                all_ok = False
                break
            re_routed.append(bnm)
        if all_ok:
            self.swaps_ok += 1
            return path

        # Rollback power branch + restore evicted signals
        for bnm in re_routed:
            self._uncommit_net(bnm)
        # Pop the appended branch from nm cleanly by rebuilding exact & board for the last appended objs
        rec_nm = self.committed_records.get(nm)
        if rec_nm is not None and len(rec_nm["touched_t_list"]) > 1:
            tt = rec_nm["touched_t_list"].pop()
            tv = rec_nm["touched_v_list"].pop()
            self._apply_footprint(tt, tv, -1)
            # Full clean uncommit+restore of prior branches
            n_segs_drop = sum(1 for _, x1, y1, x2, y2 in segs if not (x1 == x2 and y1 == y2))
            n_vias_drop = len(via_pts)
            rec_nm["segs"] = rec_nm["segs"][:len(rec_nm["segs"]) - len(segs)]
            rec_nm["via_pts"] = rec_nm["via_pts"][:len(rec_nm["via_pts"]) - n_vias_drop]
            old_rec = self._uncommit_net(nm)
            if old_rec is not None:
                self._restore_net(nm, old_rec)
        else:
            self._uncommit_net(nm)
        for bnm, rec in evicted.items():
            if rec is not None:
                self._restore_net(bnm, rec)
        return None

    def _route_power_net_incremental(self, nm: str, nc: int) -> int:
        """Route multi-island power nets (5V, 3V3, GND) incrementally one
        branch at a time (Prim MST order) after signals are locked.

        Avoids PathFinder's all-or-nothing rejection on 20-80 island trees and
        adds local via-rescue + signal blocker eviction for boxed-in power pads.
        """
        owner = f"net:{nm}"
        closed_total = 0

        for _pass in range(2):
            if time.time() - self.t0 > self.time_budget:
                break
            items = self.net_items_map.get(nc, [])
            isls = _compute_net_islands(items)
            if len(isls) < 2:
                break
            ports_list = [self._island_ports(nc, items, isl, ignore_owner=owner) for isl in isls]

            # Main trunk = island with most ports (or items)
            main_i = max(range(len(isls)), key=lambda i: (len(ports_list[i]), len(isls[i])))
            tree_ports = dict(ports_list[main_i])
            remaining = [i for i in range(len(isls)) if i != main_i]

            def cent(pdict, isl):
                if pdict:
                    return (sum(k[1] for k in pdict) / len(pdict),
                            sum(k[2] for k in pdict) / len(pdict))
                pts = [(items[idx][4]["cx"], items[idx][4]["cy"])
                       for idx in isl if items[idx][3]]
                if pts:
                    return self.xy_cell(int(sum(p[0] for p in pts) // len(pts)),
                                        int(sum(p[1] for p in pts) // len(pts)))
                return (self.nx // 2, self.ny // 2)

            cents = {i: cent(ports_list[i], isls[i]) for i in remaining}
            progressed = False

            while remaining and time.time() - self.t0 < self.time_budget:
                # Pick remaining island closest to current tree (Prim MST!)
                t_keys = list(tree_ports.keys())
                if not t_keys:
                    break
                t_sample = t_keys[::max(1, len(t_keys) // 60)]
                best_i = min(
                    remaining,
                    key=lambda i: min(abs(n[1] - cents[i][0]) + abs(n[2] - cents[i][1])
                                      for n in t_sample))
                remaining.remove(best_i)

                src_ports = ports_list[best_i]
                if not src_ports:
                    if self._rescue_single_island_via(nc, items, isls[best_i]):
                        items = self.net_items_map.get(nc, [])
                        # Recompute ports including the newly added rescue via/seg!
                        src_ports = self._island_ports(
                            nc, items, isls[best_i] + [len(items) - 1, len(items) - 2],
                            ignore_owner=owner)
                if not src_ports:
                    continue

                # Strict A* with conflict-blocking retries
                connected_path = None
                extra_blocked = set()
                for _try in range(3):
                    path = self._astar_connect(
                        nc, list(src_ports.keys()), set(tree_ports.keys()),
                        pfac=0.0, strict=True, extra_blocked=extra_blocked,
                        max_expansions=50_000)
                    if path is None:
                        break
                    sa = src_ports.get(path[0])
                    ea = tree_ports.get(path[-1])
                    if self._commit_net_reversible(nm, nc, [(path, sa, ea)], append=True):
                        connected_path = path
                        break
                    added = False
                    for k in range(len(path) - 1):
                        a, b = path[k], path[k + 1]
                        if a[0] != b[0]:
                            vx, vy = self.cell_xy(a[1], a[2])
                            ok, _ = self.exact.via_clear(vx, vy, self.via_r_nm,
                                                         self.via_drill_nm, nc,
                                                         ignore_owner=owner)
                            if not ok:
                                extra_blocked.add(a); extra_blocked.add(b); added = True
                        else:
                            p1, p2 = self.cell_xy(a[1], a[2]), self.cell_xy(b[1], b[2])
                            ok, _ = self.exact.seg_clear(p1[0], p1[1], p2[0], p2[1],
                                                         self.track_w_nm, a[0], nc,
                                                         ignore_owner=owner)
                            if not ok:
                                extra_blocked.add(a); extra_blocked.add(b); added = True
                    if not added:
                        break

                # Fallback 1: add a local fanout via on this power pad and retry!
                if connected_path is None:
                    if self._rescue_single_island_via(nc, self.net_items_map[nc], isls[best_i]):
                        cur_items = self.net_items_map[nc]
                        extra_idxs = [k for k in range(max(0, len(cur_items) - 2), len(cur_items))]
                        src_ports = self._island_ports(nc, cur_items, isls[best_i] + extra_idxs,
                                                       ignore_owner=owner)
                        if src_ports:
                            path = self._astar_connect(
                                nc, list(src_ports.keys()), set(tree_ports.keys()),
                                pfac=0.0, strict=True, max_expansions=50_000)
                            if path is not None:
                                sa = src_ports.get(path[0])
                                ea = tree_ports.get(path[-1])
                                if self._commit_net_reversible(nm, nc, [(path, sa, ea)], append=True):
                                    connected_path = path

                # Fallback 2: bounded signal blocker eviction for this single power island!
                if connected_path is None and src_ports:
                    connected_path = self._swap_single_island_to_tree(nm, nc, src_ports, tree_ports)

                if connected_path is not None:
                    closed_total += 1
                    progressed = True
                    tree_ports.update(src_ports)
                    # Add branch grid nodes as Steiner tap points for subsequent power pads!
                    for node in connected_path[::2]:
                        tree_ports.setdefault(node, self.cell_xy(node[1], node[2]))
            if not progressed:
                break
        return closed_total

    def _greedy_island_polish(self, nm: str, nc: int) -> int:
        closed = 0
        owner = f"net:{nm}"
        for _ in range(30):
            if time.time() - self.t0 > self.time_budget:
                break
            items = self.net_items_map.get(nc, [])
            isls = _compute_net_islands(items)
            if len(isls) < 2:
                break
            ports_list = [self._island_ports(nc, items, isl, ignore_owner=owner) for isl in isls]
            order = sorted(range(len(isls)), key=lambda i: len(isls[i]))
            progressed = False
            for i in order:
                if not ports_list[i]:
                    continue
                other_ports = {}
                for j in range(len(isls)):
                    if j != i:
                        other_ports.update(ports_list[j])
                if not other_ports:
                    continue
                path = self._astar_connect(
                    nc, list(ports_list[i].keys()), set(other_ports.keys()),
                    pfac=0.0, strict=True, max_expansions=60_000)
                if path is None:
                    continue
                sa = ports_list[i].get(path[0])
                ea = other_ports.get(path[-1])
                if self._commit_net_reversible(nm, nc, [(path, sa, ea)], append=True):
                    closed += 1
                    progressed = True
                    break
            if not progressed:
                break
        return closed

    # ---- Phase 0: Coupled Differential Pair Router + Exact-nm Accordion Meander
    def _offset_polyline_nm(self, pts: list[tuple[int, int]], offset_nm: int) -> list[tuple[int, int]]:
        """Offset an octilinear polyline by signed distance `offset_nm` using
        exact miter intersections so P and N stay strictly parallel around every bend."""
        if len(pts) < 2:
            return list(pts)
        edges = []
        for k in range(len(pts) - 1):
            dx = pts[k + 1][0] - pts[k][0]
            dy = pts[k + 1][1] - pts[k][1]
            L = math.hypot(dx, dy)
            if L < 1.0:
                continue
            tx, ty = dx / L, dy / L
            nx, ny = -ty, tx
            edges.append((pts[k], pts[k + 1], tx, ty, nx, ny))
        if not edges:
            return list(pts)
        out = [(int(round(edges[0][0][0] + offset_nm * edges[0][4])),
                int(round(edges[0][0][1] + offset_nm * edges[0][5])))]
        for k in range(len(edges) - 1):
            _, p1, _, _, n1x, n1y = edges[k]
            _, _, _, _, n2x, n2y = edges[k + 1]
            dot = n1x * n2x + n1y * n2y
            denom = 1.0 + dot
            if denom < 0.25:
                mx, my = n1x, n1y
            else:
                scale = min(1.5, 1.0 / denom)
                mx, my = (n1x + n2x) * scale, (n1y + n2y) * scale
            out.append((int(round(p1[0] + offset_nm * mx)),
                        int(round(p1[1] + offset_nm * my))))
        out.append((int(round(edges[-1][1][0] + offset_nm * edges[-1][4])),
                    int(round(edges[-1][1][1] + offset_nm * edges[-1][5]))))
        return out

    def _smooth_grid_xy(self, path: list[tuple[int, int]]) -> list[tuple[int, int]]:
        """Collapse collinear grid steps into clean octilinear nm waypoints."""
        if len(path) < 2:
            return [self.cell_xy(p[0], p[1]) for p in path]
        out = [self.cell_xy(path[0][0], path[0][1])]
        prev_d = (path[1][0] - path[0][0], path[1][1] - path[0][1])
        for k in range(1, len(path) - 1):
            d = (path[k + 1][0] - path[k][0], path[k + 1][1] - path[k][1])
            if d != prev_d:
                out.append(self.cell_xy(path[k][0], path[k][1]))
                prev_d = d
        out.append(self.cell_xy(path[-1][0], path[-1][1]))
        return out

    def _bridge_same_fp_diff_pads(self, nc_p: int, nc_n: int, li: int, w_nm: int) -> None:
        """Bridge same-footprint duplicate diff-pair pads (e.g. USB-C A6/B6 and
        A7/B7) on opposite sides of the pad row with zero vias and zero crossings."""
        by_fp_p = defaultdict(list)
        by_fp_n = defaultdict(list)
        for it in self.net_items_map.get(nc_p, ()):
            if it[3] and li in it[1]:
                by_fp_p[it[4]["ref"]].append(it[4])
        for it in self.net_items_map.get(nc_n, ()):
            if it[3] and li in it[1]:
                by_fp_n[it[4]["ref"]].append(it[4])
        for ref in set(by_fp_p) & set(by_fp_n):
            ps, ns = by_fp_p[ref], by_fp_n[ref]
            if len(ps) < 2 and len(ns) < 2:
                continue
            all_p = ps + ns
            xs = [m["cx"] for m in all_p]
            ys = [m["cy"] for m in all_p]
            vertical_row = (max(ys) - min(ys)) >= (max(xs) - min(xs))
            # Try opposite U-detours: P on side +1, N on side -1 (or vice versa)
            for sgn_p in (-1, 1):
                sgn_n = -sgn_p
                plan = []
                ok_both = True
                for nc_cur, pads_cur, sgn in ((nc_p, ps, sgn_p), (nc_n, ns, sgn_n)):
                    if len(pads_cur) < 2:
                        continue
                    pads_cur = sorted(pads_cur, key=lambda m: (m["cy"], m["cx"]) if vertical_row else (m["cx"], m["cy"]))
                    for k in range(len(pads_cur) - 1):
                        a, b = pads_cur[k], pads_cur[k + 1]
                        bridged = False
                        for off_um in (1_050_000, 1_250_000, 1_500_000, 1_800_000, 900_000):
                            if vertical_row:
                                ox = a["cx"] + sgn * off_um
                                segs = [
                                    (a["cx"], a["cy"], ox, a["cy"]),
                                    (ox, a["cy"], ox, b["cy"]),
                                    (ox, b["cy"], b["cx"], b["cy"]),
                                ]
                            else:
                                oy = a["cy"] + sgn * off_um
                                segs = [
                                    (a["cx"], a["cy"], a["cx"], oy),
                                    (a["cx"], oy, b["cx"], oy),
                                    (b["cx"], oy, b["cx"], b["cy"]),
                                ]
                            if all(self.exact.seg_clear(x1, y1, x2, y2, w_nm, li, nc_cur)[0]
                                   for x1, y1, x2, y2 in segs):
                                for x1, y1, x2, y2 in segs:
                                    plan.append((nc_cur, x1, y1, x2, y2))
                                bridged = True
                                break
                        if not bridged:
                            ok_both = False
                            break
                    if not ok_both:
                        break
                if ok_both and plan:
                    for nc_cur, x1, y1, x2, y2 in plan:
                        self._emit_fixed_seg(x1, y1, x2, y2, w_nm, li, nc_cur)
                    break

    def _coupled_centerline_astar(self, li: int, nc_p: int, nc_n: int,
                                  start_xy: tuple[int, int], goal_xy: tuple[int, int],
                                  env_r_nm: int) -> list[tuple[int, int]] | None:
        """Single-layer 8-direction A* for a coupled differential pair centerline."""
        sx, sy = self.xy_cell(*start_xy)
        gx, gy = self.xy_cell(*goal_xy)
        nx, ny = self.nx, self.ny
        if not (2 <= sx < nx - 2 and 2 <= sy < ny - 2 and 2 <= gx < nx - 2 and 2 <= gy < ny - 2):
            return None
        f_own = self.fixed_owner[li]
        # Inflate foreign fixed obstacles by coupled envelope radius + miter/grid safety!
        extra_r = max(0, env_r_nm - (self.track_w_nm // 2 + self.safe_clear_nm))
        halo = self._disk_offsets(extra_r) if extra_r > 0 else [(0, 0)]

        def cell_ok(x: int, y: int) -> bool:
            if (x == sx and y == sy) or (x == gx and y == gy):
                return True
            for dx, dy in halo:
                x2, y2 = x + dx, y + dy
                if x2 < 1 or y2 < 1 or x2 >= nx - 1 or y2 >= ny - 1:
                    return False
                fo = f_own[y2 * nx + x2]
                if fo != 0 and fo != nc_p and fo != nc_n:
                    return False
            return True

        ok_cache: dict[tuple[int, int], bool] = {}

        def is_ok(x: int, y: int) -> bool:
            v = ok_cache.get((x, y))
            if v is None:
                v = cell_ok(x, y)
                ok_cache[(x, y)] = v
            return v

        def heur(x: int, y: int) -> float:
            dx, dy = abs(gx - x), abs(gy - y)
            return 1.1 * (max(dx, dy) + 0.4142 * min(dx, dy))

        heap = [(heur(sx, sy), 0, (sx, sy), None)]
        gscore = {(sx, sy): 0.0}
        came = {}
        cnt = 0
        steps = ((1, 0, 1.0), (-1, 0, 1.0), (0, 1, 1.0), (0, -1, 1.0),
                 (1, 1, 1.4142), (1, -1, 1.4142), (-1, 1, 1.4142), (-1, -1, 1.4142))
        exps = 0
        found = None
        while heap and exps < 80_000:
            _, _, u, prev_dir = heapq.heappop(heap)
            if u == (gx, gy):
                found = u
                break
            exps += 1
            cur_g = gscore[u]
            ux, uy = u
            for dx, dy, step_d in steps:
                x2, y2 = ux + dx, uy + dy
                if x2 < 2 or y2 < 2 or x2 >= nx - 2 or y2 >= ny - 2:
                    continue
                if not is_ok(x2, y2):
                    continue
                turn_pen = 0.0 if (prev_dir is None or prev_dir == (dx, dy)) else 0.45
                ng = cur_g + step_d + turn_pen
                nb = (x2, y2)
                if ng < gscore.get(nb, 1e18):
                    gscore[nb] = ng
                    came[nb] = u
                    cnt += 1
                    heapq.heappush(heap, (ng + heur(x2, y2), cnt, nb, (dx, dy)))
        if found is None:
            return None
        path = []
        cur = found
        while cur in came:
            path.append(cur)
            cur = came[cur]
        path.append(cur)
        path.reverse()
        return path

    def _find_neck_escape_chain(self, p0: tuple[int, int], p1: tuple[int, int],
                                li: int, nc: int, sw: int) -> list[tuple[tuple[int, int], tuple[int, int], int]] | None:
        """Find a clean 1-, 2-, or 3-segment octilinear escape chain from fine-pitch
        pad p0 to open anchor p1 at width sw (handles BGA interior diagonal-to-channel)."""
        x0, y0 = p0
        x1, y1 = p1
        if (x0, y0) == (x1, y1):
            return []
        # 1) Direct segment
        if self.exact.seg_clear(x0, y0, x1, y1, sw, li, nc)[0]:
            return [(p0, p1, sw)]

        dx, dy = x1 - x0, y1 - y0
        adx, ady = abs(dx), abs(dy)
        sx = 1 if dx >= 0 else -1
        sy = 1 if dy >= 0 else -1

        # 2) Clean 2-segment octilinear (45-deg + H/V or H/V + 45-deg or L-bend)
        diag = min(adx, ady)
        knees_2 = []
        if diag > 0:
            knees_2.append((x0 + sx * diag, y0 + sy * diag))
            knees_2.append((x1 - sx * diag, y1 - sy * diag))
        knees_2.append((x1, y0))
        knees_2.append((x0, y1))
        for km in knees_2:
            if km != p0 and km != p1:
                if (self.exact.seg_clear(x0, y0, km[0], km[1], sw, li, nc)[0] and
                        self.exact.seg_clear(km[0], km[1], x1, y1, sw, li, nc)[0]):
                    return [(p0, km, sw), (km, p1, sw)]

        # 3) 3-segment BGA / fine-pitch diagonal channel escape:
        # step 45° into the inter-ball channel (half-pitch = 0.35..0.55 mm),
        # slide along the channel toward p1, then connect into p1!
        for d_slot in (400_000, 450_000, 350_000, 500_000, 550_000, 300_000):
            for dsx in (1, -1):
                for dsy in (1, -1):
                    k1 = (x0 + dsx * d_slot, y0 + dsy * d_slot)
                    if not self.exact.seg_clear(x0, y0, k1[0], k1[1], sw, li, nc)[0]:
                        continue
                    # Slide horizontally or vertically along channel toward p1
                    for k2 in ((x1, k1[1]), (k1[0], y1),
                               (x1 - sx * abs(y1 - k1[1]), k1[1]),
                               (k1[0], y1 - sy * abs(x1 - k1[0]))):
                        if k2 == k1 or k2 == p1:
                            continue
                        if (self.exact.seg_clear(k1[0], k1[1], k2[0], k2[1], sw, li, nc)[0] and
                                self.exact.seg_clear(k2[0], k2[1], x1, y1, sw, li, nc)[0]):
                            return [(p0, k1, sw), (k1, k2, sw), (k2, p1, sw)]
        return None

    def _chains_pn_clear(self, p_chain: list, n_chain: list) -> bool:
        for a1, b1, w1 in p_chain:
            for a2, b2, w2 in n_chain:
                d2_n, d2_d = _seg_seg_d2(a1, b1, a2, b2)
                min_sep = (w1 + w2) // 2 + self.safe_clear_nm
                if _frac_lt(d2_n, d2_d, min_sep * min_sep, 1):
                    return False
        return True

    def _find_diff_escape_pair(self, pa: dict, na: dict, li: int,
                               nc_p: int, nc_n: int, w_nm: int, cc_nm: int,
                               towards_xy: tuple[int, int]) -> list[tuple[list, list, tuple[int, int], tuple[int, int]]]:
        """Find clean parallel escape anchor pairs (p_segs, n_segs, pe, ne) on layer `li`
        for pads (pa, na), supporting both 1-seg direct escapes and 2/3-seg BGA channel
        neck-downs into open coupled space."""
        px, py = pa["cx"], pa["cy"]
        nx_, ny_ = na["cx"], na["cy"]
        mx, my = (px + nx_) // 2, (py + ny_) // 2
        vx, vy = towards_xy[0] - mx, towards_xy[1] - my
        vnorm = math.hypot(vx, vy) or 1.0
        ux, uy = vx / vnorm, vy / vnorm

        # Candidate outward escape unit vectors sorted by alignment toward destination
        base_dirs = [
            (ux, uy),
            (pa["out_dir"][0], pa["out_dir"][1]),
            (1.0, 0.0), (-1.0, 0.0), (0.0, 1.0), (0.0, -1.0),
            (0.7071, 0.7071), (0.7071, -0.7071), (-0.7071, 0.7071), (-0.7071, -0.7071),
        ]
        # Sort remaining directions after (ux, uy) by dot product with (ux, uy)
        cand_dirs = [base_dirs[0]] + sorted(
            base_dirs[1:],
            key=lambda d: -(d[0] * ux + d[1] * uy) / (math.hypot(d[0], d[1]) or 1.0)
        )
        stub_widths = [w_nm] if w_nm <= self.track_w_nm else [w_nm, self.track_w_nm]
        results = []
        half_cc = cc_nm // 2
        env_check_r = half_cc + w_nm // 2 + self.safe_clear_nm

        def _anchor_open(pe, ne, dx, dy):
            # Verify both anchors sit in open space where full-width coupled trunk can launch
            fwd = 250_000
            pe2 = (int(round(pe[0] + dx * fwd)), int(round(pe[1] + dy * fwd)))
            ne2 = (int(round(ne[0] + dx * fwd)), int(round(ne[1] + dy * fwd)))
            if not self.exact.seg_clear(pe[0], pe[1], pe2[0], pe2[1], w_nm, li, nc_p)[0]:
                return False
            if not self.exact.seg_clear(ne[0], ne[1], ne2[0], ne2[1], w_nm, li, nc_n)[0]:
                return False
            return True

        # Stage A: Clean 1-segment straight escape into open space
        for dx, dy in cand_dirs:
            dn = math.hypot(dx, dy)
            if dn < 0.1:
                continue
            dx, dy = dx / dn, dy / dn
            rx, ry = -dy, dx
            dot_pn = (px - nx_) * rx + (py - ny_) * ry
            sgn = 1 if dot_pn >= 0 else -1
            rx, ry = rx * sgn, ry * sgn
            for dist_um in (1_800_000, 2_400_000, 1_400_000, 3_000_000, 1_000_000):
                cx_e = int(round(mx + dx * dist_um))
                cy_e = int(round(my + dy * dist_um))
                pe = (int(round(cx_e + rx * half_cc)), int(round(cy_e + ry * half_cc)))
                ne = (int(round(cx_e - rx * half_cc)), int(round(cy_e - ry * half_cc)))
                if not _anchor_open(pe, ne, dx, dy):
                    continue
                for sw in stub_widths:
                    if not self.exact.seg_clear(px, py, pe[0], pe[1], sw, li, nc_p)[0]:
                        continue
                    if not self.exact.seg_clear(nx_, ny_, ne[0], ne[1], sw, li, nc_n)[0]:
                        continue
                    p_ch = [((px, py), pe, sw)]
                    n_ch = [((nx_, ny_), ne, sw)]
                    if not self._chains_pn_clear(p_ch, n_ch):
                        continue
                    results.append((p_ch, n_ch, pe, ne))
                    break
                if len(results) >= 6:
                    break
            if len(results) >= 6:
                break

        if len(results) >= 2:
            return results

        # Stage B: 2/3-segment fine-pitch / BGA diagonal-channel neck-down escape!
        sw_neck = self.track_w_nm
        for dx, dy in cand_dirs:
            dn = math.hypot(dx, dy)
            if dn < 0.1:
                continue
            dx, dy = dx / dn, dy / dn
            rx, ry = -dy, dx
            for sgn in (1, -1):
                rrx, rry = rx * sgn, ry * sgn
                for dist_um in (2_200_000, 2_800_000, 3_400_000, 4_200_000):
                    cx_e = int(round(mx + dx * dist_um))
                    cy_e = int(round(my + dy * dist_um))
                    pe = (int(round(cx_e + rrx * half_cc)), int(round(cy_e + rry * half_cc)))
                    ne = (int(round(cx_e - rrx * half_cc)), int(round(cy_e - rry * half_cc)))
                    if not _anchor_open(pe, ne, dx, dy):
                        continue
                    p_ch = self._find_neck_escape_chain((px, py), pe, li, nc_p, sw_neck)
                    if not p_ch:
                        continue
                    n_ch = self._find_neck_escape_chain((nx_, ny_), ne, li, nc_n, sw_neck)
                    if not n_ch:
                        continue
                    if not self._chains_pn_clear(p_ch, n_ch):
                        continue
                    results.append((p_ch, n_ch, pe, ne))
                    break
                if len(results) >= 6:
                    break
            if len(results) >= 6:
                break
        return results

    def _tune_diff_pair_meander(self, dp_name: str, nc_p: int, nc_n: int,
                                li: int, w_nm: int, s_nm: int, max_skew_nm: int,
                                p_recs: list[tuple], n_recs: list[tuple]) -> dict:
        """Exact-nm 45-degree accordion/trombone meander tuner:
        adds verified serpentine bumps on the shorter net pointing AWAY from
        its partner until |L(P) - L(N)| <= max_skew_nm."""
        def total_len(recs):
            return sum(math.hypot(r[3] - r[1], r[4] - r[2]) for r in recs)

        bumps = 0
        for _ in range(16):
            lp = total_len(p_recs)
            ln = total_len(n_recs)
            diff = abs(lp - ln)
            if diff <= max_skew_nm:
                break
            if lp < ln:
                s_nc, s_recs, o_recs = nc_p, p_recs, n_recs
            else:
                s_nc, s_recs, o_recs = nc_n, n_recs, p_recs

            # Sort candidate segments longest first (skip tiny fan-in stubs < 1.0mm)
            cands = sorted(
                range(len(s_recs)),
                key=lambda k: math.hypot(s_recs[k][3] - s_recs[k][1], s_recs[k][4] - s_recs[k][2]),
                reverse=True,
            )
            inserted = False
            for idx in cands:
                obj, x1, y1, x2, y2, sw, own = s_recs[idx]
                L = math.hypot(x2 - x1, y2 - y1)
                if L < 1_050_000:
                    continue
                ux, uy = (x2 - x1) / L, (y2 - y1) / L
                nx_, ny_ = -uy, ux
                mx, my = 0.5 * (x1 + x2), 0.5 * (y1 + y2)

                # Determine outward side pointing AWAY from partner net!
                best_d, best_dot = 1e18, 0.0
                for _, ox1, oy1, ox2, oy2, _, _ in o_recs:
                    omx, omy = 0.5 * (ox1 + ox2), 0.5 * (oy1 + oy2)
                    d = math.hypot(omx - mx, omy - my)
                    if d < best_d:
                        best_d = d
                        best_dot = (omx - mx) * nx_ + (omy - my) * ny_
                out_sides = [-1, 1] if best_dot > 0 else [1, -1]

                # Bevel b_nm and top_w_nm keep adjacent bump legs >= sw + safe_clear
                min_leg_gap = sw + max(self.safe_clear_nm, 140_000) + 40_000
                top_w = max(min_leg_gap, 320_000)
                b_nm = 200_000
                base_span = 2 * b_nm + top_w
                if L < base_span + 350_000:
                    continue

                # Solve exact amplitude for needed delta:
                # extra = 2 * amp - (2 - sqrt(2)) * b_nm  =>  amp = (diff + 0.5858*b_nm)/2
                amp_ideal = int(round((diff + (2.0 - math.sqrt(2.0)) * b_nm) / 2.0))
                amp_cap = min(1_350_000, max(260_000, amp_ideal))
                amps = sorted({
                    amp_cap,
                    min(amp_cap, 900_000),
                    min(amp_cap, 600_000),
                    min(amp_cap, 420_000),
                    min(amp_cap, 280_000),
                }, reverse=True)

                for sgn in out_sides:
                    dnx, dny = nx_ * sgn, ny_ * sgn
                    for amp in amps:
                        if amp < 240_000:
                            continue
                        bb = min(b_nm, amp)
                        span = 2 * bb + top_w
                        if L < span + 300_000:
                            continue
                        t_start = 0.5 * (L - span)
                        p_a = (int(round(x1 + ux * t_start)),
                               int(round(y1 + uy * t_start)))
                        p_b = (int(round(x1 + ux * (t_start + bb) + dnx * bb)),
                               int(round(y1 + uy * (t_start + bb) + dny * bb)))
                        p_c = (int(round(x1 + ux * (t_start + bb) + dnx * amp)),
                               int(round(y1 + uy * (t_start + bb) + dny * amp)))
                        p_d = (int(round(x1 + ux * (t_start + bb + top_w) + dnx * amp)),
                               int(round(y1 + uy * (t_start + bb + top_w) + dny * amp)))
                        p_e = (int(round(x1 + ux * (t_start + bb + top_w) + dnx * bb)),
                               int(round(y1 + uy * (t_start + bb + top_w) + dny * bb)))
                        p_f = (int(round(x1 + ux * (t_start + span))),
                               int(round(y1 + uy * (t_start + span))))
                        chain_pts = [(x1, y1), p_a, p_b, p_c, p_d, p_e, p_f, (x2, y2)]
                        new_segs = []
                        for k in range(len(chain_pts) - 1):
                            a_, b_ = chain_pts[k], chain_pts[k + 1]
                            if a_ != b_:
                                new_segs.append((a_[0], a_[1], b_[0], b_[1]))
                        if not new_segs:
                            continue
                        # Verify every new segment in ExactBoard (ignoring old segment's own id!)
                        all_ok = True
                        for sx1, sy1, sx2, sy2 in new_segs:
                            ok_s, _ = self.exact.seg_clear(sx1, sy1, sx2, sy2, sw, li, s_nc,
                                                           ignore_owner=own)
                            if not ok_s:
                                all_ok = False
                                break
                        if not all_ok:
                            continue
                        # Swap old segment for meander segments cleanly!
                        self.exact.remove_owner(own)
                        self.board.Delete(obj)
                        self.net_items_map[s_nc] = [
                            it for it in self.net_items_map[s_nc] if it[4].get("owner") != own]
                        del s_recs[idx]
                        for k_sub, (sx1, sy1, sx2, sy2) in enumerate(new_segs):
                            sub_own = f"{own}_m{bumps}_{k_sub}"
                            new_obj = self._emit_seg_raw(sx1, sy1, sx2, sy2, sw, li, s_nc,
                                                         owner=sub_own, mark_fixed_grid=True)
                            if new_obj is not None:
                                s_recs.append((new_obj, sx1, sy1, sx2, sy2, sw, sub_own))
                        bumps += 1
                        inserted = True
                        break
                    if inserted:
                        break
                if inserted:
                    break
            if not inserted:
                break

        lp_f, ln_f = total_len(p_recs) / MM, total_len(n_recs) / MM
        return {
            "name": dp_name,
            "L_p_mm": round(lp_f, 3),
            "L_n_mm": round(ln_f, 3),
            "skew_mm": round(abs(lp_f - ln_f), 3),
            "meander_bumps": bumps,
        }

    def run_diff_pairs(self, open_names: list[str]) -> set[str]:
        """Phase 0: Route lowered differential pairs FIRST as tightly coupled,
        same-layer parallel pairs with exact-nm meander length matching."""
        closed_nets: set[str] = set()
        if not self.diff_pairs_cfg:
            return closed_nets
        open_set = set(open_names)
        for dp_idx, dp in enumerate(self.diff_pairs_cfg):
            p_name, n_name = dp.get("p"), dp.get("n")
            if not p_name or not n_name:
                continue
            if p_name not in open_set and n_name not in open_set:
                continue
            nc_p = self.code_by_name.get(p_name)
            nc_n = self.code_by_name.get(n_name)
            if not nc_p or not nc_n:
                continue
            w_nm = max(self.track_w_nm, int(round(float(dp.get("w_mm", 0.16)) * MM)))
            s_nm = max(self.safe_clear_nm + 4_000, int(round(float(dp.get("s_mm", 0.18)) * MM)))
            max_skew_nm = int(round(float(dp.get("max_skew_mm", 0.5)) * MM))
            cc_nm = w_nm + s_nm
            half_cc = cc_nm // 2
            pref_layers = list(dp.get("prefer_layers") or [self.f_i])
            # Add fallback signal layers (avoiding inner plane layers)
            for li in range(self.nl):
                if li not in pref_layers and li not in self.plane_layer_nets:
                    pref_layers.append(li)

            # Bridge duplicate pads on same footprint first (e.g. USB-C A6/B6, A7/B7)
            self._bridge_same_fp_diff_pads(nc_p, nc_n, pref_layers[0], self.track_w_nm)

            # Group pads by footprint to find the two endpoints of the diff pair
            fp_p = defaultdict(list)
            fp_n = defaultdict(list)
            for it in self.net_items_map.get(nc_p, ()):
                if it[3]:
                    fp_p[it[4]["ref"]].append(it[4])
            for it in self.net_items_map.get(nc_n, ()):
                if it[3]:
                    fp_n[it[4]["ref"]].append(it[4])
            shared_fps = sorted(set(fp_p) & set(fp_n))
            if len(shared_fps) < 2:
                continue
            ref_a, ref_b = shared_fps[0], shared_fps[1]

            # Build sorted candidate adjacent (pa, na) pad pairs on each footprint
            ca = (sum(m["cx"] for m in fp_p[ref_a] + fp_n[ref_a]) // len(fp_p[ref_a] + fp_n[ref_a]),
                  sum(m["cy"] for m in fp_p[ref_a] + fp_n[ref_a]) // len(fp_p[ref_a] + fp_n[ref_a]))
            cb = (sum(m["cx"] for m in fp_p[ref_b] + fp_n[ref_b]) // len(fp_p[ref_b] + fp_n[ref_b]),
                  sum(m["cy"] for m in fp_p[ref_b] + fp_n[ref_b]) // len(fp_p[ref_b] + fp_n[ref_b]))
            cands_a = sorted(
                [(p_, n_) for p_ in fp_p[ref_a] for n_ in fp_n[ref_a]],
                key=lambda pn: (math.hypot(pn[0]["cx"] - pn[1]["cx"], pn[0]["cy"] - pn[1]["cy"]),
                                math.hypot(pn[0]["cx"] - cb[0], pn[0]["cy"] - cb[1]))
            )[:4]
            cands_b = sorted(
                [(p_, n_) for p_ in fp_p[ref_b] for n_ in fp_n[ref_b]],
                key=lambda pn: (math.hypot(pn[0]["cx"] - pn[1]["cx"], pn[0]["cy"] - pn[1]["cy"]),
                                math.hypot(pn[0]["cx"] - ca[0], pn[0]["cy"] - ca[1]))
            )[:4]

            routed_ok = False
            for li in pref_layers:
                for pa, na in cands_a:
                    if li not in pa["layers"] or li not in na["layers"]:
                        continue
                    esc_a_list = self._find_diff_escape_pair(pa, na, li, nc_p, nc_n, w_nm, cc_nm, cb)
                    if not esc_a_list:
                        continue
                    for pb, nb in cands_b:
                        if li not in pb["layers"] or li not in nb["layers"]:
                            continue
                        esc_b_list = self._find_diff_escape_pair(pb, nb, li, nc_p, nc_n, w_nm, cc_nm, ca)
                        if not esc_b_list:
                            continue
                        env_margins = (
                            half_cc + w_nm // 2 + self.safe_clear_nm + int(0.45 * half_cc) + self.grid_nm,
                            half_cc + w_nm // 2 + self.safe_clear_nm + self.grid_nm,
                        )
                        for env_r in env_margins:
                            for (p_esc_a, n_esc_a, pa_e, na_e) in esc_a_list[:4]:
                                for (p_esc_b, n_esc_b, pb_e, nb_e) in esc_b_list[:4]:
                                    mid_a = ((pa_e[0] + na_e[0]) // 2, (pa_e[1] + na_e[1]) // 2)
                                    mid_b = ((pb_e[0] + nb_e[0]) // 2, (pb_e[1] + nb_e[1]) // 2)
                                    grid_c = self._coupled_centerline_astar(li, nc_p, nc_n, mid_a, mid_b, env_r)
                                    if not grid_c or len(grid_c) < 2:
                                        continue
                                    c_pts = self._smooth_grid_xy(grid_c)
                                    # Align start/end of centerline smoothly to exact mid_a / mid_b
                                    if len(c_pts) >= 2:
                                        c_pts[0] = mid_a
                                        c_pts[-1] = mid_b
                                    poly_plus = self._offset_polyline_nm(c_pts, +half_cc)
                                    poly_minus = self._offset_polyline_nm(c_pts, -half_cc)

                                    # Choose non-crossing polarity matching pad entry/exit sides!
                                    d_direct = (math.hypot(pa_e[0] - poly_plus[0][0], pa_e[1] - poly_plus[0][1]) +
                                                math.hypot(na_e[0] - poly_minus[0][0], na_e[1] - poly_minus[0][1]) +
                                                math.hypot(pb_e[0] - poly_plus[-1][0], pb_e[1] - poly_plus[-1][1]) +
                                                math.hypot(nb_e[0] - poly_minus[-1][0], nb_e[1] - poly_minus[-1][1]))
                                    d_swap = (math.hypot(pa_e[0] - poly_minus[0][0], pa_e[1] - poly_minus[0][1]) +
                                              math.hypot(na_e[0] - poly_plus[0][0], na_e[1] - poly_plus[0][1]) +
                                              math.hypot(pb_e[0] - poly_minus[-1][0], pb_e[1] - poly_minus[-1][1]) +
                                              math.hypot(nb_e[0] - poly_plus[-1][0], nb_e[1] - poly_plus[-1][1]))
                                    p_trunk, n_trunk = (poly_plus, poly_minus) if d_direct <= d_swap else (poly_minus, poly_plus)

                                    p_segs = list(p_esc_a) + [(pa_e, p_trunk[0], w_nm)]
                                    for k in range(len(p_trunk) - 1):
                                        p_segs.append((p_trunk[k], p_trunk[k + 1], w_nm))
                                    p_segs.append((p_trunk[-1], pb_e, w_nm))
                                    # Reverse b escape chain so segments walk pb_e -> pb
                                    for a_, b_, sw_ in reversed(p_esc_b):
                                        p_segs.append((b_, a_, sw_))

                                    n_segs = list(n_esc_a) + [(na_e, n_trunk[0], w_nm)]
                                    for k in range(len(n_trunk) - 1):
                                        n_segs.append((n_trunk[k], n_trunk[k + 1], w_nm))
                                    n_segs.append((n_trunk[-1], nb_e, w_nm))
                                    for a_, b_, sw_ in reversed(n_esc_b):
                                        n_segs.append((b_, a_, sw_))

                                    # Exact-nm check of P against fixed board, N against fixed board, AND P against N!
                                    p_segs = [(a_, b_, sw_) for a_, b_, sw_ in p_segs if a_ != b_]
                                    n_segs = [(a_, b_, sw_) for a_, b_, sw_ in n_segs if a_ != b_]
                                    if not all(self.exact.seg_clear(a_[0], a_[1], b_[0], b_[1], sw_, li, nc_p)[0]
                                               for a_, b_, sw_ in p_segs):
                                        continue
                                    if not all(self.exact.seg_clear(a_[0], a_[1], b_[0], b_[1], sw_, li, nc_n)[0]
                                               for a_, b_, sw_ in n_segs):
                                        continue
                                    if not self._chains_pn_clear(p_segs, n_segs):
                                        continue

                                    # Commit P and N with individual segment owners so meander tuner can edit cleanly!
                                    p_recs, n_recs = [], []
                                    for k_s, (a_, b_, sw_) in enumerate(p_segs):
                                        own = f"dp{dp_idx}_p_{k_s}"
                                        obj = self._emit_seg_raw(a_[0], a_[1], b_[0], b_[1], sw_, li, nc_p,
                                                                 owner=own, mark_fixed_grid=True)
                                        if obj is not None:
                                            p_recs.append((obj, a_[0], a_[1], b_[0], b_[1], sw_, own))
                                    for k_s, (a_, b_, sw_) in enumerate(n_segs):
                                        own = f"dp{dp_idx}_n_{k_s}"
                                        obj = self._emit_seg_raw(a_[0], a_[1], b_[0], b_[1], sw_, li, nc_n,
                                                                 owner=own, mark_fixed_grid=True)
                                        if obj is not None:
                                            n_recs.append((obj, a_[0], a_[1], b_[0], b_[1], sw_, own))

                                    # Tune exact meander length matching (tight <=0.35mm target)!
                                    mstat = self._tune_diff_pair_meander(
                                        dp.get("name", f"{p_name}/{n_name}"), nc_p, nc_n, li,
                                        w_nm, s_nm, min(max_skew_nm, 350_000), p_recs, n_recs)
                                    mstat["layer"] = self.board.GetLayerName(self.lids[li])
                                    mstat["w_mm"] = round(w_nm / MM, 3)
                                    mstat["s_mm"] = round(s_nm / MM, 3)
                                    mstat["vias"] = 0
                                    self.diff_pair_stats.append(mstat)

                                    if len(_compute_net_islands(self.net_items_map[nc_p])) < 2:
                                        closed_nets.add(p_name)
                                    if len(_compute_net_islands(self.net_items_map[nc_n])) < 2:
                                        closed_nets.add(n_name)
                                    routed_ok = True
                                    break
                                if routed_ok:
                                    break
                            if routed_ok:
                                break
                        if routed_ok:
                            break
                    if routed_ok:
                        break
                if routed_ok:
                    break
        return closed_nets

    # ---- Phase 7: Exact-nm Power Trunk Widening + Automatic Local Neck-Down
    def run_power_widening(self) -> int:
        """Upgrade power-net routed segments from track_min up to target trunk width
        (e.g. 5V -> 0.45-0.50mm, 3V3/GND -> 0.35mm), automatically splitting long
        segments with short local neck-downs near fine-pitch BGA/LGA pads so
        ExactBoard guarantees ZERO new clearance or edge violations."""
        if not self.net_widths_nm:
            return 0
        widened = 0
        for t in list(self.board.GetTracks()):
            if t.Type() == pcbnew.PCB_VIA_T:
                continue
            nc = int(t.GetNetCode())
            target_w = self.net_widths_nm.get(nc)
            if not target_w:
                continue
            cur_w = int(t.GetWidth())
            if cur_w >= target_w - 5_000:
                continue
            li = self.lid_to_i.get(t.GetLayer())
            if li is None:
                continue
            s, e = t.GetStart(), t.GetEnd()
            x1, y1, x2, y2 = int(s.x), int(s.y), int(e.x), int(e.y)
            L = math.hypot(x2 - x1, y2 - y1)
            if L < 150_000:
                continue

            # Candidate step-down widths from target down to > cur_w
            w_steps = [w for w in (target_w, 450_000, 400_000, 350_000, 300_000, 250_000, 200_000)
                       if cur_w + 15_000 < w <= target_w]
            if not w_steps:
                continue

            # 1) Try full-segment widening first!
            best_full = None
            for cand_w in w_steps:
                ok, _ = self.exact.seg_clear(x1, y1, x2, y2, cand_w, li, nc)
                if ok:
                    best_full = cand_w
                    break
            if best_full is not None:
                t.SetWidth(int(best_full))
                self.exact.add_item(nc, (li,), (0, x1, y1, x2, y2, best_full), owner=f"wide_{widened}")
                widened += 1
                continue

            # 2) Segment blocked only near tight pad/via end -> split with local neck-down!
            if L < 950_000:
                continue
            ux, uy = (x2 - x1) / L, (y2 - y1) / L
            split_done = False
            for cand_w in w_steps:
                for n1_um, n2_um in ((0, 500_000), (500_000, 0),
                                     (450_000, 450_000), (650_000, 650_000)):
                    if L < n1_um + n2_um + 350_000:
                        continue
                    mx1 = int(round(x1 + ux * n1_um)) if n1_um else x1
                    my1 = int(round(y1 + uy * n1_um)) if n1_um else y1
                    mx2 = int(round(x2 - ux * n2_um)) if n2_um else x2
                    my2 = int(round(y2 - uy * n2_um)) if n2_um else y2
                    ok_mid, _ = self.exact.seg_clear(mx1, my1, mx2, my2, cand_w, li, nc)
                    if not ok_mid:
                        continue
                    # Replace original segment with neck(s) + wide trunk!
                    self.board.Delete(t)
                    pieces = []
                    if n1_um:
                        pieces.append((x1, y1, mx1, my1, cur_w))
                    pieces.append((mx1, my1, mx2, my2, cand_w))
                    if n2_um:
                        pieces.append((mx2, my2, x2, y2, cur_w))
                    for px1, py1, px2, py2, pw in pieces:
                        if px1 == px2 and py1 == py2:
                            continue
                        nt = pcbnew.PCB_TRACK(self.board)
                        nt.SetStart(pcbnew.VECTOR2I(px1, py1))
                        nt.SetEnd(pcbnew.VECTOR2I(px2, py2))
                        nt.SetWidth(int(pw))
                        nt.SetLayer(self.lids[li])
                        nt.SetNet(self.net_by_code[nc])
                        self.board.Add(nt)
                        self.exact.add_item(nc, (li,), (0, px1, py1, px2, py2, pw), owner=f"wide_{widened}")
                    widened += 1
                    split_done = True
                    break
                if split_done:
                    break
        self.power_widened_segs = widened
        return widened

    # ---- Plane-Layer Direct Pad Stitching (when solid plane exists for a rail)
    def run_plane_stitching(self, open_names: list[str]) -> None:
        """If an inner plane layer is dedicated to a rail (e.g. In1.Cu = GND),
        drop clean short fanout vias on single-layer pads of that rail so they
        connect directly to the solid plane instead of long thin traces."""
        if not self.plane_layer_nets:
            return
        open_set = set(open_names)
        plane_nets = set(self.plane_layer_nets.values())
        for nm in sorted(plane_nets & open_set):
            nc = self.code_by_name.get(nm)
            if not nc:
                continue
            items = self.net_items_map.get(nc, [])
            isls = _compute_net_islands(items)
            for isl in isls:
                if any(len(items[i][1]) > 1 for i in isl):
                    continue
                if self._rescue_single_island_via(nc, self.net_items_map[nc], isl):
                    self.plane_stitch_vias += 1

    def _trim_dangling_stubs(self) -> int:
        """Remove strictly degree-1 dead-end track stubs whose free endpoint does
        not touch any same-net pad, via, or interior of another track segment."""
        # Collect pads and vias per net
        pads_by_nc: dict[int, list[tuple[tuple[int, ...], tuple]]] = defaultdict(list)
        for nc, items in self.net_items_map.items():
            for _, lays, sh, is_pad, _ in items:
                if is_pad:
                    pads_by_nc[nc].append((lays, sh))
        vias_by_nc: dict[int, list[tuple[int, int, int]]] = defaultdict(list)
        segs: list = []
        for t in list(self.board.GetTracks()):
            nc = int(t.GetNetCode())
            if nc == 0:
                continue
            if t.Type() == pcbnew.PCB_VIA_T:
                q = t.GetPosition()
                vias_by_nc[nc].append((int(q.x), int(q.y), int(t.GetWidth(pcbnew.F_Cu)) // 2))
            else:
                segs.append(t)

        def _anchored(pt: tuple[int, int], li: int, nc: int, self_seg) -> bool:
            px, py = pt
            # Touch a same-net via?
            for vx, vy, vr in vias_by_nc.get(nc, ()):
                if (px - vx) ** 2 + (py - vy) ** 2 <= (vr + 25_000) ** 2:
                    return True
            # Touch a same-net pad on this layer?
            pt_sh = (1, px, py, 20_000)
            for lays, psh in pads_by_nc.get(nc, ()):
                if li in lays and _shapes_touch_nm(pt_sh, psh):
                    return True
            return False

        removed = 0
        for _ in range(8):
            deg: dict[tuple[int, int, int, int], int] = defaultdict(int)
            for t in segs:
                li = self.lid_to_i.get(t.GetLayer())
                if li is None:
                    continue
                nc = int(t.GetNetCode())
                s, e = t.GetStart(), t.GetEnd()
                deg[(nc, li, int(s.x), int(s.y))] += 1
                deg[(nc, li, int(e.x), int(e.y))] += 1
            # Also check T-junction onto interior of another same-net segment!
            by_nl: dict[tuple[int, int], list] = defaultdict(list)
            for t in segs:
                li = self.lid_to_i.get(t.GetLayer())
                if li is not None:
                    by_nl[(int(t.GetNetCode()), li)].append(t)

            to_del = []
            for t in segs:
                li = self.lid_to_i.get(t.GetLayer())
                if li is None:
                    continue
                nc = int(t.GetNetCode())
                s, e = t.GetStart(), t.GetEnd()
                p1, p2 = (int(s.x), int(s.y)), (int(e.x), int(e.y))
                d1 = deg[(nc, li, p1[0], p1[1])]
                d2 = deg[(nc, li, p2[0], p2[1])]
                dangling = False
                for p, d in ((p1, d1), (p2, d2)):
                    if d > 1:
                        continue
                    if _anchored(p, li, nc, t):
                        continue
                    # Check if endpoint touches interior of any other segment on same net & layer
                    hits_other = False
                    for o in by_nl[(nc, li)]:
                        if o is t:
                            continue
                        os_, oe_ = o.GetStart(), o.GetEnd()
                        ow = int(o.GetWidth())
                        num, den = _geom.pt_seg_d2(p, (int(os_.x), int(os_.y)), (int(oe_.x), int(oe_.y)))
                        rad = ow // 2 + 20_000
                        if num <= rad * rad * den:
                            hits_other = True
                            break
                    if not hits_other:
                        dangling = True
                        break
                if dangling:
                    to_del.append(t)
            if not to_del:
                break
            del_set = set(id(x) for x in to_del)
            for t in to_del:
                self.board.Delete(t)
                removed += 1
            segs = [t for t in segs if id(t) not in del_set]
        return removed

    # ---- Main Solve
    def solve(self, target_nets: set[str] | None = None) -> dict:
        open_names = self.ingest_board(target_nets)
        if not open_names:
            return {"ok": True, "open_before": 0, "open_after": 0,
                    "segs_added": 0, "vias_added": 0, "seconds": round(time.time() - self.t0, 2)}

        # Phase 0: Coupled differential pairs FIRST (before BGA fanout & signals!)
        diff_closed = self.run_diff_pairs(open_names)
        rem_open_for_fanout = [nm for nm in open_names if nm not in diff_closed]

        # Phase 1: BGA fanout + plane stitching
        self.run_bga_fanout(rem_open_for_fanout)
        self.run_plane_stitching(rem_open_for_fanout)

        # Build exact islands and continuous access ports for every still-open net
        net_work = {}
        power_names = []
        for name in open_names:
            if name in diff_closed:
                continue
            nc = self.code_by_name.get(name)
            if not nc:
                continue
            items = self.net_items_map.get(nc, [])
            isls = _compute_net_islands(items)
            if len(isls) < 2:
                continue
            if len(isls) > 6:
                power_names.append((len(isls), name, nc))
                continue
            ports_list = [self._island_ports(nc, items, isl) for isl in isls]
            valid_ports = [p for p in ports_list if p]
            net_work[name] = {
                "nc": nc,
                "n_islands": len(isls),
                "ports": valid_ports,
            }

        # Phase 2: Fast Active-Cell 6-Layer PathFinder Negotiation (SIGNALS ONLY!)
        sig_nets = sorted(net_work.keys(),
                          key=lambda nm: (net_work[nm]["n_islands"], nm))
        routable_sigs = [nm for nm in sig_nets if len(net_work[nm]["ports"]) >= 2]
        routes = {}
        rng = random.Random(42)
        best_conflicts = 10 ** 9
        converged = False
        pf_iters_run = 0

        zero_c_streak = 0
        pf_deadline = self.t0 + self.time_budget * (0.45 if power_names else 0.65)
        for it in range(self.max_iters):
            if not routable_sigs or time.time() > pf_deadline:
                break
            pf_iters_run += 1
            pfac = 0.0 if it == 0 else min(32.0, 0.5 * (1.45 ** (it - 1)))
            if it == 0:
                to_route = list(routable_sigs)
            else:
                to_route = [nm for nm in routable_sigs
                            if (nm not in routes or not routes[nm]["complete"]
                                or self._route_conflicts(routes[nm]) > 0)]
                if not to_route:
                    converged = True
                    break
                for nm in to_route:
                    if nm in routes:
                        self._apply_route_occ(routes[nm], -1)
                        del routes[nm]
                rng.shuffle(to_route)

            for nm in to_route:
                if time.time() > pf_deadline:
                    break
                nw = net_work[nm]
                r = self._route_net_islands(nw["nc"], nw["ports"], pfac=pfac, strict=False)
                routes[nm] = r
                self._apply_route_occ(r, +1)

            # Fast sparse active-cell history update!
            active_t = set()
            active_v = set()
            for r in routes.values():
                active_t.update(r.get("_touched_t", ()))
                active_v.update(r.get("_touched_v", ()))
            over_t = over_v = 0
            for li, idx in active_t:
                o = self.occ[li][idx]
                if o > 1:
                    self.hist[li][idx] += 0.6 * (o - 1)
                    over_t += 1
            for idx in active_v:
                o = self.via_occ[idx]
                if o > 1:
                    self.via_hist[idx] += 0.6 * (o - 1)
                    over_v += 1
            tot_c = over_t + over_v
            if tot_c < best_conflicts:
                best_conflicts = tot_c
            if tot_c == 0 and all(routes.get(nm, {}).get("complete") for nm in routable_sigs):
                converged = True
                break
            if tot_c == 0:
                zero_c_streak += 1
                if zero_c_streak >= 2:
                    break
            else:
                zero_c_streak = 0

        # Phase 3: Reset soft arrays & commit conflict-free SIGNAL nets reversibly
        for li in range(self.nl):
            self.occ[li] = array.array("h", [0]) * self.nn
            self.hist[li] = array.array("f", [0.0]) * self.nn
        self.via_occ = array.array("h", [0]) * self.nn
        self.via_hist = array.array("f", [0.0]) * self.nn

        commit_order = sorted(
            sig_nets,
            key=lambda nm: (0 if routes.get(nm, {}).get("complete") else 1,
                            net_work[nm]["n_islands"], nm))
        failed_exact = []
        for nm in commit_order:
            nc = net_work[nm]["nc"]
            r = routes.get(nm)
            if r and r["chains"] and self._commit_net_reversible(nm, nc, r["chains"]):
                if len(_compute_net_islands(self.net_items_map[nc])) < 2:
                    continue
            failed_exact.append(nm)

        # Strict clean A* retry for any signal net that missed exact commit
        still_open = []
        sig_deadline = self.t0 + self.time_budget * (0.72 if power_names else 0.98)
        for nm in failed_exact:
            if time.time() > sig_deadline:
                still_open.append(nm)
                continue
            nc = net_work[nm]["nc"]
            if not self._strict_route_net(nm, nc, max_retries=3, append=(nm in self.committed_records)):
                still_open.append(nm)

        # Phase 4: Residual via rescue + multi-pass local blocker eviction on signals
        for p_idx in range(3):
            if not still_open or time.time() > sig_deadline:
                break
            next_open = []
            max_blk = 4 if p_idx == 0 else 6
            for nm in still_open:
                if time.time() > sig_deadline:
                    next_open.append(nm)
                    continue
                nc = net_work[nm]["nc"]
                if len(_compute_net_islands(self.net_items_map[nc])) < 2:
                    continue
                if self._rescue_island_via(nm, nc):
                    if self._strict_route_net(nm, nc, max_retries=3, append=(nm in self.committed_records)):
                        continue
                if self._phase4_rip_and_swap(nm, nc, max_blockers=max_blk):
                    continue
                self._greedy_island_polish(nm, nc)
                if len(_compute_net_islands(self.net_items_map[nc])) >= 2:
                    next_open.append(nm)
            if len(next_open) == len(still_open) and p_idx >= 1:
                break
            still_open = next_open

        # Phase 5: Incremental Power Tree Routing (5V, 3V3, GND — smallest first!)
        power_names.sort(key=lambda t: (t[0], t[1]))
        for _, nm, nc in power_names:
            if time.time() - self.t0 > self.time_budget:
                break
            self._route_power_net_incremental(nm, nc)

        # Phase 6: Exact-rasterize final cleanup pass!
        # Phases 2-5 used conservative PathFinder halos in occ/via_occ; rasterizing
        # committed copper at exact physical clearance into fixed_owner and clearing
        # occ/via_occ unlocks legal narrow corridors (every segment/via is still
        # strictly verified by ExactBoard in nanometers).
        p6_deadline = max(self.t0 + self.time_budget, time.time() + 18.0)
        rem_open = []
        for nc, items in self.net_items_map.items():
            if sum(1 for it in items if it[3]) < 2:
                continue
            nobj = self.net_by_code.get(nc)
            if not nobj:
                continue
            nm = nobj.GetNetname()
            if target_nets and nm not in target_nets:
                continue
            isls = _compute_net_islands(items)
            if len(isls) >= 2:
                rem_open.append((len(isls), nm, nc))
        if rem_open:
            for rec in self.committed_records.values():
                rnc = rec["nc"]
                for vx, vy in rec["via_pts"]:
                    sh = (1, vx, vy, self.via_r_nm)
                    self._rasterize_fixed_shape(self.all_li, sh, rnc, mark_via=True)
                    self._rasterize_fixed_hole(vx, vy, self.via_drill_nm)
                for li, x1, y1, x2, y2 in rec["segs"]:
                    if x1 == x2 and y1 == y2:
                        continue
                    sh = (0, x1, y1, x2, y2, self.track_w_nm)
                    self._rasterize_fixed_shape((li,), sh, rnc, mark_via=True)
                rec["touched_t_list"].clear()
                rec["touched_v_list"].clear()
            for li in range(self.nl):
                self.occ[li] = array.array("h", [0]) * self.nn
            self.via_occ = array.array("h", [0]) * self.nn
            rem_open.sort(key=lambda t: (t[0], t[1]))
            for _, nm, nc in rem_open:
                if time.time() > p6_deadline:
                    break
                prev_s = len(self.committed_records.get(nm, {}).get("segs", ()))
                prev_v = len(self.committed_records.get(nm, {}).get("via_pts", ()))
                cur_isls = _compute_net_islands(self.net_items_map[nc])
                if len(cur_isls) < 2:
                    continue
                if len(cur_isls) <= 6:
                    if not self._strict_route_net(nm, nc, max_retries=4, append=True):
                        if self._rescue_island_via(nm, nc):
                            self._strict_route_net(nm, nc, max_retries=4, append=True)
                    if len(_compute_net_islands(self.net_items_map[nc])) >= 2:
                        self._greedy_island_polish(nm, nc)
                else:
                    self._route_power_net_incremental(nm, nc)
                rec = self.committed_records.get(nm)
                if rec:
                    for vx, vy in rec["via_pts"][prev_v:]:
                        sh = (1, vx, vy, self.via_r_nm)
                        self._rasterize_fixed_shape(self.all_li, sh, nc, mark_via=True)
                        self._rasterize_fixed_hole(vx, vy, self.via_drill_nm)
                    for li, x1, y1, x2, y2 in rec["segs"][prev_s:]:
                        if x1 == x2 and y1 == y2:
                            continue
                        sh = (0, x1, y1, x2, y2, self.track_w_nm)
                        self._rasterize_fixed_shape((li,), sh, nc, mark_via=True)
                    for tt, tv in zip(rec["touched_t_list"], rec["touched_v_list"]):
                        self._apply_footprint(tt, tv, -1)
                    rec["touched_t_list"].clear()
                    rec["touched_v_list"].clear()

        # Phase 7: Exact-nm Power Trunk Widening + Local Neck-Down
        self.run_power_widening()

        # Final exact connectivity audit
        # Safe dangling track stub trim (removes only degree-1 dead-end segments not on pads/vias)
        self._trim_dangling_stubs()

        open_after = 0
        missing_after = 0
        for nc, items in self.net_items_map.items():
            if sum(1 for it in items if it[3]) < 2:
                continue
            isls = _compute_net_islands(items)
            if len(isls) >= 2:
                open_after += 1
                pad_counts = sorted((sum(1 for i in isl if items[i][3]) for isl in isls), reverse=True)
                missing_after += sum(pad_counts[1:])

        return {
            "ok": True,
            "mode": self.mode,
            "pf_iters": pf_iters_run,
            "converged": converged,
            "best_pf_conflicts": best_conflicts,
            "open_before": len(open_names),
            "open_after": open_after,
            "missing_pads_after": missing_after,
            "committed_nets": len(self.committed_records),
            "swaps_ok": self.swaps_ok,
            "diff_pairs": self.diff_pair_stats,
            "power_widened_segs": self.power_widened_segs,
            "plane_stitch_vias": self.plane_stitch_vias,
            "fanout_vias": self.fanout_vias,
            "fanout_segs": self.fanout_segs,
            "segs_added": self.added_segs,
            "vias_added": self.added_vias,
            "via_mm": [round(self.via_diam_nm / MM, 3), round(self.via_drill_nm / MM, 3)],
            "track_mm": round(self.track_w_nm / MM, 3),
            "clear_mm": round(self.clear_nm / MM, 3),
            "seconds": round(time.time() - self.t0, 2),
        }


def main(argv=None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if len(args) < 1:
        print(json.dumps({"ok": False, "error": "usage: route_pathfinder.py PCB [CFG.json]"}))
        return 2
    pcb_path = Path(args[0])
    cfg = {}
    if len(args) >= 2 and Path(args[1]).exists():
        cfg = json.loads(Path(args[1]).read_text(encoding="utf-8"))

    board = pcbnew.LoadBoard(str(pcb_path))
    if board is None:
        print(json.dumps({"ok": False, "error": f"LoadBoard failed: {pcb_path}"}))
        return 1

    router = SixLayerPathFinder(board, pcb_path, cfg)
    targets = set(cfg["target_nets"]) if cfg.get("target_nets") else None
    res = router.solve(target_nets=targets)

    from fluxpcb import repair_drc
    if board.GetAreaCount() > 0:
        try:
            pcbnew.ZONE_FILLER(board).Fill(board.Zones())
        except Exception:
            pass
    repair_drc.save_preserving_project(board, str(pcb_path))
    print(json.dumps(res, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
