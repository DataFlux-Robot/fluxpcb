#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bga_escape_probe — pcbnew-free BGA escape feasibility prover.

Answers one question with evidence instead of opinion: *can every used ball
of this BGA be fanned out and escaped to the edge of the ball field without
violating the given manufacturing rules?*

It reads a .kicad_pcb as plain text (no KiCad needed), then

  1. census  — classify every ball (signal / power / unused) per ring;
  2. power   — give every power ball its own dog-bone via (bipartite
               matching onto the 4-ball centres, outward centre first);
  3. escape  — solve the signal escape as ONE min-cost max-flow on a
               pitch-aligned channel lattice (not a uniform grid):
                 * top layer  : tracks run on the lines midway between ball
                                rows/columns; balls attach by a 45 deg stub;
                 * lower layer: tracks run on the lines directly under the
                                ball rows/columns (midway between via
                                sites); vias attach by a 45 deg stub;
               every lattice node carries at most one net, so the emitted
               geometry is clearance-clean by construction;
  4. verify  — re-check ALL emitted copper with an independent brute-force
               exact geometry checker (segment/circle distances), so the
               "by construction" claim is not taken on faith.

Scope: the ball field plus one pitch around it. Existing tracks on the
board are ignored on purpose — this measures what the package and the
rules allow, not what a previous routing attempt left behind.

Usage:
  python tools/bga_escape_probe.py BOARD.kicad_pcb [--ref U1] [--layers 1]
         [--track 0.10] [--clearance 0.10] [--via 0.45 0.20]
         [--mode any|facing] [--svg out.svg] [--json out.json]

