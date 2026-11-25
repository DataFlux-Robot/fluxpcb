"""Audit-grade copper model: the real copper, per layer, as a resistor network.

The pipeline's `dcsolve` is a screening proxy: it merges the layers into one
mask, models a zone as three fat bars, and pins every non-copper cell to the
supply voltage, so copper islands are shorted through "air".  It is fine for
a gate that only asks "is some rail obviously starving", and it is not fine
for an audit that says "38 A/mm^2 at this neck".

This module builds what the audit needs:

  * a per-net, per-layer coverage raster from the board itself — the net's
    tracks, its pads (rect/rotated), its vias and a zone's **filled**
    polygons (holes cut out), at `cell_mm`;
  * a sparse conductance graph: adjacent copper cells on a layer joined by
    the harmonic mean of their sheet conductance, layers joined only where a
    via or a plated through-hole pad is;
  * a DC solve with **Neumann at every copper edge** (no phantom Dirichlet),
    a source set fixed at 0 V and current injected at the sinks, by
    preconditioned conjugate gradient;
  * current density from the face currents divided by the cell width and the
    copper thickness (the quantity IPC-2221 limits), the current through
    every via, and the drop on the rail.

    extract(board, cell_mm=0.2) -> Copper
    net_cover(copper, net) -> [array per layer]
    solve(copper, net, current_a, v_rail=...) -> dict
    narrowest(copper, net) -> {width_mm, at, layer, length_mm}

Nothing here reads a project file or a spec: it takes a `.kicad_pcb` and a
net, and measures that board.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

SIGMA_CU = 5.8e7          # S/m
T_CU_MM = 0.035           # 1 oz
RHO_CU = 1.0 / SIGMA_CU   # ohm*m
R_VIA_OHM = 1e-3          # one via/barrel, barrel + spreading (order of magnitude)
MIN_COVER = 0.08          # a cell counts as copper above this


@dataclass
class Copper:
    """One board's copper: geometry to rasterise, plus a net index."""
    board_path: str
    board_fp: str
    cell_mm: float
    x0_mm: float
    y0_mm: float
    nx: int
    ny: int
    layers: list = field(default_factory=list)        # layer names
    segs: dict = field(default_factory=dict)          # net -> [(x1,y1,x2,y2,w,layer)]
    pads: dict = field(default_factory=dict)          # net -> [{ref,pad,x,y,w,h,rot,layers}]
    vias: list = field(default_factory=list)          # [{x,y,r,net}]
    zones: list = field(default_factory=list)         # [{net,layer,areas:[(pts,holes)]}]
    nets: dict = field(default_factory=dict)          # netcode -> name
    board_mask: object = None                         # bool (ny,nx) inside Edge.Cuts
    board_area_mm2: float = 0.0                       # outline area

    # ------------------------------------------------------------ helpers
    def cell_of(self, x_mm, y_mm):
        return (min(self.nx - 1, max(0, int((x_mm - self.x0_mm) / self.cell_mm))),
                min(self.ny - 1, max(0, int((y_mm - self.y0_mm) / self.cell_mm))))

    def xy_of(self, i, j):
        return (self.x0_mm + (i + 0.5) * self.cell_mm,
                self.y0_mm + (j + 0.5) * self.cell_mm)

    def layer_index(self, name):
        return self.layers.index(name) if name in self.layers else None


# ---------------------------------------------------------------- extraction
def _filled_polys(zone, lid):
    """Outer outlines and holes of a zone's FILLED copper on one layer."""
    out = []
    try:
        poly = zone.GetFilledPolysList(lid)
        n = poly.OutlineCount()
    except Exception:
        return out
    for i in range(n):
        try:
            chain = poly.Outline(i)
            pts = [(chain.CPoint(k).x / 1e6, chain.CPoint(k).y / 1e6)
                   for k in range(chain.PointCount())]
        except Exception:
            continue
        holes = []
        try:
            for h in range(poly.HoleCount(i)):
                hc = poly.Hole(i, h)
                holes.append([(hc.CPoint(k).x / 1e6, hc.CPoint(k).y / 1e6)
                              for k in range(hc.PointCount())])
        except Exception:
            holes = []
        out.append((pts, holes))
    return out


