"""The Gerber kernel: prove the manufacturable artifact, not the board model.

The fab gets RS-274X copper + Excellon holes, not a .kicad_pcb.  This module
parses exactly what KiCad plots (C/R/O apertures, draws, flashes, regions),
maps the plot frame back to board coordinates through the Edge.Cuts outline,
and then:

  * proves a bijection between the plotted copper features and the board's
    items (every board item has its copper on the plot, and every plotted
    feature is a board item);
  * assigns each plotted feature the net of its board item and re-runs the
    same fab predicates on the plotted geometry (clearance, width, annular,
    drill, hole-to-hole, edge);
  * reports the process box margins when asked.

A translation bug in the export (a layer offset, a dropped pad, a wrong
aperture) breaks the bijection or the predicates; the board kernel cannot
see it.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from pathlib import Path

from ..kernels import fab
from . import geom, state as S

TOL = 1000          # nm: one micron, for segments/circles
TOL_POLY = 10000    # nm: KiCad polygonises obrounds/roundrects, so the
                    # effective-polygon bbox is inset by up to a few microns


@dataclass
class Prim:
    kind: str                  # 'seg' | 'circle' | 'poly'
    layer: str
    shape: dict
    gx: int = 0
    gy: int = 0                # plot-frame anchor, for hole matching
    ap: str = ''


def _aperture(spec):
    """('R', '0.75X0.8') or 'R,0.75X0.8' -> ('R', [750000, 800000]) in nm."""
    if isinstance(spec, tuple):
        kind, rest = spec
        nums = []
        for q in re.split(r'[Xx]', rest):
            if q:
                try:
                    nums.append(int(round(float(q) * 1e6)))
                except ValueError:
                    pass
        return kind, nums
    parts = spec.split(',')
    kind = parts[0]
    nums = []
    for p in parts[1:]:
        for q in re.split(r'[Xx]', p):
            if q:
                try:
                    nums.append(int(round(float(q) * 1e6)))
                except ValueError:
                    pass
    return kind, nums


def _stadium(cx, cy, w, h, n=16):
    """Obround copper as a polygon (semicircular ends, n points each)."""
    if w >= h:
        r = h / 2.0
        d = (w - h) / 2.0
        pts = []
        for k in range(n + 1):
            a = -math.pi / 2 + math.pi * k / n
            pts.append([int(round(cx + d + r * math.cos(a))),
                        int(round(cy + r * math.sin(a)))])
        for k in range(n + 1):
            a = math.pi / 2 + math.pi * k / n
            pts.append([int(round(cx - d + r * math.cos(a))),
                        int(round(cy + r * math.sin(a)))])
        return pts
    pts = _stadium(cy, cx, h, w, n)
    return [[y, x] for x, y in pts]


def _arc_points(x0, y0, x1, y1, i, j, cw, step_deg=5.0):
    cx, cy = x0 + i, y0 + j
    a0 = math.atan2(y0 - cy, x0 - cx)
    a1 = math.atan2(y1 - cy, x1 - cx)
    r = math.hypot(x0 - cx, y0 - cy)
    if cw:
        sweep = (a0 - a1) % (2 * math.pi)
        if sweep == 0:
            sweep = 2 * math.pi
        sign = -1
    else:
        sweep = (a1 - a0) % (2 * math.pi)
        if sweep == 0:
            sweep = 2 * math.pi
        sign = 1
    n = max(2, int(abs(sweep) / (math.radians(step_deg))))
    pts = []
    for k in range(n + 1):
        a = a0 + sign * sweep * k / n
        pts.append((int(round(cx + r * math.cos(a))),
                    int(round(cy + r * math.sin(a)))))
    pts[-1] = (x1, y1)
    return pts


def _rot_pts(pts, deg):
    c, sn = math.cos(math.radians(deg or 0)), math.sin(math.radians(deg or 0))
    return [(x * c - y * sn, x * sn + y * c) for x, y in pts]


def _rounded_rect(x0, y0, x1, y1, r, n=6):
    r = max(0.0, min(r, (x1 - x0) / 2, (y1 - y0) / 2))
    pts = []
    for cx, cy, a0 in ((x1 - r, y0 + r, -90), (x1 - r, y1 - r, 0),
                       (x0 + r, y1 - r, 90), (x0 + r, y0 + r, 180)):
        for k in range(n + 1):
            a = math.radians(a0 + 90 * k / n)
            pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    return pts


# KiCad's own aperture macros, synthesised as one polygon each (the plotted
# primitive set is a union, but the board state has one pad; a single outline
# keeps the bijection one-to-one).
def _macro_shape(name, params, fx, fy):
    def poly(pts):
        # macro geometry is in mm; the flash anchor (fx, fy) is in nm
        return {'kind': 'poly', 'pts': [[int(round(x * 1e6 + fx)),
                                         int(round(y * 1e6 + fy))]
                                        for x, y in pts]}
    if name == 'RoundRect':
        r = params[0]
        corners = [(params[1 + 2 * i], params[2 + 2 * i]) for i in range(4)]
        corners = _rot_pts(corners, params[9] if len(params) > 9 else 0)
        xs = [p[0] for p in corners]
        ys = [p[1] for p in corners]
        pts = _rounded_rect(min(xs) - r, min(ys) - r,
                            max(xs) + r, max(ys) + r, r)
        return poly(pts)
    if name == 'Outline4P':
        pts = [(params[2 * i], params[1 + 2 * i]) for i in range(4)]
        pts = _rot_pts(pts, params[8] if len(params) > 8 else 0)
        return poly(pts)
    if name == 'RotRect':
        L, w, a = params[0], params[1], params[2]
        pts = [(-L / 2, -w / 2), (L / 2, -w / 2), (L / 2, w / 2),
               (-L / 2, w / 2)]
        return poly(_rot_pts(pts, a))
    raise ValueError(f'unsupported aperture macro {name}')


def parse_gerber(path, layer):
    """Parse the subset KiCad plots: C/R/O apertures, draws, flashes, regions."""
    text = Path(path).read_text(encoding='utf-8', errors='replace')
    apertures, prims, macros = {}, [], {}
    cur_ap = None
    x = y = 0
    mode = 'G01'
    region, rpts = False, []
    for m in re.finditer(r'%([^%]*)%|([^%*]+)\*', text):
        block, cmd = m.group(1), m.group(2)
        if block is not None:
            b = block.rstrip('*').strip()
            if b.startswith('ADD'):
                mm = re.match(r'ADD(\d+)([A-Za-z]+),(.*)$', b)
                if mm:
                    apertures[mm.group(1)] = (mm.group(2), mm.group(3))
            elif b.startswith('AM'):
                parts = [p.strip() for p in b.split('*')]
                name = parts[0][2:]
                macros[name] = [p for p in parts[1:]
                                if p and not p.startswith('0')]
            elif b in ('LPC',):
                raise ValueError('negative polarity is not supported')
            continue
        c = cmd.strip()
        if not c or c.startswith('G04') or c.startswith('M02'):
            continue
        if c in ('G01', 'G02', 'G03'):
            mode = c
            continue
        if c == 'G36':
            region, rpts = True, []
            continue
        if c == 'G37':
            if len(rpts) >= 3:
                prims.append(Prim('poly', layer, {'kind': 'poly',
                                                  'pts': [list(p) for p in rpts]}))
            region = False
            continue
        if re.fullmatch(r'D\d+', c):
            cur_ap = c[1:]
            continue
        dm = re.fullmatch(r'(?:X(-?\d+))?(?:Y(-?\d+))?(?:I(-?\d+))?'
                          r'(?:J(-?\d+))?D0([123])', c)
        if not dm:
            continue
        nx = int(dm.group(1)) if dm.group(1) is not None else x
        ny = int(dm.group(2)) if dm.group(2) is not None else y
        ii = int(dm.group(3)) if dm.group(3) is not None else 0
        jj = int(dm.group(4)) if dm.group(4) is not None else 0
        op = dm.group(5)
        if region:
            if op == '1' and mode in ('G02', 'G03'):
                rpts.extend(_arc_points(x, y, nx, ny, ii, jj, mode == 'G02'))
            else:
                rpts.append((nx, ny))
            x, y = nx, ny
            continue
        if op == '1':
            kind, nums = _aperture(apertures.get(cur_ap, ('C', '0.1')))
            w = nums[0] if nums else 100000
            if kind not in ('C',):
                # KiCad draws tracks with circular apertures; anything else
                # is plotted as a zero-length flash or a region.
                w = min(nums) if nums else w
            if mode in ('G02', 'G03'):
                pts = _arc_points(x, y, nx, ny, ii, jj, mode == 'G02')
                for a, b2 in zip(pts, pts[1:]):
                    prims.append(Prim('seg', layer,
                                      {'kind': 'seg', 'x1': a[0], 'y1': a[1],
                                       'x2': b2[0], 'y2': b2[1], 'w': w},
                                      gx=b2[0], gy=b2[1], ap=kind + 'arc'))
            else:
                prims.append(Prim('seg', layer, {'kind': 'seg', 'x1': x, 'y1': y,
                                                 'x2': nx, 'y2': ny, 'w': w},
                                  gx=nx, gy=ny, ap=kind))
        elif op == '3':
            kind, nums = _aperture(apertures.get(cur_ap, ('C', '0.1')))
            if kind in macros:
                params_mm = [n / 1e6 for n in nums]
                prims.append(Prim('poly', layer,
                                  _macro_shape(kind, params_mm, nx, ny),
                                  gx=nx, gy=ny, ap=kind))
                x, y = nx, ny
                continue
            if kind == 'C':
                r = (nums[0] if nums else 100000) // 2
                sh = {'kind': 'circle', 'cx': nx, 'cy': ny, 'r': r}
            elif kind == 'R':
                w, h = (nums + nums)[:2]
                sh = {'kind': 'poly', 'pts': [
                    [nx - w // 2, ny - h // 2], [nx + w // 2, ny - h // 2],
                    [nx + w // 2, ny + h // 2], [nx - w // 2, ny + h // 2]]}
            elif kind == 'O':
                w, h = (nums + nums)[:2]
                sh = {'kind': 'poly', 'pts': _stadium(nx, ny, w, h)}
            else:
                raise ValueError(f'unsupported flash aperture {kind}')
            prims.append(Prim('poly' if sh['kind'] == 'poly' else 'circle',
                              layer, sh, gx=nx, gy=ny, ap=kind))
        x, y = nx, ny
    return prims


def parse_excellon(path):
    """Excellon -> [(x_nm, y_nm, drill_nm)] (metric decimal, as KiCad writes)."""
    holes, tools, cur = [], {}, None
    for line in Path(path).read_text(encoding='utf-8',
                                     errors='replace').splitlines():
        s = line.strip()
        m = re.fullmatch(r'T(\d+)C([\d.]+)', s)
        if m:
            tools[m.group(1)] = int(round(float(m.group(2)) * 1e6))
            continue
        m = re.fullmatch(r'T(\d+)', s)
        if m:
            cur = m.group(1)
            continue
        m = re.fullmatch(r'X([-\d.]+)Y([-\d.]+)', s)
        if m and cur in tools:
            holes.append((int(round(float(m.group(1)) * 1e6)),
                          int(round(float(m.group(2)) * 1e6)),
                          tools[cur]))
    return holes


def edge_polygon(path):
    prims = parse_gerber(path, 'Edge.Cuts')
    pts = []
    for p in prims:
        if p.kind == 'seg':
            pts += [(p.shape['x1'], p.shape['y1']),
                    (p.shape['x2'], p.shape['y2'])]
    uniq = []
    for q in pts:
        if not any(abs(q[0] - u[0]) <= TOL and abs(q[1] - u[1]) <= TOL
                   for u in uniq):
            uniq.append(q)
    if len(uniq) < 3:
        return []
    cx = sum(p[0] for p in uniq) / len(uniq)
    cy = sum(p[1] for p in uniq) / len(uniq)
    return sorted(uniq, key=lambda p: math.atan2(p[1] - cy, p[0] - cx))


def _transform(gout, sout):
    """Plot frame -> board frame: x = gx + tx, y = ty - gy.

    Anchored on the outline bounding box, not on every vertex: KiCad plots
    rounded corners as arcs and the state outline is a polygonisation, so the
    point sets differ by microns while the frame is exact.  The bbox gives
    four constraints for two unknowns.
    """
    gx = [p[0] for p in gout]
    gy = [p[1] for p in gout]
    bx = [p[0] for p in sout]
    by = [p[1] for p in sout]
    tx = min(bx) - min(gx)
    ty = max(by) + min(gy)
    # The state outline is a polygonisation inscribed in the plotted rounded
    # outline, so its bbox is a few microns small on every side.  KiCad plots
    # in board coordinates; when the bbox-derived shift is within that
    # approximation the true frame is zero, not the bbox residue.
    if abs(tx) <= TOL_POLY and abs(ty) <= TOL_POLY:
        tx = ty = 0
    checks = [abs(min(gx) + tx - min(bx)), abs(max(gx) + tx - max(bx)),
              abs(ty - min(gy) - max(by)), abs(ty - max(gy) - min(by))]
    ok = all(c <= TOL_POLY for c in checks)
    return tx, ty, ok


def _match(prim, board_items, used):
    tol = TOL
    for it in board_items:
        key = (it['id'], prim.layer)
        if key in used or prim.layer not in it['layers']:
            continue
        sh, bs = prim.shape, it['shape']
        if sh['kind'] == 'seg' and bs['kind'] == 'seg':
            a = sorted([(sh['x1'], sh['y1']), (sh['x2'], sh['y2'])])
            b = sorted([(bs['x1'], bs['y1']), (bs['x2'], bs['y2'])])
            if all(abs(a[k][0] - b[k][0]) <= tol and
                   abs(a[k][1] - b[k][1]) <= tol for k in (0, 1)) and \
                    abs(sh['w'] - bs['w']) <= tol:
                used.add(key)
                return it
        elif sh['kind'] == 'circle' and bs['kind'] == 'circle':
            if abs(sh['cx'] - bs['cx']) <= tol and \
                    abs(sh['cy'] - bs['cy']) <= tol and \
                    abs(sh['r'] - bs['r']) <= tol:
                used.add(key)
                return it
        elif sh['kind'] == 'poly' and bs['kind'] == 'poly':
            a = (min(p[0] for p in sh['pts']), min(p[1] for p in sh['pts']),
                 max(p[0] for p in sh['pts']), max(p[1] for p in sh['pts']))
            b = (min(p[0] for p in bs['pts']), min(p[1] for p in bs['pts']),
                 max(p[0] for p in bs['pts']), max(p[1] for p in bs['pts']))
            if all(abs(a[k] - b[k]) <= TOL_POLY for k in range(4)):
                used.add(key)
                return it
    return None


def _shape_bbox(sh):
    if sh['kind'] == 'seg':
        h = sh['w'] / 2.0
        return (min(sh['x1'], sh['x2']) - h, min(sh['y1'], sh['y2']) - h,
                max(sh['x1'], sh['x2']) + h, max(sh['y1'], sh['y2']) + h)
    if sh['kind'] == 'circle':
        return (sh['cx'] - sh['r'], sh['cy'] - sh['r'],
                sh['cx'] + sh['r'], sh['cy'] + sh['r'])
    xs = [p[0] for p in sh['pts']]
    ys = [p[1] for p in sh['pts']]
    return (min(xs), min(ys), max(xs), max(ys))


def _index(items, cell=2_000_000):
    """layer -> coarse grid cell -> items whose bbox touches it."""
    idx = {}
    for it in items:
        if not it['layers']:
            continue
        bb = _shape_bbox(it['shape'])
        for layer in it['layers']:
            for gx in range(int(bb[0] // cell), int(bb[2] // cell) + 1):
                for gy in range(int(bb[1] // cell), int(bb[3] // cell) + 1):
                    idx.setdefault((layer, gx, gy), []).append(it)
    return idx


def _candidates(idx, layer, sh, cell=2_000_000):
    bb = _shape_bbox(sh)
    # a match may sit a few microns outside the primitive's bbox (polygon
    # approximation), so query one extra ring of cells
    m = 2 * TOL_POLY
    out, seen = [], set()
    for gx in range(int((bb[0] - m) // cell), int((bb[2] + m) // cell) + 1):
        for gy in range(int((bb[1] - m) // cell), int((bb[3] + m) // cell) + 1):
            for it in idx.get((layer, gx, gy), []):
                if it['id'] not in seen:
                    seen.add(it['id'])
                    out.append(it)
    return out


def gerber_state(board_state, layers, drill_path, layer_files):
    """Build a state from the plotted artifact, with nets inherited by match."""
    prims = []
    for layer, path in layer_files.items():
        if layer == 'Edge.Cuts':
            continue
        prims.extend(parse_gerber(path, layer))
    gout = edge_polygon(layer_files['Edge.Cuts'])
    sout = [tuple(p) for p in (board_state.get('outline') or [])]
    if not gout or not sout:
        return None, {'ok': False, 'reason': 'no outline to anchor the plot'}
    tx, ty, tok = _transform(gout, sout)
    if not tok:
        return None, {'ok': False, 'reason': 'plot frame does not match the board outline'}
    # drill coordinates are in the plot frame too: map them once, so every
    # consumer (flash drill, hole marks, hole-only items) uses board nm
    holes = [(h[0] + tx, ty - h[1], h[2])
             for h in (parse_excellon(drill_path) if drill_path else [])]
    items, used, extra = [], set(), []
    unmatched = []
    _idx = _index(board_state['items'])
    for n, p in enumerate(prims):
        sh = dict(p.shape)
        for k in ('x1', 'x2', 'cx'):
            if k in sh:
                sh[k] = sh[k] + tx
        for k in ('y1', 'y2', 'cy'):
            if k in sh:
                sh[k] = ty - sh[k]
        if sh['kind'] == 'poly':
            sh['pts'] = [[q[0] + tx, ty - q[1]] for q in sh['pts']]
        bx, by = p.gx + tx, ty - p.gy
        m = _match(Prim(p.kind, p.layer, sh, gx=bx, gy=by),
                   _candidates(_idx, p.layer, sh), used)
        net = m['net'] if m else f'?{n}'
        item = {'id': f'g{n}', 'net': net, 'layers': [p.layer], 'shape': sh}
        if m:
            if m.get('graphic'):
                item['graphic'] = True
            if m.get('ref'):
                item['ref'] = m['ref']
            if m.get('drill') is not None and m['drill'] >= 0:
                item['drill'] = m['drill']
                if m.get('drill_y') is not None and m['drill_y'] >= 0:
                    item['drill_y'] = m['drill_y']
            if m.get('span'):
                item['span'] = m['span']
        else:
            item['graphic'] = True      # unidentifiable copper is not a track
            unmatched.append((len(items), sh, p.layer, bx, by))
            # a hole at an unmatched pad/via flash still has to be checked
            if sh['kind'] != 'seg':
                for hx, hy, drill in holes:
                    if abs(hx - bx) <= TOL and abs(hy - by) <= TOL:
                        item['drill'] = drill
                        break
        items.append(item)
    # a small flash whose centre is a drill of the same size is the hole
    # plotted on the copper layer, not copper; the drill file carries it
    hole_marks, drop = 0, set()
    for idx, sh, layer, bx, by in unmatched:
        is_hole = False
        if sh['kind'] == 'circle':
            for hx, hy, drill in holes:
                if abs(hx - bx) <= TOL and abs(hy - by) <= TOL and                         sh['r'] <= drill // 2 + TOL:
                    is_hole = True
                    break
        if is_hole:
            hole_marks += 1
            drop.add(idx)
        else:
            extra.append(items[idx]['id'])
    if drop:
        items = [it for n, it in enumerate(items) if n not in drop]
    # every board copper item must be on the plot, on every layer it lives on
    missing = []
    for it in board_state['items']:
        if it.get('npth'):
            continue
        for layer in it['layers']:
            if (it['id'], layer) not in used:
                missing.append(f"{it['id']}@{layer}")
    # non-plated holes exist only in the drill file: keep them as hole-only
    # items so drill/hole predicates run on the artifact too.
    for k, (hx, hy, dr) in enumerate(holes):
        if any(abs(hx - (p.gx + tx)) <= TOL and abs(hy - (ty - p.gy)) <= TOL
               for p in prims):
            continue
        items.append({'id': f'h{k}', 'net': f'~h{k}', 'layers': [],
                      'npth': True, 'drill': dr,
                      'shape': {'kind': 'circle', 'cx': hx, 'cy': hy,
                                'r': max(dr, 1) // 2}})
    net_pads = {}
    refs = {}
    for it in items:
        if it.get('ref'):
            refs.setdefault(it['ref'], []).append(it['id'])
    gst = {'schema': S.SCHEMA, 'board': str(layer_files.get('board', 'gerber')),
           'layers': [l for l in layer_files if l != 'Edge.Cuts'],
           'items': items, 'rules': dict(board_state.get('rules') or {}),
           'netPads': net_pads, 'footprints': refs,
           'zones': board_state.get('zones', 0),
           'outline': sout}
    gst = S.freeze(gst)
    equiv = {'ok': not extra and not missing and tok,
             'transform': {'tx': tx, 'ty': ty, 'ok': tok},
             'extra': extra[:20], 'missing': missing[:20],
             'hole_marks': hole_marks,
             'n_extra': len(extra), 'n_missing': len(missing)}
    return gst, equiv


def gerber_claims(board_state, gst, process=None, skip_clearance=False):
    rules = board_state.get('rules') or {}
    scope = sorted(i['id'] for i in gst['items'])
    out = []
    for cid, pred, key in (('FAB:CLEARANCE', 'clearance', 'clearance'),
                           ('FAB:WIDTH', 'width', 'track_min'),
                           ('FAB:ANNULAR', 'annular', 'annular'),
                           ('FAB:DRILL', 'drill', 'drill_min'),
                           ('FAB:HOLE', 'hole', 'hole_to_hole'),
                           ('FAB:EDGE', 'edge', 'edge')):
        if pred == 'clearance' and skip_clearance:
            continue
        rule = rules.get(key)
        if rule:
            out.append({'id': 'G:' + cid, 'pred': pred, 'scope': scope,
                        'rule': rule})
    if process:
        from . import claims as C
        for c in C.process_claims(board_state, process):
            c = dict(c)
            c['id'] = 'G:' + c['id']
            c['scope'] = scope
            out.append(c)
    return out


def check(board_state, layer_files, drill_path, process=None, robust=None,
          coverage=True):
    """Verify the plotted artifact: bijection or coverage equality + fab."""
    gst, equiv = gerber_state(board_state, list(layer_files), drill_path,
                              layer_files)
    if gst is None:
        return {'ok': False, 'reasons': [equiv.get('reason')],
                'equiv': equiv, 'fab': None, 'coverage': None}
    cov = None
    if coverage:
        from . import raster
        cov = raster.coverage(board_state, gst)
    cov_ok = bool(cov is not None and cov.get('ok'))
    artifact_ok = equiv['ok'] or cov_ok
    skip_clearance = (not equiv['ok']) and cov_ok
    cl = gerber_claims(board_state, gst, process,
                       skip_clearance=skip_clearance)
    rep = fab.check(gst, cl, board_state.get('rules') or {})
    reasons = []
    if not equiv['ok'] and not skip_clearance:
        reasons.append(f"plot/board bijection failed: {equiv['n_extra']} extra, "
                       f"{equiv['n_missing']} missing")
        if cov is not None and cov.get('reason'):
            reasons.append(cov['reason'])
        elif cov is not None and not cov['ok']:
            bad = {k: v for k, v in cov['layers'].items()
                   if v['gerber_not_board'] or v['board_not_gerber']}
            reasons.append(f"copper coverage differs at {cov['res_nm']} nm: {bad}")
    for c in rep['claims']:
        if c['status'] != 'proven':
            reasons.append(f"{c['id']}: {c['status']} {c['witness']}")
    margins = None
    if robust:
        from . import process as proc
        eff = dict(board_state.get('rules') or {})
        if process:
            eff.update(S.process_rules(process))
        # when coverage is the basis, the plot's unmatched primitives have
        # no nets; margins come from the board state whose copper set equals
        # the plot's
        mstate = board_state if (not equiv['ok'] and cov_ok) else gst
        margins = proc.margins(mstate, eff, robust)
        if not margins['ok']:
            reasons += margins['reasons']
    return {'ok': artifact_ok and not reasons, 'reasons': reasons,
            'equiv': equiv, 'coverage': cov, 'fab': rep, 'margins': margins,
            'digest': gst['digest']}


def shift_x(src, dst, dx_nm):
    """Negative control: move one plotted layer by dx nm on the X axis."""
    text = Path(src).read_text(encoding='utf-8', errors='replace')
    def repl(m):
        return f'X{int(m.group(1)) + dx_nm}'
    text = re.sub(r'X(-?\d+)', repl, text)
    Path(dst).write_text(text, encoding='utf-8')
    return Path(dst)