Exit status 0 = every used ball escaped, every power ball has a via and
the exact check found no violation; 1 otherwise.
"""
from __future__ import annotations

import argparse
import itertools
import json
import math
import re
import sys
from collections import Counter, defaultdict, deque
from dataclasses import dataclass, field
from pathlib import Path

# --------------------------------------------------------------- s-expr
_TOK = re.compile(r'\(|\)|"(?:\\.|[^"\\])*"|[^\s()]+')


def _parse(text: str):
    stack = [[]]
    for m in _TOK.finditer(text):
        t = m.group(0)
        if t == "(":
            new = []
            stack[-1].append(new)
            stack.append(new)
        elif t == ")":
            stack.pop()
        elif t[0] == '"':
            stack[-1].append(("s", t[1:-1]))
        else:
            stack[-1].append(t)
    return stack[0][0]


def _val(x):
    return x[1] if isinstance(x, tuple) else x


def _kid(n, name):
    for c in n:
        if isinstance(c, list) and c and c[0] == name:
            return c
    return None


def _kids(n, name):
    return [c for c in n if isinstance(c, list) and c and c[0] == name]


def load_footprints(path) -> list[dict]:
    """[{ref, x, y, rot, pads: [{name, x, y, w, h, shape, net}]}]"""
    tree = _parse(Path(path).read_text(encoding="utf-8"))
    out = []
    for fp in _kids(tree, "footprint"):
        at = _kid(fp, "at")
        fx, fy = float(_val(at[1])), float(_val(at[2]))
        fr = float(_val(at[3])) if len(at) > 3 else 0.0
        ref = ""
        for p in _kids(fp, "property"):
            if _val(p[1]) == "Reference":
                ref = _val(p[2])
        ca, sa = math.cos(math.radians(fr)), math.sin(math.radians(fr))
        pads = []
        for pd in _kids(fp, "pad"):
            pat, sz, net = _kid(pd, "at"), _kid(pd, "size"), _kid(pd, "net")
            lx, ly = float(_val(pat[1])), float(_val(pat[2]))
            pads.append({
                "name": _val(pd[1]), "shape": pd[3],
                "x": fx + lx * ca + ly * sa, "y": fy - lx * sa + ly * ca,
                "w": float(_val(sz[1])), "h": float(_val(sz[2])),
                "net": _val(net[-1]) if net is not None else None})
        out.append({"ref": ref, "x": fx, "y": fy, "rot": fr, "pads": pads})
    return out


# --------------------------------------------------------------- model
POWER_RE = re.compile(r"^(GND|AGND|DGND|VSS\w*|VDD\w*|VCC\w*|VCAP\w*|VBAT|"
                      r"VREF\S*|\+?\d+V\d*)$", re.I)


@dataclass
class Rules:
    track_w: float = 0.10       # mm, signal escape track
    clearance: float = 0.10     # mm, copper to copper (JLC limit: 0.09)
    via_pad: float = 0.45       # mm, dog-bone via land diameter
    via_drill: float = 0.20     # mm
    hole_to_hole: float = 0.25  # mm, drill wall to drill wall
    power_stub_w: float = 0.15  # mm


@dataclass
class Bga:
    ref: str
    nx: int
    ny: int
    pitch: float
    pad_d: float
    x0: float                   # board mm of the column-0 / row-0 ball
    y0: float
    balls: dict = field(default_factory=dict)   # (i, j) -> {name, net, kind}
    dest: dict = field(default_factory=dict)    # net -> centroid (x, y) of
                                                # its pads outside the BGA

    def xy(self, u: int, v: int) -> tuple[float, float]:
        """Board mm of a half-pitch lattice coordinate (ball i -> u = 2i)."""
        return (self.x0 + u * self.pitch / 2, self.y0 + v * self.pitch / 2)

    def ring(self, i: int, j: int) -> int:
        """0 = outermost row."""
        return min(i, j, self.nx - 1 - i, self.ny - 1 - j)


def build_bga(fps: list[dict], ref: str | None = None) -> Bga:
    if ref:
        cands = [f for f in fps if f["ref"] == ref]
    else:
        cands = [f for f in fps if len(f["pads"]) >= 36
                 and all(p["shape"] == "circle" for p in f["pads"])]
    if not cands:
        raise SystemExit("no BGA-like footprint found (use --ref)")
    fp = max(cands, key=lambda f: len(f["pads"]))
    if abs(fp["rot"] % 90) > 1e-6:
        raise SystemExit("footprint rotation must be a multiple of 90 deg")
    xs = sorted({round(p["x"], 4) for p in fp["pads"]})
    ys = sorted({round(p["y"], 4) for p in fp["pads"]})
    pitch = round(min(b - a for a, b in zip(xs, xs[1:])), 4)
    nx = round((xs[-1] - xs[0]) / pitch) + 1
    ny = round((ys[-1] - ys[0]) / pitch) + 1
    npads = Counter()
    where = defaultdict(list)
    for f in fps:
        for p in f["pads"]:
            if p["net"]:
                npads[p["net"]] += 1
                if f is not fp:
                    where[p["net"]].append((p["x"], p["y"]))
    bga = Bga(fp["ref"], nx, ny, pitch, max(p["w"] for p in fp["pads"]),
              xs[0], ys[0])
    for p in fp["pads"]:
        i = round((p["x"] - xs[0]) / pitch)
        j = round((p["y"] - ys[0]) / pitch)
        net = p["net"]
        if not net or net.startswith("unconnected-"):
            kind, net = "unused", None
        elif POWER_RE.match(net):
            kind = "power"
        elif npads[net] < 2:
            kind = "dangling"       # named net with one pad: nothing to
        else:                       # reach, the ball is an obstacle only
            kind = "signal"
        bga.balls[(i, j)] = {"name": p["name"], "net": net, "kind": kind}
    for net, pts in where.items():
        bga.dest[net] = (sum(x for x, _ in pts) / len(pts),
                         sum(y for _, y in pts) / len(pts))
    return bga


def census(bga: Bga) -> dict:
    rings = defaultdict(Counter)
    for (i, j), b in bga.balls.items():
        rings[bga.ring(i, j)][b["kind"]] += 1
    return {"balls": len(bga.balls),
            "by_kind": dict(Counter(b["kind"] for b in bga.balls.values())),
            "by_ring": {r: dict(c) for r, c in sorted(rings.items())}}


def lattice_margins(bga: Bga, r: Rules) -> dict:
    """Closed-form worst-case copper gaps of the channel lattice (mm).
    A value below Rules.clearance means the rule set cannot use it."""
    p, diag = bga.pitch, bga.pitch / math.sqrt(2)
    return {
        "top_track_between_pads": p / 2 - bga.pad_d / 2 - r.track_w / 2,
        "dogbone_via_to_pad": diag - bga.pad_d / 2 - r.via_pad / 2,
        "track_between_vias": p / 2 - r.via_pad / 2 - r.track_w / 2,
        "via_to_via": p - r.via_pad,
        "hole_to_hole": p - r.via_drill,
        "tracks_per_top_channel": int(
            (p - bga.pad_d - r.clearance + 1e-9) // (r.track_w + r.clearance)),
    }


# --------------------------------------------------------------- min-cost flow
class MCMF:
    """Successive-shortest-path min-cost max-flow (SPFA), unit capacities."""

    def __init__(self):
        self.g: list[list[int]] = []
        self.to: list[int] = []
        self.cap: list[int] = []
        self.cost: list[int] = []

    def node(self) -> int:
        self.g.append([])
        return len(self.g) - 1

    def edge(self, a: int, b: int, cap: int, cost: int) -> int:
        self.g[a].append(len(self.to))
        self.to.append(b), self.cap.append(cap), self.cost.append(cost)
        self.g[b].append(len(self.to))
        self.to.append(a), self.cap.append(0), self.cost.append(-cost)
        return len(self.to) - 2

    def run(self, s: int, t: int) -> tuple[int, int]:
        flow = cost = 0
        n = len(self.g)
        while True:
            dist = [math.inf] * n
            inq = [False] * n
            prev = [-1] * n
            dist[s] = 0
            dq = deque([s])
            while dq:
                a = dq.popleft()
                inq[a] = False
                for e in self.g[a]:
                    b = self.to[e]
                    if self.cap[e] > 0 and dist[a] + self.cost[e] < dist[b]:
                        dist[b] = dist[a] + self.cost[e]
                        prev[b] = e
                        if not inq[b]:
                            inq[b] = True
                            dq.append(b)
            if prev[t] < 0:
                return flow, cost
            b = t
            while b != s:
                e = prev[b]
                self.cap[e] -= 1
                self.cap[e ^ 1] += 1
                b = self.to[e ^ 1]
            flow += 1
            cost += dist[t]


# --------------------------------------------------------------- escape
C_STUB, C_STEP, C_VIA = 2, 4, 6     # integer costs (stub 0.57mm, step 0.8mm)
SIDES = ("N", "E", "S", "W")
_DIAG = ((1, 1), (1, -1), (-1, 1), (-1, -1))
_ORTH = ((2, 0), (-2, 0), (0, 2), (0, -2))


def facing_side(bga: Bga, net: str) -> str | None:
    """Side of the ball field that faces the net's pads outside the BGA."""
    if net not in bga.dest:
        return None
    cx = bga.x0 + (bga.nx - 1) * bga.pitch / 2
    cy = bga.y0 + (bga.ny - 1) * bga.pitch / 2
    dx, dy = bga.dest[net][0] - cx, bga.dest[net][1] - cy
    if abs(dx) >= abs(dy):
        return "E" if dx > 0 else "W"
    return "S" if dy > 0 else "N"