def _board_layers(b):
    import pcbnew
    n = b.GetCopperLayerCount()
    if n > 2:
        return [pcbnew.F_Cu] + [getattr(pcbnew, f'In{k}_Cu')
                                for k in range(1, n - 1)] + [pcbnew.B_Cu]
    return [pcbnew.F_Cu, pcbnew.B_Cu]


def extract(board_path, cell_mm: float = 0.2) -> Copper:
    """Read the board's geometry; no raster yet (see net_cover)."""
    import hashlib

    import pcbnew

    with open(board_path, 'rb') as fh:
        fp = hashlib.sha256(fh.read()).hexdigest()[:16]
    b = pcbnew.LoadBoard(str(board_path))
    lids = _board_layers(b)
    names = [b.GetLayerName(l) for l in lids]
    bb = b.GetBoardEdgesBoundingBox()
    x0, y0 = bb.GetLeft() / 1e6, bb.GetTop() / 1e6
    w, h = bb.GetWidth() / 1e6, bb.GetHeight() / 1e6
    nx = max(8, int(math.ceil(w / cell_mm)) + 1)
    ny = max(8, int(math.ceil(h / cell_mm)) + 1)
    # the board outline, as a mask: it is the domain for plane coverage and
    # for return-path availability (a disc hanging off the board is not a
    # return path, and the bbox is not the board on any shaped outline)
    board_mask = None
    board_area = w * h
    try:
        poly = pcbnew.SHAPE_POLY_SET()
        if b.GetBoardPolygonOutlines(poly, False) and poly.OutlineCount():
            from PIL import Image, ImageDraw
            s_ = 1.0 / cell_mm
            img = Image.new('L', (nx, ny), 0)
            dr = ImageDraw.Draw(img)
            for i in range(poly.OutlineCount()):
                ch = poly.Outline(i)
                pts = [((ch.CPoint(k).x / 1e6 - x0) * s_,
                        (ch.CPoint(k).y / 1e6 - y0) * s_)
                       for k in range(ch.PointCount())]
                if len(pts) >= 3:
                    dr.polygon(pts, fill=255)
                for hh in range(poly.HoleCount(i)):
                    hc = poly.Hole(i, hh)
                    hp = [((hc.CPoint(k).x / 1e6 - x0) * s_,
                           (hc.CPoint(k).y / 1e6 - y0) * s_)
                          for k in range(hc.PointCount())]
                    if len(hp) >= 3:
                        dr.polygon(hp, fill=0)
            board_mask = np.asarray(img, dtype=bool)
            board_area = poly.Area() / 1e12
    except Exception:
        board_mask = None

    segs, pads, nets = {}, {}, {}
    for t in b.GetTracks():
        nc = t.GetNetCode()
        if nc:
            nm = b.FindNet(nc)
            nets[nc] = nm.GetNetname() if nm else ''
        if t.Type() == pcbnew.PCB_VIA_T:
            continue
        s_, e_ = t.GetStart(), t.GetEnd()
        segs.setdefault(nc, []).append(
            (s_.x / 1e6, s_.y / 1e6, e_.x / 1e6, e_.y / 1e6,
             t.GetWidth() / 1e6, b.GetLayerName(t.GetLayer())))
    for fp_ in b.GetFootprints():
        for pad in fp_.Pads():
            nc = pad.GetNetCode()
            if not nc:
                continue
            p = pad.GetPosition()
            sz = pad.GetSize()
            pads.setdefault(nc, []).append({
                'ref': fp_.GetReference(), 'pad': pad.GetNumber(),
                'value': fp_.GetValue(),
                'x': p.x / 1e6, 'y': p.y / 1e6,
                'w': sz.x / 1e6, 'h': sz.y / 1e6,
                'rot': pad.GetOrientation().AsDegrees(),
                'layers': [b.GetLayerName(l) for l in lids if pad.IsOnLayer(l)]})
            if len(pads[nc][-1]['layers']) > 1:      # a barrel links the layers
                pads[nc][-1]['barrel'] = True
    vias = []
    for t in b.GetTracks():
        if t.Type() == pcbnew.PCB_VIA_T:
            q = t.GetPosition()
            nm = b.FindNet(t.GetNetCode())
            vias.append({'x': q.x / 1e6, 'y': q.y / 1e6,
                         'r_mm': t.GetWidth(pcbnew.F_Cu) / 2e6,
                         'net': nm.GetNetname() if nm else ''})
    zones = []
    for z in b.Zones():
        nc = z.GetNetCode()
        nm = b.FindNet(nc)
        for lid in lids:
            try:
                if not z.IsOnLayer(lid):
                    continue
            except Exception:
                continue
            areas = _filled_polys(z, lid)
            if areas:
                zones.append({'net': nm.GetNetname() if nm else '',
                              'layer': b.GetLayerName(lid), 'areas': areas})
    return Copper(board_path=str(board_path), board_fp=fp, cell_mm=cell_mm,
                  x0_mm=x0, y0_mm=y0, nx=nx, ny=ny, layers=names,
                  segs=segs, pads=pads, vias=vias, zones=zones, nets=nets,
                  board_mask=board_mask, board_area_mm2=round(board_area, 1))


