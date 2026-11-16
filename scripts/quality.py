#!/usr/bin/env python3
"""Electrical quality of a routed board, measured on its copper (beyond completion and DRC).

    KICAD_PYTHON scripts/quality.py <board.kicad_pcb> [--json out.json]

Four measures, each computed the same way for every tool and for the human reference:

- return_path: every via on a high-speed net (a differential pair member, or a clock) and
  whether a ground via or ground through-hole pin sits within RETURN_VIA_MM of it; and the
  share of high-speed track length that runs over ground copper on an adjacent layer (filled
  zones or ground tracks under the track's centreline), and over any plane (a ground or power
  pour; a power plane returns the current through the decoupling capacitors).
- pairs: each differential pair's routed length mismatch (skew) and the share of its length
  routed side by side with its partner (centrelines within COUPLED_W track widths, same layer).
- rails: for each power net, the narrowest track on it (segments shorter than NECK_MM, the
  necks into pads, are left out) and whether the net is poured.
- decoupling: for each two-pad capacitor between ground and a power net, the shortest routed
  path (tracks and vias, not pours) from its power pad to the nearest pad on the same net of a
  part with at least 8 pads: copper length and via count. No path through tracks = reached
  through a pour (or not at all), reported separately.

Nets are classified by name; the patterns are below. Zones are refilled in memory first.
Nothing here scores a tool against its own idea of the board: the human design is measured
with the same code, and summary tables compare each tool with it.
"""
import heapq
import json
import math
import re
import sys

import pcbnew

RETURN_VIA_MM = 1.5          # ground via beside a high-speed layer change (CM5 IO reference: 103 of 104 within 1.5 mm)
COUPLED_W = 3.0              # side by side: partner centreline within 3 track widths
SAMPLE_MM = 0.25             # sampling step along tracks
NECK_MM = 0.5                # rail segments shorter than this are necks into pads
DECOUPLING_IC_PADS = 8       # a "larger part": at least this many pads
SNAP_MM = 0.002              # endpoints closer than this are the same node

GND_RX = re.compile(r"^/?(?:[ADP]?GND\w*|VSS\w*|0V)$", re.I)
POWER_RX = re.compile(r"^/?(?:\+[\w.]+|[\d.]+V\d*[\w.]*|V(?:CC|DD|BUS|IN|BAT|SYS|REF|IO|CORE)[\w.]*|\w*_\d+V\d*\w*|P\d+V\d+\w*)$", re.I)
CLOCK_RX = re.compile(r"(?:^|[_/\\-])(?:\w*CLK\w*|\w*CLOCK\w*)$", re.I)
PAIR_SUFFIXES = [("_P", "_N"), ("_DP", "_DM"), ("+", "-"), ("P", "N"), ("_T", "_C"), ("DP", "DM")]

mm = pcbnew.ToMM


def pair_partner(name):
    """The name of a net's differential partner by suffix, or None."""
    for p, n in PAIR_SUFFIXES:
        if name.endswith(p) and len(name) > len(p):
            return name[: -len(p)] + n, True
        if name.endswith(n) and len(name) > len(n):
            return name[: -len(n)] + p, False
    return None, None


def seg_dist(px, py, ax, ay, bx, by):
    vx, vy = bx - ax, by - ay
    l2 = vx * vx + vy * vy
    t = 0.0 if l2 == 0 else max(0.0, min(1.0, ((px - ax) * vx + (py - ay) * vy) / l2))
    return math.hypot(px - (ax + t * vx), py - (ay + t * vy))


def samples(t):
    s, e = t.GetStart(), t.GetEnd()
    ax, ay, bx, by = mm(s.x), mm(s.y), mm(e.x), mm(e.y)
    n = max(1, int(math.hypot(bx - ax, by - ay) / SAMPLE_MM))
    return [(ax + (bx - ax) * (i + 0.5) / n, ay + (by - ay) * (i + 0.5) / n, math.hypot(bx - ax, by - ay) / n) for i in range(n)]