class Escape:
    """Channel-lattice state: occupied nodes, vias and the emitted paths.

    Coordinates are half-pitch integers: ball (i, j) sits at (2i, 2j);
    top-layer lattice nodes (= the 4-ball centres, where dog-bone vias go)
    have odd coordinates; lower-layer lattice nodes have even coordinates.
    """

    def __init__(self, bga: Bga, n_lower: int):
        self.bga, self.n_lower = bga, n_lower
        U, V = 2 * bga.nx, 2 * bga.ny
        self.top = [(u, v) for u in range(-1, U, 2) for v in range(-1, V, 2)]
        self.low = [(u, v) for u in range(-2, U + 1, 2)
                    for v in range(-2, V + 1, 2)]
        self.used_top: dict = {}        # (u, v) -> net
        self.used_low: dict = {}        # (L, u, v) -> net
        self.direct: dict = {}          # ball -> side (straight outer-row exit)
        self.paths: dict = {}           # ball -> [node, ...]
        self.vias: dict = {}            # (u, v) -> net (through vias)
        self.power: dict = {}           # power ball -> its via site

    def top_sides(self, u, v) -> set:
        b = self.bga
        return {s for s, ok in (("N", v == -1), ("S", v == 2 * b.ny - 1),
                                ("W", u == -1), ("E", u == 2 * b.nx - 1)) if ok}

    def low_sides(self, u, v) -> set:
        b = self.bga
        return {s for s, ok in (("N", v == -2), ("S", v == 2 * b.ny),
                                ("W", u == -2), ("E", u == 2 * b.nx)) if ok}

    def direct_side(self, i, j) -> str | None:
        """Outer-row, non-corner balls may leave straight outwards."""
        b = self.bga
        sides = [s for s, ok in (("N", j == 0), ("S", j == b.ny - 1),
                                 ("W", i == 0), ("E", i == b.nx - 1)) if ok]
        return sides[0] if len(sides) == 1 else None

    # -- power: one dog-bone via per power ball (bipartite matching)
    def place_power(self, only: list | None = None) -> list:
        """Match power balls to free 4-ball centres, outward centre first.
        Returns the balls that found none."""
        b = self.bga
        pw = [ij for ij, x in b.balls.items()
              if x["kind"] == "power" and ij not in self.power
              and (only is None or ij in only)]
        cx, cy = (b.nx - 1) / 2, (b.ny - 1) / 2

        def sites(ij):
            i, j = ij
            sx, sy = (1 if i > cx else -1), (1 if j > cy else -1)
            order = ((sx, sy), (sx, -sy), (-sx, sy), (-sx, -sy))
            return [(2 * i + du, 2 * j + dv) for du, dv in order
                    if (2 * i + du, 2 * j + dv) not in self.used_top]

        match: dict = {}                    # site -> ball

        def augment(ij, seen):
            for s in sites(ij):
                if s in seen:
                    continue
                seen.add(s)
                if s not in match or augment(match[s], seen):
                    match[s] = ij
                    return True
            return False

        unmatched = [ij for ij in sorted(pw, key=lambda q: (b.ring(*q), q))
                     if not augment(ij, set())]
        for s, ij in match.items():
            self.power[ij] = s
            self.used_top[s] = self.vias[s] = b.balls[ij]["net"]
        return unmatched

    # -- signals: one min-cost max-flow over the free lattice
    def solve(self, balls: list, sides: set | None = None) -> int:
        """Route `balls` to exits on `sides` (None = any). Returns #routed."""
        b = self.bga
        f = MCMF()
        S, T = f.node(), f.node()
        tin, tout, lin, lout = {}, {}, {}, {}
        for n in self.top:
            if n not in self.used_top:
                tin[n], tout[n] = f.node(), f.node()
                f.edge(tin[n], tout[n], 1, 0)       # node capacity 1
        for L in range(self.n_lower):
            for (u, v) in self.low:
                k = (L, u, v)
                if k not in self.used_low:
                    lin[k], lout[k] = f.node(), f.node()
                    f.edge(lin[k], lout[k], 1, 0)
        want = sides or set(SIDES)
        for n in tin:
            u, v = n
            ex = self.top_sides(u, v)
            if ex:                      # boundary node: leave, never run along
                if ex & want:
                    f.edge(tout[n], T, 1, 0)
            else:
                for du, dv in _ORTH:
                    m = (u + du, v + dv)
                    if m in tin:
                        f.edge(tout[n], tin[m], 1, C_STEP)
            for L in range(self.n_lower):       # through via + 45 deg stub
                for du, dv in _DIAG:
                    k = (L, u + du, v + dv)
                    if k in lin:
                        f.edge(tout[n], lin[k], 1, C_VIA + C_STUB + L)
        for k in lin:
            L, u, v = k
            ex = self.low_sides(u, v)
            if ex:
                if ex & want:
                    f.edge(lout[k], T, 1, 0)
            else:
                for du, dv in _ORTH:
                    m = (L, u + du, v + dv)
                    if m in lin:
                        f.edge(lout[k], lin[m], 1, C_STEP)
        src, straight = {}, {}
        for ij in balls:
            i, j = ij
            src[ij] = f.node()
            f.edge(S, src[ij], 1, 0)
            side = self.direct_side(i, j)
            if side and side in want:
                straight[ij] = f.edge(src[ij], T, 1, 0)
            for du, dv in _DIAG:
                n = (2 * i + du, 2 * j + dv)
                if n in tin:
                    f.edge(src[ij], tin[n], 1, C_STUB)
        flow, _ = f.run(S, T)
        # decompose: node capacities are 1, so every source's walk is unique
        label = {tout[n]: ("top",) + n for n in tout}
        label.update({lout[k]: ("low",) + k for k in lout})
        for ij, x in src.items():
            net = b.balls[ij]["net"]
            if ij in straight and f.cap[straight[ij]] == 0:
                self.direct[ij] = self.direct_side(*ij)
                self.paths[ij] = []
                continue
            path, cur = [], x
            while cur != T:
                e = next((e for e in f.g[cur] if e % 2 == 0
                          and f.cap[e] == 0 and f.cap[e ^ 1] == 1), None)
                if e is None:
                    break
                f.cap[e ^ 1] = 0            # consume this unit
                cur = f.to[e]
                if cur in label:
                    path.append(label[cur])
            if cur != T:
                continue                    # this ball got no flow
            self.paths[ij] = path
            for k, node in enumerate(path):
                if node[0] == "top":
                    self.used_top[node[1:]] = net
                    if k + 1 < len(path) and path[k + 1][0] == "low":
                        self.vias[node[1:]] = net
                else:
                    self.used_low[node[1:]] = net
        return flow