def _raster(copper: Copper, lid_name, net: str | None):
    """Draw one layer.  net=None draws everything (the thermal view)."""
    from PIL import Image, ImageDraw

    s = 1.0 / copper.cell_mm
    x0, y0 = copper.x0_mm, copper.y0_mm

    def P(x, y):
        return ((x - x0) * s, (y - y0) * s)

    img = Image.new('L', (copper.nx, copper.ny), 0)
    d = ImageDraw.Draw(img)
    for nc, plist in copper.pads.items():
        if net is not None and copper.nets.get(nc) != net:
            continue
        for p in plist:
            if lid_name not in p['layers']:
                continue
            hw, hh = p['w'] / 2, p['h'] / 2
            ca = math.cos(math.radians(p['rot']))
            sa = math.sin(math.radians(p['rot']))
            pts = []
            for (sx, sy) in ((1, 1), (-1, 1), (-1, -1), (1, -1)):
                lx, ly = sx * hw, sy * hh
                pts.append(P(p['x'] + lx * ca - ly * sa, p['y'] + lx * sa + ly * ca))
            d.polygon(pts, fill=255)
    for nc, rows in copper.segs.items():
        if net is not None and copper.nets.get(nc) != net:
            continue
        for (x1, y1, x2, y2, w, layer) in rows:
            if layer != lid_name:
                continue
            wpx = max(1.0, w * s)
            d.line([P(x1, y1), P(x2, y2)], fill=255, width=int(round(wpx)))
            # a KiCad track has round ends; without the caps a corner between
            # two segments is a one-cell pinch and the solver reports a fake
            # hot spot there
            r = wpx / 2.0
            for (x_, y_) in ((x1, y1), (x2, y2)):
                cx, cy = P(x_, y_)
                d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=255)
    for v in copper.vias:
        if net is not None and v['net'] != net:
            continue
        cx, cy = P(v['x'], v['y'])
        r = v['r_mm'] * s
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=255)
    for z in copper.zones:
        if z['layer'] != lid_name:
            continue
        if net is not None and z['net'] != net:
            continue
        for (pts, holes) in z['areas']:
            if len(pts) < 3:
                continue
            d.polygon([P(x, y) for (x, y) in pts], fill=255)
            for hole in holes:
                if len(hole) >= 3:
                    d.polygon([P(x, y) for (x, y) in hole], fill=0)
    return np.asarray(img, dtype=np.float32) / 255.0


def net_cover(copper: Copper, net: str, lid_name=None):
    """The net's own copper, per layer, 0..1 per cell."""
    if lid_name is not None:
        return _raster(copper, lid_name, net)
    return [_raster(copper, l, net) for l in copper.layers]


def segment_inductance_nh(p, q, width_mm):
    """Partial self-inductance of one straight trace segment, in nH.

    The closed form is the one OpenPE uses (Apache-2.0,
    lib/pcb-partial-inductance.ts): for length L and radius r, in mm,

        L_p = 0.1 * 2 * (L*asinh(L/r) - L^2/(hypot(L,r)+r))   [nH]

    with r = width/2 (an equivalent round conductor; a rectangular trace's
    corner current is not modelled).  Mutual terms between segments are not
    summed: the route value is a self-term series, said so in the result.
    """
    L = math.hypot(q[0] - p[0], q[1] - p[1])
    if L <= 0:
        return 0.0
    r = max(float(width_mm) / 2.0, 0.01)
    return 0.1 * 2.0 * (L * math.asinh(L / r)
                        - L * L / (math.hypot(L, r) + r))