def measure(path):
    b = pcbnew.LoadBoard(path)
    pcbnew.ZONE_FILLER(b).Fill(b.Zones())
    cu = list(b.GetEnabledLayers().CuStack())
    order = {l: i for i, l in enumerate(cu)}
    tracks = [t for t in b.GetTracks() if not isinstance(t, pcbnew.PCB_VIA)]
    vias = [t for t in b.GetTracks() if isinstance(t, pcbnew.PCB_VIA)]
    pads = [p for fp in b.GetFootprints() for p in fp.Pads()]
    nets = sorted({n for n in (str(k) for k in b.GetNetsByName().keys()) if n})
    netset = set(nets)
    gnd = {n for n in nets if GND_RX.match(n)}
    power = {n for n in nets if n not in gnd and POWER_RX.match(n)}
    pairs = {}
    for n in nets:
        partner, is_p = pair_partner(n)
        if partner and partner in netset and is_p and n not in power and n not in gnd:
            pairs[n] = partner
    hs = set(pairs) | set(pairs.values()) | {n for n in nets if CLOCK_RX.search(n) and n not in gnd and n not in power}
    by_net = {}
    for t in tracks:
        by_net.setdefault(t.GetNetname(), []).append(t)

    # -- return path -------------------------------------------------------------------
    gnd_points = [(mm(v.GetPosition().x), mm(v.GetPosition().y)) for v in vias if v.GetNetname() in gnd]
    gnd_points += [(mm(p.GetPosition().x), mm(p.GetPosition().y)) for p in pads
                   if p.GetNetname() in gnd and p.GetAttribute() == pcbnew.PAD_ATTRIB_PTH]
    hs_vias = [v for v in vias if v.GetNetname() in hs]
    with_return = 0
    for v in hs_vias:
        x, y = mm(v.GetPosition().x), mm(v.GetPosition().y)
        if any(math.hypot(x - gx, y - gy) <= RETURN_VIA_MM for gx, gy in gnd_points):
            with_return += 1
    gnd_fill, plane_fill = {}, {}
    for z in b.Zones():
        if z.GetIsRuleArea() or not (z.GetNetname() in gnd or z.GetNetname() in power):
            continue
        for l in cu:
            if z.IsOnLayer(l):
                plane_fill.setdefault(l, []).append(z.GetFilledPolysList(l))
                if z.GetNetname() in gnd:
                    gnd_fill.setdefault(l, []).append(z.GetFilledPolysList(l))
    gnd_tracks = {}
    for t in tracks:
        if t.GetNetname() in gnd:
            gnd_tracks.setdefault(t.GetLayer(), []).append(t)

    def under(x, y, layer, fills, with_tracks):
        """Copper of the given kind on a layer adjacent to `layer`, under (x, y)."""
        i = order.get(layer)
        if i is None:
            return False
        p = pcbnew.VECTOR2I(pcbnew.FromMM(x), pcbnew.FromMM(y))
        for j in (i - 1, i + 1):
            if j < 0 or j >= len(cu):
                continue
            l2 = cu[j]
            if any(poly.Contains(p) for poly in fills.get(l2, [])):
                return True
            if with_tracks and any(g.HitTest(p, 0) for g in gnd_tracks.get(l2, [])):
                return True
        return False

    hs_len = hs_gnd = hs_plane = 0.0
    for n in hs:
        for t in by_net.get(n, []):
            for x, y, w in samples(t):
                hs_len += w
                if under(x, y, t.GetLayer(), gnd_fill, True):
                    hs_gnd += w
                    hs_plane += w
                elif under(x, y, t.GetLayer(), plane_fill, False):
                    hs_plane += w

    # -- pairs ---------------------------------------------------------------------------
    pair_rows = []
    for p, n in sorted(pairs.items()):
        tp, tn = by_net.get(p, []), by_net.get(n, [])
        lp = sum(mm(t.GetLength()) for t in tp)
        ln = sum(mm(t.GetLength()) for t in tn)
        if not tp or not tn:
            pair_rows.append({"pair": p, "routed": False})
            continue
        coupled = total = 0.0
        for t in tp:
            w = mm(t.GetWidth())
            for x, y, d in samples(t):
                total += d
                for u in tn:
                    if u.GetLayer() != t.GetLayer():
                        continue
                    s, e = u.GetStart(), u.GetEnd()
                    if seg_dist(x, y, mm(s.x), mm(s.y), mm(e.x), mm(e.y)) <= COUPLED_W * w:
                        coupled += d
                        break
        pair_rows.append({"pair": p, "routed": True, "skew_mm": round(abs(lp - ln), 3),
                          "coupled_pct": round(100 * coupled / total, 1) if total else None})

    # -- rails ---------------------------------------------------------------------------
    poured = {z.GetNetname() for z in b.Zones() if not z.GetIsRuleArea()}
    rail_rows = []
    for n in sorted(power):
        ws = [mm(t.GetWidth()) for t in by_net.get(n, []) if mm(t.GetLength()) >= NECK_MM]
        rail_rows.append({"net": n, "narrowest_mm": round(min(ws), 3) if ws else None,
                          "track_mm": round(sum(mm(t.GetLength()) for t in by_net.get(n, [])), 1), "poured": n in poured})

    # -- decoupling ----------------------------------------------------------------------
    big = {fp.GetReference() for fp in b.GetFootprints() if len(list(fp.Pads())) >= DECOUPLING_IC_PADS}
    caps = []
    for fp in b.GetFootprints():
        ps = list(fp.Pads())
        if not re.match(r"^C\d", fp.GetReference()) or len(ps) != 2:
            continue
        ns = [p.GetNetname() for p in ps]
        if sum(n in gnd for n in ns) == 1 and any(n in power for n in ns):
            caps.append([p for p in ps if p.GetNetname() in power][0])
    dec_rows = [decoupling_path(c, pads, by_net, vias, big) for c in caps]

    reached = [r for r in dec_rows if r["path_mm"] is not None]
    return {
        "board": path,
        "return_path": {"hs_nets": len(hs), "hs_vias": len(hs_vias), "hs_vias_with_ground_via": with_return,
                        "hs_vias_with_ground_via_pct": round(100 * with_return / len(hs_vias), 1) if hs_vias else None,
                        "hs_track_mm": round(hs_len, 1),
                        "hs_over_ground_pct": round(100 * hs_gnd / hs_len, 1) if hs_len else None,
                        "hs_over_plane_pct": round(100 * hs_plane / hs_len, 1) if hs_len else None},
        "pairs": {"count": len(pair_rows), "routed": sum(r["routed"] for r in pair_rows),
                  "median_skew_mm": median([r["skew_mm"] for r in pair_rows if r["routed"]]),
                  "median_coupled_pct": median([r["coupled_pct"] for r in pair_rows if r.get("coupled_pct") is not None]),
                  "rows": pair_rows},
        "rails": {"count": len(rail_rows), "rows": rail_rows},
        "decoupling": {"caps": len(dec_rows), "by_tracks": len(reached),
                       "median_path_mm": median([r["path_mm"] for r in reached]),
                       "median_vias": median([r["vias"] for r in reached]),
                       "rows": dec_rows},
    }