def _route_signals(esc: Escape, sig: list, mode: str) -> None:
    b = esc.bga
    if mode == "any":
        esc.solve(sig)
        return
    groups = defaultdict(list)
    for ij in sig:
        groups[facing_side(b, b.balls[ij]["net"]) or "N"].append(ij)
    for s in sorted((s for s in SIDES if groups[s]),
                    key=lambda s: -len(groups[s])):     # busiest side first
        esc.solve(groups[s], {s})


def plan(bga: Bga, n_lower: int = 1, mode: str = "any") -> Escape:
    """Fan out the way a designer does: power dog-bones first (each only
    needs the nearest free centre), then ONE flow for all signals. If that
    strands something, retry signals-first, reserving a centre only for the
    power balls that end up without one. Keeps the best attempt."""
    sig = [ij for ij, x in bga.balls.items() if x["kind"] == "signal"]

    def score(esc):
        esc.unrouted = [ij for ij in sig if ij not in esc.paths]
        return (len(esc.unrouted) + len(esc.unmatched_power),
                len(esc.unrouted))

    esc = Escape(bga, n_lower)
    esc.unmatched_power = esc.place_power()
    _route_signals(esc, sig, mode)
    best = (score(esc), esc)
    reserved: list = []
    for _ in range(40):
        if best[0][0] == 0:
            break
        esc = Escape(bga, n_lower)
        stuck = esc.place_power(only=reserved) if reserved else []
        _route_signals(esc, sig, mode)
        esc.unmatched_power = stuck + esc.place_power()
        sc = score(esc)
        if sc < best[0]:
            best = (sc, esc)
        new = [ij for ij in esc.unmatched_power if ij not in reserved]
        if not new:
            break
        reserved += new
    return best[1]


