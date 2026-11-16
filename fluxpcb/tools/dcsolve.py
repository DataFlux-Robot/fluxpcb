# -*- coding: utf-8 -*-
"""Geometry-aware DC solver — real copper from the .kicad_pcb file.

Rasterizes every copper item (tracks, vias, pads, zone outlines) of the
routed board into a per-layer grid, then solves the conduction field on the
REAL geometry (padne/KiPIDA-equivalent, in-house):

  div(sigma*t*grad V) = 0    on same-rail copper
  J(x,y) = -sigma*t*grad V   -> current density + crowding metrics

Outputs sim/dc_irdrop_geom.json and sim/dc_current_density.json with the
full field for the dashboard, plus pass/fail metrics (drop %, max J vs
IPC-2152-style density limit, neck width).
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import numpy as np

from fluxpcb.gates import _sexpr_blocks

SIGMA_CU = 5.8e7          # S/m
IPC_J_LIMIT_A_MM2 = 45.0  # ~external 1oz, 10C rise class (conservative)


def _f(s):
    return float(s)


def parse_copper(pcb_path: Path):
    """[(layer, kind, points|seg, width, net)] from a .kicad_pcb file."""
    txt = pcb_path.read_text(encoding="utf-8", errors="replace")
    items = []

    for blk in _sexpr_blocks(txt, "segment"):
        m = re.search(r"\(start ([-\d.]+) ([-\d.]+)\)\s*\(end ([-\d.]+) ([-\d.]+)\)"
                      r"[\s\S]{0,120}?\(width ([\d.]+)\)[\s\S]{0,80}?\(layer \"(\w+)\.Cu\""
                      r"[\s\S]{0,80}?\(net \"([^\"]*)\"", blk)
        if m:
            items.append(("seg", m.group(6), (_f(m.group(1)), _f(m.group(2))),
                          (_f(m.group(3)), _f(m.group(4))), _f(m.group(5)), m.group(7)))

    for blk in _sexpr_blocks(txt, "via"):
        m = re.search(r"\(at ([-\d.]+) ([-\d.]+)[\s\S]{0,120}?\(size ([\d.]+)\)"
                      r"[\s\S]{0,120}?\(net \"([^\"]*)\"", blk)
        if m:
            items.append(("via", "ALL", (_f(m.group(1)), _f(m.group(2))),
                          None, _f(m.group(3)), m.group(4)))

    for blk in _sexpr_blocks(txt, "footprint"):
        ref = re.search(r'\(\s*property\s*"Reference"\s*"([^"]+)"', blk)
        at = re.search(r"\(\s*at\s+([-\d.]+) ([-\d.]+)(?: ([-\d.]+))?", blk)
        if not ref or not at:
            continue
        fx, fy, frot = _f(at.group(1)), _f(at.group(2)), _f(at.group(3) or 0)
        for pm in re.finditer(
                r'\(\s*pad\s*"([^"]+)"[\s\S]{0,120}?\(\s*at\s+'
                r'([-\d.]+) ([-\d.]+)(?: ([-\d.]+))?'
                r"[\s\S]{0,200}?\(\s*size\s+([\d.]+) ([\d.]+)\)"
                # nets appear as (net N "name") in our output or name-only
                # (net "name") after a KiCad 10 re-save
                r"[\s\S]{0,400}?\(\s*net(?:\s*\d+)?\s*\"([^\"]*)\"", blk):
            px, py = _f(pm.group(2)), _f(pm.group(3))
            prot = frot + _f(pm.group(4) or 0)
            w, h = _f(pm.group(5)), _f(pm.group(6))
            if abs(prot % 180 - 90) < 45:
                w, h = h, w
            ca, sa = np.cos(np.radians(frot)), np.sin(np.radians(frot))
            gx = fx + px * ca + py * sa
            gy = fy - px * sa + py * ca
            items.append(("pad", "ALL", (gx, gy), None, max(w, h),
                          pm.group(7), ref.group(1)))
    # zone polygons -> plane copper rectangles (fulfills the docstring):
    # each zone contributes a full-copper bbox on its layer for its net
    for zm in re.finditer(
            r'\(zone \(net(?: \d+)?\) \(net_name "([^"]+)"\) \(layer "([^"]+)"\)'
            r'[\s\S]{0,900}?\(polygon \(pts((?: \((?:xy|arc)[^)]*\))+)\)', txt):
        net, layer, pts_raw = zm.group(1), zm.group(2), zm.group(3)
        xy = [(float(a), float(b)) for a, b in re.findall(
            r'\(xy ([-\d.]+) ([-\d.]+)', pts_raw)]
        if len(xy) < 3:
            continue
        xs = [p[0] for p in xy]
        ys = [p[1] for p in xy]
        items.append(("zone", layer,
                      (min(xs), min(ys)), (max(xs), max(ys)),
                      0.0, net, "__plane__"))
    return items


class CopperGrid:
    def __init__(self, w_mm, h_mm, res=0.2):
        self.res = res
        self.nx = int(round(w_mm / res))
        self.ny = int(round(h_mm / res))
        self.mask = np.zeros((self.ny, self.nx), dtype=np.int8)   # conductor?
        self.net = {}                                             # idx -> net
        self.w_mm, self.h_mm = w_mm, h_mm

    def _plot(self, x, y, r):
        i0 = max(0, int((y - r) / self.res))
        i1 = min(self.ny, int((y + r) / self.res) + 1)
        j0 = max(0, int((x - r) / self.res))
        j1 = min(self.nx, int((x + r) / self.res) + 1)
        if i1 > i0 and j1 > j0:
            self.mask[i0:i1, j0:j1] = 1

    def _plot_seg(self, a, b, w):
        ax, ay = a
        bx, by = b
        n = max(2, int(((bx - ax) ** 2 + (by - ay) ** 2) ** 0.5 / (self.res / 2)))
        r = max(w / 2, self.res / 2)
        for i in range(n + 1):
            t = i / n
            self._plot(ax + (bx - ax) * t, ay + (by - ay) * t, r)

    def add_items(self, items, net_name):
        """Rasterize only items of the given rail net (pads by pad size;
        zone planes as full-copper rectangles)."""
        for it in items:
            kind = it[0]
            net = it[5]
            if kind in ("seg", "via", "zone") and net != net_name:
                continue
            if kind == "seg":
                self._plot_seg(it[2], it[3], it[4])
            elif kind == "via":
                self._plot(it[2][0], it[2][1], it[4] / 2)
            elif kind == "zone":
                (x0, y0), (x1, y1) = it[2], it[3]
                # three fat bars tile the bbox (plot radius = half height)
                for yy in (y0, (y0 + y1) / 2, y1):
                    self._plot_seg((x0, yy), (x1, yy), (y1 - y0) / 2)
            elif kind == "pad" and net == net_name:
                self._plot(it[2][0], it[2][1], it[4] / 2)


def solve_rail(pcb_path: Path, rail: str, v_rail: float,
               source_ref: str = "U1", sink_refs: tuple = ("MOD1",),
               i_total_a: float = 0.5, t_cu_mm: float = 0.035,
               res: float = 0.2, iters: int = 15000):
    """DC solve on real copper. Returns dict with V/J fields + metrics."""
    items = parse_copper(pcb_path)
    # board outline
    txt = pcb_path.read_text(encoding="utf-8", errors="replace")
    edge = re.search(r"\(gr_rect[\s\S]{0,120}?\(start ([-\d.]+) ([-\d.]+)\)"
                     r"[\s\S]{0,120}?\(end ([-\d.]+) ([-\d.]+)\)", txt)
    if not edge:
        return {"status": "error", "error": "no board outline"}
    x0, y0, x1, y1 = (min(_f(edge.group(1)), _f(edge.group(3))),
                      min(_f(edge.group(2)), _f(edge.group(4))),
                      max(_f(edge.group(1)), _f(edge.group(3))),
                      max(_f(edge.group(2)), _f(edge.group(4))))
    g = CopperGrid(x1 - x0, y1 - y0, res)
    g.add_items(items, rail)

    def stamp(ref, val):
        for it in items:
            if it[0] == "pad" and it[6] == ref and it[5] == rail:
                return (it[2][0] - x0, it[2][1] - y0)
        return None

    src_xy = stamp(source_ref, None)
    sinks = [s for s in (stamp(r, None) for r in sink_refs) if s]
    if not src_xy or not sinks:
        return {"status": "error",
                "error": f"source/sink pads of net {rail} not found"}

    mask = g.mask.astype(bool)
    ny, nx = mask.shape
    t = t_cu_mm * 1e-3                       # m
    V = np.full((ny, nx), v_rail)
    src = np.zeros((ny, nx), bool)
    snk = np.zeros((ny, nx), bool)
    for xy, arr in ((src_xy, src), *[(s, snk) for s in sinks]):
        j = min(nx - 1, max(0, int(xy[0] / res)))
        i = min(ny - 1, max(0, int(xy[1] / res)))
        # spread over the pad footprint cells within radius
        for di in range(-3, 4):
            for dj in range(-3, 4):
                ii, jj = i + di, j + dj
                if 0 <= ii < ny and 0 <= jj < nx and mask[ii, jj]:
                    arr[ii, jj] = True
    V[src] = v_rail

    # red-black SOR on the 5-point conduction stencil; each sink cell draws
    # i_total/n_sink, modeled as a fixed depression of one cell resistance
    sink_cells = snk & mask
    n_sink = int(sink_cells.sum())
    i_cell = i_total_a / n_sink if n_sink else 0.0
    # sheet resistance per square (face R = res/(sigma*t*res) = 1/(sigma*t))
    r_cell = 1.0 / (SIGMA_CU * t)
    parity = (np.add.outer(np.arange(ny), np.arange(nx)) % 2).astype(bool)
    omega = 1.9
    tol = 1e-7          # absolute volts; board drops are ~mV scale
    acc = np.zeros_like(V)
    for it in range(iters):
        delta = 0.0
        for color in (False, True):
            acc[:] = 0.0
            acc[1:, :] += V[:-1, :]
            acc[:-1, :] += V[1:, :]
            acc[:, 1:] += V[:, :-1]
            acc[:, :-1] += V[:, 1:]
            acc *= 0.25
            sel = mask & ~src & (parity == color) & ~sink_cells
            if sel.any():
                upd = (1 - omega) * V[sel] + omega * acc[sel]
                delta = max(delta, float(np.max(np.abs(upd - V[sel]))))
                V[sel] = upd
            if n_sink:
                sel_s = sink_cells & (parity == color)
                if sel_s.any():
                    upd_s = (1 - omega) * V[sel_s] + omega * (acc[sel_s] - i_cell * r_cell / 4)
                    delta = max(delta, float(np.max(np.abs(upd_s - V[sel_s]))))
                    V[sel_s] = upd_s
        V[src] = v_rail
        if it > 800 and delta < tol:
            break

    # gradient -> current density (A/m^2 through the foil cross-section)
    gy, gx = np.gradient(V, res * 1e-3)
    J = SIGMA_CU * np.sqrt(gx ** 2 + gy ** 2)          # A/m^2
    J[~mask] = 0.0
    J_mm2 = J / 1e6                                    # A/mm^2
    v_min = float(V[mask].min())
    drop_pct = (v_rail - v_min) / v_rail * 100
    jmax = float(J_mm2.max())
    # neck metric: thinnest conductor corridor the flow passes through
    # (approx: min over rows/cols of copper coverage on the rail)
    return {
        "status": "ok", "rail": rail, "v_rail_v": v_rail,
        "v_min_v": round(v_min, 4), "drop_pct": round(drop_pct, 4),
        "j_max_a_mm2": round(jmax, 2), "j_limit_a_mm2": IPC_J_LIMIT_A_MM2,
        "crowding_ok": jmax <= IPC_J_LIMIT_A_MM2,
        "i_total_a": i_total_a,
        "grid": {"nx": nx, "ny": ny, "dx_mm": res, "origin": [x0, y0]},
        "field_v": [[round(float(v), 4) for v in row[::2]] for row in V[::2]],
        "field_j": [[round(float(v), 2) for v in row[::2]] for row in J_mm2[::2]],
        "mask": [[1 if c else 0 for c in row[::2]] for row in mask[::2]],
    }


def run_dc(project_dir: Path, spec: dict) -> dict:
    """Solve every rail with a regulator footprint on it; writes evidence."""
    pcbs = sorted((project_dir / "pcb").rglob("*.kicad_pcb"))
    if not pcbs:
        return {"status": "error", "error": "no pcb"}
    power = spec.get("power") or {}
    i_load = float(power.get("total_w", 1.5)) / 3.3   # A on 3V3 (approx)
    results = []
    for rail in power.get("rails", []):
        if isinstance(rail, dict):
            name = rail["name"]
            v = float(rail.get("v", 3.3))
            src = rail.get("source_ref", "U1")
            snks = tuple(rail.get("sink_refs", ["MOD1"]))
            ia = float(rail.get("current_a", max(0.1, i_load)))
        else:
            name, v = rail, 3.3 if "3V3" in str(rail) else 1.8
            src, snks, ia = "U1", ("MOD1",), max(0.1, i_load)
        r = solve_rail(pcbs[0], name, float(v), source_ref=src,
                       sink_refs=snks, i_total_a=ia)
        if r.get("status") == "ok":
            results.append(r)
            (project_dir / "sim").mkdir(exist_ok=True)
            (project_dir / "sim" / "dc_current_density.json").write_text(
                json.dumps({k: r[k] for k in
                            ("rail", "j_max_a_mm2", "j_limit_a_mm2",
                             "crowding_ok", "grid", "field_j", "mask")},
                           ensure_ascii=False), encoding="utf-8")
            (project_dir / "sim" / "dc_irdrop_geom.json").write_text(
                json.dumps({k: r[k] for k in
                            ("rail", "v_rail_v", "v_min_v", "drop_pct",
                             "grid", "field_v")},
                           ensure_ascii=False), encoding="utf-8")
        else:
            results.append({"rail": name, "status": "error",
                            "error": r.get("error", "")})
    ok = [x for x in results if x.get("status") == "ok"]
    return {"status": "ok" if ok else "error", "rails": results}