def decoupling_path(cap_pad, pads, by_net, vias, big):
    """Shortest track-and-via path from a capacitor's power pad to the nearest pad on its net of a
    larger part: Dijkstra over track endpoints (snapped), pads joined to the endpoints they contain,
    vias joining every layer at their position."""
    net = cap_pad.GetNetname()
    targets = [p for p in pads if p.GetNetname() == net and p.GetParentFootprint().GetReference() in big]
    row = {"cap": cap_pad.GetParentFootprint().GetReference(), "net": net, "path_mm": None, "vias": None}
    if not targets:
        row["note"] = "no larger part on its net"
        return row
    key = lambda x, y, l: (round(x / SNAP_MM), round(y / SNAP_MM), l)
    adj = {}

    def link(a, b, w, via):
        adj.setdefault(a, []).append((b, w, via))
        adj.setdefault(b, []).append((a, w, via))

    ts = by_net.get(net, [])
    ends = []
    for t in ts:
        s, e = t.GetStart(), t.GetEnd()
        a, c = key(mm(s.x), mm(s.y), t.GetLayer()), key(mm(e.x), mm(e.y), t.GetLayer())
        link(a, c, mm(t.GetLength()), 0)
        ends += [(a, s, t.GetLayer()), (c, e, t.GetLayer())]
    for v in vias:
        if v.GetNetname() != net:
            continue
        pos = v.GetPosition()
        here = [k for (k, p, l) in ends if v.HitTest(p, 0)]
        for i in range(len(here)):
            for j in range(i + 1, len(here)):
                link(here[i], here[j], 0.0, 1 if here[i][2] != here[j][2] else 0)
    def pad_node(p):
        return ("pad", p.GetParentFootprint().GetReference(), p.GetNumber())
    for p in [cap_pad] + targets:
        for (k, pt, l) in ends:
            if p.IsOnLayer(l) and p.HitTest(pt):
                link(pad_node(p), k, 0.0, 0)
    goal = {pad_node(p) for p in targets}
    start = pad_node(cap_pad)
    best = {start: (0.0, 0)}
    heap = [(0.0, 0, 0, start)]
    tick = 0
    while heap:
        d, nv, _, u = heapq.heappop(heap)
        if u in goal:
            row["path_mm"], row["vias"], row["to"] = round(d, 2), nv, f"{u[1]}.{u[2]}"
            return row
        if best.get(u, (math.inf, 0))[0] < d:
            continue
        for (w_, length, via) in adj.get(u, []):
            nd = d + length
            if nd < best.get(w_, (math.inf, 0))[0]:
                best[w_] = (nd, nv + via)
                tick += 1
                heapq.heappush(heap, (nd, nv + via, tick, w_))
    row["note"] = "no track path (through a pour, or not connected)"
    return row


def median(xs):
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return None
    m = len(xs) // 2
    return xs[m] if len(xs) % 2 else round((xs[m - 1] + xs[m]) / 2, 3)


if __name__ == "__main__":
    r = measure(sys.argv[1])
    if "--json" in sys.argv:
        with open(sys.argv[sys.argv.index("--json") + 1], "w") as f:
            json.dump(r, f, indent=1)
    brief = {k: ({kk: vv for kk, vv in v.items() if kk != "rows"} if isinstance(v, dict) else v) for k, v in r.items()}
    print(json.dumps(brief))