# --------------------------------------------------------------- geometry
_OUT = {"N": (0, -1), "S": (0, 1), "W": (-1, 0), "E": (1, 0)}


def _layer_name(L: int) -> str:
    return "F.Cu" if L < 0 else "ESC%d" % (L + 1)


def emit(esc: Escape, r: Rules) -> dict:
    """Paths -> copper. tracks: (layer, net, a, b, half_width);
    vias: (net, centre) through all layers; pads: (net, centre) on F.Cu."""
    b = esc.bga
    tracks, hw = [], r.track_w / 2
    for ij, path in esc.paths.items():
        net = b.balls[ij]["net"]
        i, j = ij
        if ij in esc.direct:
            dx, dy = _OUT[esc.direct[ij]]
            tracks.append(("F.Cu", net, b.xy(2 * i, 2 * j),
                           b.xy(2 * i + 3 * dx, 2 * j + 3 * dy), hw))
            continue
        cur, layer = (2 * i, 2 * j), -1
        for node in path:
            nxt, layer = (node[1:], -1) if node[0] == "top" \
                else (node[2:], node[1])
            tracks.append((_layer_name(layer), net, b.xy(*cur), b.xy(*nxt), hw))
            cur = nxt
        sides = esc.top_sides(*cur) if layer < 0 else esc.low_sides(*cur)
        dx = sum(_OUT[s][0] for s in sides)
        dy = sum(_OUT[s][1] for s in sides)
        tracks.append((_layer_name(layer), net, b.xy(*cur),
                       b.xy(cur[0] + 2 * dx, cur[1] + 2 * dy), hw))
    for ij, s in esc.power.items():
        tracks.append(("F.Cu", b.balls[ij]["net"], b.xy(2 * ij[0], 2 * ij[1]),
                       b.xy(*s), r.power_stub_w / 2))
    vias = [(net, b.xy(*s)) for s, net in esc.vias.items()]
    pads = [(x["net"] or "~" + x["name"], b.xy(2 * i, 2 * j))
            for (i, j), x in b.balls.items()]
    return {"tracks": tracks, "vias": vias, "pads": pads}