def route_inductance_nh(copper: Copper, net: str, t_board_mm: float = 1.6):
    """Self-inductance along one net's route: segments plus vias.

    A via is taken as 0.4 nH per mm of board thickness (a declared
    approximation, not a field solve).  The result names its limits: no
    mutual coupling between parallel segments, no return-path inductance
    (that needs the plane geometry, which the audit reports separately).
    """
    nc = net_by_name(copper, net)
    if nc is None:
        return {'l_nh': None, 'error': f'net {net!r} not on this board'}
    total = 0.0
    n = 0
    for (x1, y1, x2, y2, w, _layer) in copper.segs.get(nc, []):
        total += segment_inductance_nh((x1, y1), (x2, y2), w)
        n += 1
    nv = sum(1 for v in copper.vias if v.get('net') == net)
    total += nv * 0.4 * float(t_board_mm)
    return {'l_nh': round(total, 2), 'n_segments': n, 'n_vias': nv,
            'assumption': ('segment self-terms + 0.4 nH/mm per via; no mutual '
                           'coupling and no return-path inductance')}


def net_by_name(copper: Copper, name: str):
    for nc, nm in copper.nets.items():
        if nm == name:
            return nc
    return None


def narrowest(copper: Copper, name: str):
    """The narrowest segment of a net, with its midpoint."""
    nc = net_by_name(copper, name)
    if nc is None or not copper.segs.get(nc):
        return None
    rows = copper.segs[nc]
    x1, y1, x2, y2, w, layer = min(rows, key=lambda r: r[4])
    return {'width_mm': w, 'at': (round((x1 + x2) / 2, 2), round((y1 + y2) / 2, 2)),
            'layer': layer, 'length_mm': round(math.hypot(x2 - x1, y2 - y1), 3)}