def _pt_seg(p, a, c):
    dx, dy = c[0] - a[0], c[1] - a[1]
    l2 = dx * dx + dy * dy
    if l2 == 0:
        return math.hypot(p[0] - a[0], p[1] - a[1])
    t = max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / l2))
    return math.hypot(p[0] - a[0] - t * dx, p[1] - a[1] - t * dy)


def _seg_seg(a1, a2, b1, b2):
    d = (a2[0] - a1[0]) * (b2[1] - b1[1]) - (a2[1] - a1[1]) * (b2[0] - b1[0])
    if abs(d) > 1e-12:
        t = ((b1[0] - a1[0]) * (b2[1] - b1[1])
             - (b1[1] - a1[1]) * (b2[0] - b1[0])) / d
        u = ((b1[0] - a1[0]) * (a2[1] - a1[1])
             - (b1[1] - a1[1]) * (a2[0] - a1[0])) / d
        if 0 <= t <= 1 and 0 <= u <= 1:
            return 0.0
    return min(_pt_seg(a1, b1, b2), _pt_seg(a2, b1, b2),
               _pt_seg(b1, a1, a2), _pt_seg(b2, a1, a2))


def verify(cu: dict, bga: Bga, r: Rules) -> dict:
    """Independent exact check of every different-net copper pair. Knows
    nothing about the lattice: plain segment / circle distances."""
    kinds = ("track-track", "track-pad", "track-via", "via-pad", "via-via",
             "hole-hole")
    worst = dict.fromkeys(kinds, math.inf)
    bad = []

    def note(kind, gap, what):
        worst[kind] = min(worst[kind], gap)
        need = r.hole_to_hole if kind == "hole-hole" else r.clearance
        if gap < need - 1e-9:
            bad.append({"kind": kind, "gap_mm": round(gap, 4), "between": what})

    tr, vias, pads = cu["tracks"], cu["vias"], cu["pads"]
    pr, vr = bga.pad_d / 2, r.via_pad / 2
    for a, c in itertools.combinations(tr, 2):
        if a[0] == c[0] and a[1] != c[1]:
            note("track-track",
                 _seg_seg(a[2], a[3], c[2], c[3]) - a[4] - c[4],
                 [a[1], c[1], a[0]])
    for t in tr:
        if t[0] == "F.Cu":
            for net, c in pads:
                if net != t[1]:
                    note("track-pad", _pt_seg(c, t[2], t[3]) - pr - t[4],
                         [t[1], net])
        for net, c in vias:                 # through vias exist on all layers
            if net != t[1]:
                note("track-via", _pt_seg(c, t[2], t[3]) - vr - t[4],
                     [t[1], net, t[0]])
    for vn, vc in vias:
        for net, c in pads:
            if net != vn:
                note("via-pad", math.dist(vc, c) - pr - vr, [vn, net])
    for (n1, c1), (n2, c2) in itertools.combinations(vias, 2):
        note("hole-hole", math.dist(c1, c2) - r.via_drill, [n1, n2])
        if n1 != n2:
            note("via-via", math.dist(c1, c2) - 2 * vr, [n1, n2])
    return {"violations": bad,
            "min_gap_mm": {k: (None if v == math.inf else round(v, 4))
                           for k, v in worst.items()}}


# --------------------------------------------------------------- report
def summarize(bga: Bga, esc: Escape, r: Rules, check: dict) -> dict:
    sig = [ij for ij, x in bga.balls.items() if x["kind"] == "signal"]
    how, exits = Counter(), Counter()
    for ij in sig:
        if ij not in esc.paths:
            how["unrouted"] += 1
        elif ij in esc.direct:
            how["top_straight_out"] += 1
            exits[esc.direct[ij]] += 1
        else:
            last = esc.paths[ij][-1]
            if last[0] == "top":
                how["top_between_pads"] += 1
                sides = esc.top_sides(*last[1:])
            else:
                how["via_to_lower_layer_%d" % (last[1] + 1)] += 1
                sides = esc.low_sides(*last[2:])
            exits["".join(sorted(sides))] += 1
    pw = sum(1 for x in bga.balls.values() if x["kind"] == "power")
    sig_vias = sum(1 for ij in sig
                   if any(n[0] == "low" for n in esc.paths.get(ij, [])))
    return {
        "bga": {"ref": bga.ref, "grid": [bga.nx, bga.ny],
                "pitch_mm": bga.pitch, "pad_mm": bga.pad_d},
        "rules": dict(r.__dict__),
        "census": census(bga),
        "lattice_margins_mm": {k: round(v, 4) if isinstance(v, float) else v
                               for k, v in lattice_margins(bga, r).items()},
        "signals": len(sig), "escaped": len(sig) - how["unrouted"],
        "how": dict(how), "exit_sides": dict(exits),
        "unrouted": sorted(bga.balls[ij]["name"] for ij in esc.unrouted),
        "power_balls": pw,
        "power_with_own_via": pw - len(esc.unmatched_power),
        "power_without_via": sorted(bga.balls[ij]["name"]
                                    for ij in esc.unmatched_power),
        "vias": {"signal": sig_vias, "power": len(esc.power),
                 "centres_total": len(esc.top),
                 "centres_used_pct": round(100 * len(esc.vias) / len(esc.top), 1)},
        "exact_check": check,
        "ok": (how["unrouted"] == 0 and not esc.unmatched_power
               and not check["violations"]),
    }


def render_svg(bga: Bga, cu: dict, r: Rules, path) -> None:
    """One panel per layer, mm units, for eyeballing the result."""
    b = bga
    m = 2.2 * b.pitch
    w = (b.nx - 1) * b.pitch + 2 * m
    h = (b.ny - 1) * b.pitch + 2 * m
    layers = ["F.Cu"] + sorted({t[0] for t in cu["tracks"]} - {"F.Cu"})
    colour = {"F.Cu": "#c0392b", "ESC1": "#1f6fb4", "ESC2": "#1e8449",
              "ESC3": "#8e44ad"}
    fill = {"signal": "#f1c40f", "power": "#7f8c8d", "unused": "#ecf0f1",
            "dangling": "#ecf0f1"}
    gap, head, px = 0.9, 1.0, 46.0
    W = len(layers) * w + (len(layers) - 1) * gap
    o = ['<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 %.3f %.3f" '
         'width="%d" height="%d" font-family="sans-serif">'
         % (W, h + head, W * px, (h + head) * px),
         '<rect width="100%" height="100%" fill="#ffffff"/>']
    for k, ly in enumerate(layers):
        o.append('<g transform="translate(%.3f %.3f)">'
                 % (k * (w + gap) + m - b.x0, head + m - b.y0))
        title = ("F.Cu - yellow: signal ball, grey: power ball, white: unused"
                 if ly == "F.Cu" else
                 "%s - lanes run under the ball rows, between the vias" % ly)
        o.append('<text x="%.3f" y="%.3f" font-size="0.5" fill="#222">%s</text>'
                 % (b.x0 - m + 0.2, b.y0 - m - 0.3, title))
        for (i, j), x in b.balls.items():
            cx, cy = b.xy(2 * i, 2 * j)
            if ly == "F.Cu":
                o.append('<circle cx="%.3f" cy="%.3f" r="%.3f" fill="%s" '
                         'stroke="#555" stroke-width="0.02"/>'
                         % (cx, cy, b.pad_d / 2, fill[x["kind"]]))
            else:
                o.append('<circle cx="%.3f" cy="%.3f" r="%.3f" fill="none" '
                         'stroke="#dddddd" stroke-width="0.02"/>'
                         % (cx, cy, b.pad_d / 2))
        for t in cu["tracks"]:
            if t[0] == ly:
                o.append('<line x1="%.3f" y1="%.3f" x2="%.3f" y2="%.3f" '
                         'stroke="%s" stroke-width="%.3f" '
                         'stroke-linecap="round"/>'
                         % (t[2][0], t[2][1], t[3][0], t[3][1],
                            colour.get(ly, "#333333"), 2 * t[4]))
        for net, c in cu["vias"]:
            o.append('<circle cx="%.3f" cy="%.3f" r="%.3f" fill="%s"/>'
                     '<circle cx="%.3f" cy="%.3f" r="%.3f" fill="#ffffff"/>'
                     % (c[0], c[1], r.via_pad / 2,
                        "#95a5a6" if POWER_RE.match(net or "") else "#2c3e50",
                        c[0], c[1], r.via_drill / 2))
        o.append("</g>")
    o.append("</svg>")
    Path(path).write_text("\n".join(o), encoding="utf-8")