# --------------------------------------------------------------------- solve
def solve(copper: Copper, net: str, current_a: float, v_rail: float = 1.0,
          sources=None, sinks=None, t_cu_mm: float = T_CU_MM,
          r_via_ohm: float = R_VIA_OHM, cover=None, smooth_mm: float = 2.0):
    """DC solve of one net on the real copper.

    sources: [(x,y)] fixed at 0 V (default: the pad nearest the board edge);
    sinks: where `current_a` is drawn (default: the net's other pads).
    `cover` lets a caller pass rasters it already has (tests, cached audit).
    """
    import scipy.sparse as sp
    from scipy.sparse.linalg import cg, spsolve

    nc = net_by_name(copper, net)
    if nc is None:
        return {'status': 'error', 'error': f'net {net!r} not on this board'}
    covs = cover if cover is not None else net_cover(copper, net)
    ids, rev = {}, []
    for li, cov in enumerate(covs):
        mask = cov >= MIN_COVER
        for j, i in zip(*np.nonzero(mask)):
            ids[(li, int(j), int(i))] = len(rev)
            rev.append((li, int(j), int(i)))
    if not rev:
        return {'status': 'error', 'error': f'net {net!r} has no copper cells'}
    n = len(rev)
    G_sq = SIGMA_CU * (t_cu_mm * 1e-3)        # S per square

    def node(li, j, i):
        return ids.get((li, j, i))

    rows, cols, vals = [], [], []
    for (li, j, i) in rev:
        k = ids[(li, j, i)]
        c0 = covs[li][j, i]
        # the diagonal links matter: a 45-degree trace is one pixel wide at
        # some cell size, and a 4-neighbour graph would cut it into pieces.
        # A 9-point Laplacian with half weight on the diagonals is the
        # standard stencil for the same continuum operator.
        for (dj, di) in ((0, 1), (1, 0), (1, 1), (1, -1)):
            jj, ii = j + dj, i + di
            if not (0 <= jj < copper.ny and 0 <= ii < copper.nx):
                continue
            k2 = node(li, jj, ii)
            if k2 is None:
                continue
            c1 = covs[li][jj, ii]
            # the 9-point stencil that keeps the same continuum operator:
            # edge 2/3, diagonal 1/6 (a plain 1 + 1/2 doubles the conductance)
            w = (1.0 / 6.0) if (dj != 0 and di != 0) else (2.0 / 3.0)
            g = G_sq * w * (2.0 * c0 * c1 / max(1e-9, c0 + c1))
            rows += [k, k2]
            cols += [k2, k]
            vals += [-g, -g]
    # vias and plated barrels join every layer they pass
    links = []
    for v in copper.vias:
        if v['net'] and v['net'] != net:
            continue
        links.append((v['x'], v['y']))
    for p in copper.pads.get(nc, []):
        if p.get('barrel'):
            links.append((p['x'], p['y']))
    via_edges = []
    for (x, y) in links:
        i, j = copper.cell_of(x, y)
        ks = [node(li, j, i) for li in range(len(copper.layers))]
        ks = [k for k in ks if k is not None]
        for a in range(len(ks)):
            for b in range(a + 1, len(ks)):
                rows += [ks[a], ks[b]]
                cols += [ks[b], ks[a]]
                vals += [-1.0 / r_via_ohm, -1.0 / r_via_ohm]
                via_edges.append((ks[a], ks[b], x, y))
    A = sp.coo_matrix((vals, (rows, cols)), shape=(n, n)).tocsr()
    diag = -np.asarray(A.sum(axis=1)).ravel()
    A = A + sp.diags(diag)

    # terminals
    plist = copper.pads.get(nc, [])
    if sources is None:
        if not plist:
            return {'status': 'error', 'error': f'net {net!r} has no pads'}
        yn = copper.y0_mm + copper.ny * copper.cell_mm
        xr = copper.x0_mm + copper.nx * copper.cell_mm
        edge = min(plist, key=lambda p: min(abs(p['x'] - copper.x0_mm),
                                            abs(p['x'] - xr),
                                            abs(p['y'] - copper.y0_mm),
                                            abs(p['y'] - yn)))
        sources = [(edge['x'], edge['y'])]
    if sinks is None:
        sinks = [(p['x'], p['y']) for p in plist
                 if all(math.hypot(p['x'] - s[0], p['y'] - s[1]) > 2 * copper.cell_mm
                        for s in sources)]

    def near(x, y):
        """All copper cells of the pad at (x,y): a pad is an equipotential
        area, not a point — injecting into one cell makes a fake hot spot."""
        hit = [p for p in plist
               if math.hypot(p['x'] - x, p['y'] - y) < max(0.05, copper.cell_mm)]
        out = []
        for p in hit:
            hw, hh = p['w'] / 2, p['h'] / 2
            ca = math.cos(math.radians(p['rot']))
            sa = math.sin(math.radians(p['rot']))
            r = hw + hh
            i0, j0 = copper.cell_of(p['x'] - r, p['y'] - r)
            i1, j1 = copper.cell_of(p['x'] + r, p['y'] + r)
            for j in range(j0, j1 + 1):
                for i in range(i0, i1 + 1):
                    cx, cy = copper.xy_of(i, j)
                    dx, dy = cx - p['x'], cy - p['y']
                    lx = dx * ca + dy * sa
                    ly = -dx * sa + dy * ca
                    if abs(lx) <= hw + copper.cell_mm / 2 and \
                            abs(ly) <= hh + copper.cell_mm / 2:
                        for li in range(len(copper.layers)):
                            k = node(li, j, i)
                            if k is not None:
                                out.append(k)
        if out:
            return out
        # not a pad (or smaller than a cell): take the nearest copper cell
        i, j = copper.cell_of(x, y)
        ks = [node(li, j, i) for li in range(len(copper.layers))]
        cand = [k for k in ks if k is not None]
        if cand:
            return cand
        best = min(rev, key=lambda t: (t[1] - j) ** 2 + (t[2] - i) ** 2)
        return [ids[best]]

    # a sink pad on an island must NOT be snapped to the nearest copper cell
    # of the main component (that invents a connection): classify the pad
    # cells by connected component and name the pads that are open.
    from scipy.sparse.csgraph import connected_components
    ncomp, lab = connected_components(A, directed=False)
    src_cells = [(x, y, near(x, y)) for (x, y) in sources]
    src_all = [k for _x, _y, cells in src_cells for k in cells]
    if not src_all:
        return {'status': 'error', 'error': 'no source cells on copper'}
    comps = [int(lab[k]) for k in src_all]
    src_comp = max(set(comps), key=comps.count)
    src_nodes = sorted({k for k in src_all if lab[k] == src_comp})
    sink_nodes, open_sinks = [], []
    for (x, y) in sinks:
        good = [k for k in near(x, y) if lab[k] == src_comp]
        if good:
            sink_nodes += good
        else:
            open_sinks.append((round(float(x), 2), round(float(y), 2)))
    sink_nodes = [k for k in dict.fromkeys(sink_nodes) if k not in set(src_nodes)]
    if not sink_nodes:
        return {'status': 'error', 'open': True,
                'error': ('net is open: no sink pad shares copper with the '
                          'source'), 'components': int(ncomp),
                'open_sinks': open_sinks}

    free = np.ones(n, bool)
    free[src_nodes] = False
    b = np.zeros(n)
    for k in sink_nodes:
        b[k] += current_a / len(sink_nodes)
    A_ff = A[free][:, free].tocsc()
    x = np.zeros(n)
    # a narrow neck makes the matrix stiff (partial-coverage conductances span
    # orders of magnitude): Jacobi-preconditioned CG, and a direct solve as the
    # fallback for boards small enough to afford it
    try:
        d = A_ff.diagonal()
        Mpre = sp.diags(1.0 / np.maximum(np.abs(d), 1e-18))
        sol, info = cg(A_ff, b[free], M=Mpre, rtol=1e-8, maxiter=30000)
    except Exception as e:
        return {'status': 'error', 'error': f'solver: {type(e).__name__}: {e}'}
    if info != 0:
        if A_ff.shape[0] > 200000:
            return {'status': 'error', 'error': f'cg did not converge ({info})'}
        try:
            sol = spsolve(A_ff, b[free])
        except Exception as e:
            return {'status': 'error', 'error': f'direct: {type(e).__name__}: {e}'}
        if not np.all(np.isfinite(sol)):
            return {'status': 'error', 'error': 'singular copper matrix'}
    x[free] = sol

    # face currents, per layer: the two layers share the grid, so a single
    # potential array would mix them at a via cell and fake a gradient.
    # The physical current through an E-W face of the 9-point stencil is
    # (2/3) of the edge difference plus 1/6 of each diagonal difference that
    # crosses the same face; on a uniform strip the three add to G*dV, so J
    # comes out I/(w*t) and an equipotential pad cannot fake a hot spot.
    dx = copper.cell_mm * 1e-3
    t_m = t_cu_mm * 1e-3
    jmag = []
    for li, cov in enumerate(covs):
        v = np.full((copper.ny, copper.nx), np.nan, np.float64)
        for k, (l_, j_, i_) in enumerate(rev):
            if l_ == li:
                v[j_, i_] = x[k]
        okc = (cov >= MIN_COVER) & np.isfinite(v)
        J = np.zeros((copper.ny, copper.nx), np.float32)
        # E-W faces
        e = okc[:, :-1] & okc[:, 1:]
        d0 = np.where(e, v[:, 1:] - v[:, :-1], 0.0)
        up = np.zeros_like(d0)
        dn = np.zeros_like(d0)
        up[1:, :] = np.where(okc[1:, :-1] & okc[1:, 1:],
                             v[1:, 1:] - v[1:, :-1], 0.0)
        dn[:-1, :] = np.where(okc[:-1, :-1] & okc[:-1, 1:],
                              v[:-1, 1:] - v[:-1, :-1], 0.0)
        cur = G_sq * ((2.0 / 3.0) * d0 + (1.0 / 6.0) * up + (1.0 / 6.0) * dn)
        jj = np.where(e, np.abs(cur) / (dx * t_m) / 1e6, 0.0)
        J[:, :-1] = np.maximum(J[:, :-1], jj)
        J[:, 1:] = np.maximum(J[:, 1:], jj)
        # N-S faces
        nf = okc[:-1, :] & okc[1:, :]
        d0 = np.where(nf, v[1:, :] - v[:-1, :], 0.0)
        rt = np.zeros_like(d0)
        lf = np.zeros_like(d0)
        rt[:, 1:] = np.where(okc[:-1, 1:] & okc[1:, 1:],
                             v[1:, 1:] - v[:-1, 1:], 0.0)
        lf[:, :-1] = np.where(okc[:-1, :-1] & okc[1:, :-1],
                              v[1:, :-1] - v[:-1, :-1], 0.0)
        cur = G_sq * ((2.0 / 3.0) * d0 + (1.0 / 6.0) * rt + (1.0 / 6.0) * lf)
        jj = np.where(nf, np.abs(cur) / (dx * t_m) / 1e6, 0.0)
        J[:-1, :] = np.maximum(J[:-1, :], jj)
        J[1:, :] = np.maximum(J[1:, :], jj)
        jmag.append(np.where(okc, J, 0.0))
    vv = x[~np.isin(np.arange(n), src_nodes)]
    drop = float(vv.max() - vv.min()) if vv.size else 0.0
    # Raw J has a singularity at every re-entrant corner of the ideal 2D
    # conductor (a 45-degree bend reads ~1.8x the geometric value; a pad
    # edge is worse).  The audit check therefore uses J averaged over a
    # 2 mm window — much larger than a corner, much smaller than a neck —
    # and its 95th percentile; raw J stays for the map.
    from scipy.ndimage import uniform_filter
    kw = max(1, int(round(smooth_mm / copper.cell_mm)))
    jrob = [np.zeros_like(a) for a in jmag]
    for li, (a, cov) in enumerate(zip(jmag, covs)):
        occ = (cov >= MIN_COVER).astype(np.float32)
        num = uniform_filter(a * occ, size=kw)
        den = uniform_filter(occ, size=kw)
        jrob[li] = np.where(den > 1e-6, num / np.maximum(den, 1e-6), 0.0)
    jmax, j_at, j_layer = 0.0, None, None
    for li, a in enumerate(jmag):
        if not a.size or not np.isfinite(a).any():
            continue
        k = int(np.nanargmax(a))
        j, i = np.unravel_index(k, a.shape)
        if float(a[j, i]) > jmax:
            jmax, j_at, j_layer = float(a[j, i]), copper.xy_of(i, j), copper.layers[li]
    j_robust, jr_at = 0.0, None
    jvals = []
    for li, a in enumerate(jrob):
        if not a.size:
            continue
        k = int(np.argmax(a))
        j, i = np.unravel_index(k, a.shape)
        if float(a[j, i]) > j_robust:
            j_robust, jr_at = float(a[j, i]), copper.xy_of(i, j)
        m = covs[li] >= MIN_COVER
        jvals.append(a[m])
    j_p95 = float(np.percentile(np.concatenate(jvals), 95)) if jvals else 0.0
    vias_out = []
    for (k1, k2, x_, y_) in via_edges:
        vias_out.append({'x': round(x_, 2), 'y': round(y_, 2),
                         'current_a': round(abs((x[k1] - x[k2]) / r_via_ohm), 4)})
    return {'status': 'ok', 'net': net, 'current_a': current_a,
            'v_rail': v_rail, 'open_sinks': open_sinks,
            'components': int(ncomp),
            'drop_v': round(drop, 6),
            'drop_pct': round(100.0 * drop / v_rail, 3) if v_rail else None,
            'j_max_a_mm2': round(jmax, 2),
            'j_at': (float(round(j_at[0], 2)), float(round(j_at[1], 2))) if j_at else None,
            'j_layer': j_layer,
            'j_robust_a_mm2': round(j_robust, 2),
            'j_robust_at': (float(round(jr_at[0], 2)),
                            float(round(jr_at[1], 2))) if jr_at else None,
            'j_p95_a_mm2': round(j_p95, 2),
            'j_smooth': jrob,
            'v': v, 'j_by_layer': jmag, 'cell_mm': copper.cell_mm,
            'nodes': n, 'sources': list(sources), 'sinks': list(sinks),
            'via_currents': sorted(vias_out, key=lambda r: -r['current_a'])[:20]}