def run(board, ref=None, n_lower=1, mode="any", rules: Rules | None = None,
        svg=None, with_copper=False) -> dict:
    """Probe one board. Returns the report dict (see summarize)."""
    r = rules or Rules()
    bga = build_bga(load_footprints(board), ref)
    esc = plan(bga, n_lower, mode)
    cu = emit(esc, r)
    rep = summarize(bga, esc, r, verify(cu, bga, r))
    rep["mode"], rep["layers_below_top"] = mode, n_lower
    if with_copper:
        rep["copper"] = {
            "tracks": [{"layer": t[0], "net": t[1], "a": t[2], "b": t[3],
                        "width": round(2 * t[4], 4)} for t in cu["tracks"]],
            "vias": [{"net": n, "at": c, "pad": r.via_pad,
                      "drill": r.via_drill} for n, c in cu["vias"]]}
    if svg:
        render_svg(bga, cu, r, svg)
    return rep


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("board")
    ap.add_argument("--ref", help="BGA reference (default: largest array)")
    ap.add_argument("--layers", type=int, default=1,
                    help="routing layers below the top one (default 1)")
    ap.add_argument("--mode", choices=("any", "facing"), default="any",
                    help="any: leave on any side (capacity proof); facing: "
                         "leave only on the side that faces the net's other "
                         "pads (pin-assignment diagnostic)")
    ap.add_argument("--track", type=float, default=0.10)
    ap.add_argument("--clearance", type=float, default=0.10)
    ap.add_argument("--via", type=float, nargs=2, default=(0.45, 0.20),
                    metavar=("PAD", "DRILL"))
    ap.add_argument("--svg", help="write a per-layer picture")
    ap.add_argument("--json", help="write the full report incl. copper")
    a = ap.parse_args(argv)
    rules = Rules(track_w=a.track, clearance=a.clearance,
                  via_pad=a.via[0], via_drill=a.via[1])
    rep = run(a.board, a.ref, a.layers, a.mode, rules, a.svg,
              with_copper=bool(a.json))
    if a.json:
        Path(a.json).write_text(json.dumps(rep, ensure_ascii=False, indent=1),
                                encoding="utf-8")
    brief = {k: rep[k] for k in ("bga", "signals", "escaped", "how",
                                 "exit_sides", "power_balls",
                                 "power_with_own_via", "vias")}
    brief["lattice_margins_mm"] = rep["lattice_margins_mm"]
    brief["exact_check_min_gap_mm"] = rep["exact_check"]["min_gap_mm"]
    brief["exact_check_violations"] = len(rep["exact_check"]["violations"])
    brief["ok"] = rep["ok"]
    print(json.dumps(brief, ensure_ascii=False, indent=1))
    return 0 if rep["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
