"""Per-net susceptibility index — a labelled proxy, not a field solve.

What it answers: which nets are most exposed to a switching neighbour,
and which neighbours.  It is the "electromagnetic susceptibility map"
the design document asks for at the stage where a field solver is not
available (or not yet approved to run).

The physics it *does* use, and the parts it invents:

  * loop area of a victim net: sum of segment length x height above the
    nearest reference plane.  The height comes from the stackup when the
    project has one; otherwise 1.6 mm for a 2-layer board and 0.2 mm for
    anything thicker, and the finding says so.
  * coupling: loop area of the victim x loop area of the aggressor /
    distance^2, distance = closest approach of the two nets' copper.
    A 1/d^2 near-field term, no spectrum, no shielding, no stackup
    resonance.
  * aggressor weight: a relative number from the net's name (clock and
    switching nodes 2.0, differential pairs 1.5, other named aggressors
    1.0).  There is no current, no di/dt, no edge rate anywhere in
    KiCad's data model, so no absolute field strength is claimed.

Everything this module returns is therefore an estimate: the caller
must emit it with ``status='estimate'`` (capped at P1) and never as a
measured field.  openEMS (``tools/em_adapters.py``) is the upgrade path.

    run(project_dir) -> {'nets': [...], 'pairs': [...], 'threshold', ...}
"""
from __future__ import annotations

import json
import math
from pathlib import Path

# name heuristics: what tends to be a victim / an aggressor
SENSITIVE_HINTS = ('adc', 'ain', 'sense', 'vref', 'ref', 'comp', 'cur',
                   'fb', 'dac', 'op', 'ain', 'vin', 'vout', 'audio', 'in+', 'in-')
AGGRESSOR_HINTS = ('clk', 'sclk', 'sck', 'scl', 'sda', 'pwm', 'sw', 'd+', 'd-',
                   'tx', 'rx', 'spi', 'usb', 'can', 'eth', 'sd', 'mosi', 'miso',
                   'drv', 'gate')
POWERISH = ('gnd', 'agnd', 'pgnd', 'vss', 'vcc', 'vdd', 'vbat', 'vbus',
            '+3v3', '+5v0', '+5v', '+12', '+24', '3v3', '5v0', 'vref_p',
            'net-(', '__plane__')

MAX_SEGMENTS = 240          # per net, for the distance search
SEARCH_SEGMENTS = 60        # per net, in the pairwise distance search
FAR_MM = 12.0               # beyond this, 1/d^2 coupling is not counted
TIME_BUDGET_S = 2.0         # the gate must not become a solver
DEFAULT_H_2L = 1.6
DEFAULT_H_MULTI = 0.2


def net_kind(net: str) -> str:
    n = (net or '').strip().lower()
    if not n or n == '__plane__' or any(h in n for h in POWERISH):
        return 'power'
    if any(h in n for h in AGGRESSOR_HINTS):
        return 'aggressor'
    if any(h in n for h in SENSITIVE_HINTS):
        return 'sensitive'
    return 'signal'


def aggressor_weight(net: str) -> float:
    n = (net or '').lower()
    if any(h in n for h in ('clk', 'sclk', 'sck', 'pwm', 'sw')):
        return 2.0
    if any(h in n for h in ('d+', 'd-', 'tx', 'rx', 'usb', 'can')):
        return 1.5
    return 1.0


def _seg_dist(a, b):
    """Distance between segment a=(p,q) and b=(p,q)."""
    (x1, y1), (x2, y2) = a
    (x3, y3), (x4, y4) = b
    d = (x2 - x1) * (y4 - y3) - (y2 - y1) * (x4 - x3)
    if abs(d) < 1e-12:
        return min(_pt_seg((x1, y1), b), _pt_seg((x2, y2), b),
                   _pt_seg((x3, y3), a), _pt_seg((x4, y4), a))
    t = ((x3 - x1) * (y4 - y3) - (y3 - y1) * (x4 - x3)) / d
    u = ((x3 - x1) * (y2 - y1) - (y3 - y1) * (x2 - x1)) / d
    if 0 <= t <= 1 and 0 <= u <= 1:
        return 0.0
    return min(_pt_seg((x1, y1), b), _pt_seg((x2, y2), b),
               _pt_seg((x3, y3), a), _pt_seg((x4, y4), a))


def _pt_seg(p, seg):
    (ax, ay), (bx, by) = seg
    dx, dy = bx - ax, by - ay
    l2 = dx * dx + dy * dy
    if l2 == 0:
        return math.hypot(p[0] - ax, p[1] - ay)
    t = max(0.0, min(1.0, ((p[0] - ax) * dx + (p[1] - ay) * dy) / l2))
    return math.hypot(p[0] - ax - t * dx, p[1] - ay - t * dy)


def _layer_height(project_dir: Path, copper_layers: int) -> tuple:
    """(height to the nearest reference plane in mm, where it came from)."""
    p = Path(project_dir) / "stackup.yaml"
    if p.exists():
        try:
            import yaml
            d = yaml.safe_load(p.read_text(encoding='utf-8')) or {}
            layers = d.get('layers') or []
            if isinstance(layers, list) and layers:
                tops = [float(l.get('dielectric_mm') or 0.0) for l in layers
                        if isinstance(l, dict)]
                tops = [t for t in tops if t > 0]
                if tops:
                    return round(min(tops), 4), 'stackup.yaml'
            if d.get('h_mm'):
                return round(float(d['h_mm']), 4), 'stackup.yaml h_mm'
        except Exception:
            pass
    if copper_layers and copper_layers >= 4:
        return DEFAULT_H_MULTI, 'default 0.2 mm (4+ layers)'
    return DEFAULT_H_2L, 'default 1.6 mm (2 layers)'


def _bbox(segs):
    xs = [p[0] for s in segs for p in s]
    ys = [p[1] for s in segs for p in s]
    return (min(xs), min(ys), max(xs), max(ys))


def _bbox_gap(a, b) -> float:
    gx = max(a[0] - b[2], b[0] - a[2], 0.0)
    gy = max(a[1] - b[3], b[1] - a[3], 0.0)
    return (gx * gx + gy * gy) ** 0.5


def _copper_by_net(items) -> dict:
    """net -> {'segs': [(p,q)], 'area': loop area per segment, 'w': half width}"""
    out = {}
    for it in items:
        if len(it) < 6:
            continue
        kind, layer, p, q, width, net = it[0], it[1], it[2], it[3], it[4], it[5]
        if net in ('', None) or net == '__plane__':
            continue
        d = out.setdefault(net, {'segs': [], 'widths': [], 'layers': set()})
        d['layers'].add(layer)
        if kind == 'seg' and q is not None:
            d['segs'].append((tuple(p), tuple(q)))
            d['widths'].append(float(width or 0.0))
        elif kind == 'via':
            d['segs'].append(((p[0], p[1]), (p[0], p[1])))
            d['widths'].append(float(width or 0.0))
        elif kind == 'pad':
            half = float(width or 0.0) / 2.0
            d['segs'].append(((p[0] - half, p[1]), (p[0] + half, p[1])))
            d['widths'].append(float(width or 0.0))
    return out


def run(project_dir, pcb_path=None, threshold: float = 40.0,
        h_mm: float = None) -> dict:
    """Susceptibility index per net, and the worst victim/aggressor pairs."""
    from .dcsolve import parse_copper

    project_dir = Path(project_dir)
    pcb = Path(pcb_path) if pcb_path else None
    if pcb is None:
        boards = sorted((project_dir / 'pcb').rglob('*.kicad_pcb'))
        if not boards:
            return {'status': 'blocked', 'why': 'no .kicad_pcb under pcb/'}
        pcb = boards[0]
    items = parse_copper(pcb)
    if not items:
        return {'status': 'blocked', 'why': f'no copper parsed from {pcb.name}'}

    copper_layers = 0
    try:
        import re
        txt = pcb.read_text(encoding='utf-8', errors='replace')
        m = re.search(r'\(layers?\s+(\d+)\s+', txt)
        copper_layers = int(m.group(1)) if m else 0
    except Exception:
        copper_layers = 0
    if h_mm is None:
        h_mm, h_from = _layer_height(project_dir, copper_layers)
    else:
        h_from = 'caller'

    by_net = _copper_by_net(items)
    rows, pairs = [], []
    nets = sorted(by_net)
    # the pairwise search is bounded three ways so the gate stays cheap:
    # bounding boxes first (1/d^2 at 12 mm is negligible), a stride so no
    # net offers more than SEARCH_SEGMENTS segments, and a wall clock.
    boxes = {n: _bbox(by_net[n]['segs'][:MAX_SEGMENTS]) for n in nets}
    kinds = {n: net_kind(n) for n in nets}
    areas = {}
    for n in nets:
        segs = by_net[n]['segs'][:MAX_SEGMENTS]
        areas[n] = sum(math.hypot(q[0] - p[0], q[1] - p[1]) * h_mm
                       for (p, q) in segs)
    import time as _time
    t0 = _time.time()
    truncated = False
    aggressors = [n for n in nets if kinds[n] == 'aggressor']
    for v in nets:
        dv = by_net[v]
        segs_v = dv['segs'][:MAX_SEGMENTS]
        if not segs_v:
            continue
        area = areas[v]
        vkind = kinds[v]
        if vkind in ('power',):
            continue
        best = None
        total = 0.0
        for a in aggressors:
            if a == v:
                continue
            if _time.time() - t0 > TIME_BUDGET_S:
                truncated = True
                break
            if _bbox_gap(boxes[v], boxes[a]) > FAR_MM:
                continue
            da = by_net[a]
            sv = segs_v[::max(1, len(segs_v) // SEARCH_SEGMENTS)] or segs_v
            sa_all = da['segs'][:MAX_SEGMENTS]
            sa = sa_all[::max(1, len(sa_all) // SEARCH_SEGMENTS)] or sa_all
            d = min(_seg_dist(x, y) for x in sv for y in sa)
            if d > FAR_MM:
                continue
            weight = aggressor_weight(a)
            # a dimensionless 0..100 index: 20*log10(1 + coupling), where
            # coupling = (A_v/100)(A_a/100)*w*(0.5/d)^2.  The log keeps the
            # number readable across four orders of magnitude; the
            # absolute coupling is a proxy, which the index does not hide.
            coupling = ((area / 100.0) * (areas[a] / 100.0) * weight
                        * (0.5 / max(d, 0.1)) ** 2)
            score = round(min(100.0, 20.0 * math.log10(1.0 + coupling)), 2)
            # the net's index is its WORST single coupling: a sum over
            # every neighbour saturates at 100 for half a board and stops
            # discriminating
            total = max(total, score)
            # two nets cannot share copper; a 0.0 here is the parse's
            # rounding at adjacent pads, so report the floor
            row = {'victim': v, 'aggressor': a, 'd_mm': round(max(d, 0.02), 3),
                   'area_v_mm2': round(area, 2), 'area_a_mm2': round(areas[a], 2),
                   'weight': weight, 'score': round(score, 2)}
            pairs.append(row)
            if best is None or score > best['score']:
                best = row
        if truncated:
            break
        rows.append({'net': v, 'kind': vkind,
                     'area_mm2': round(area, 2),
                     'index': round(total, 2),
                     'worst': best,
                     'layers': sorted(dv['layers'])})
    rows.sort(key=lambda r: -r['index'])
    pairs.sort(key=lambda r: -r['score'])
    return {
        'status': 'ok',
        'pcb': str(pcb.name),
        'threshold': threshold,
        'h_mm': h_mm,
        'h_from': h_from,
        'copper_layers': copper_layers,
        'nets': rows[:40],
        'pairs': pairs[:40],
        'assumption': (f'loop area = segment length x {h_mm} mm to the nearest '
                       f'reference plane ({h_from}); coupling ~ A_v*A_a/d^2 '
                       f'with relative aggressor weights from net names; no '
                       f'current spectrum and no field solve'),
        'truncated': truncated,
        'elapsed_s': round(_time.time() - t0, 3),
        'over': [r for r in rows if r['index'] > threshold][:10],
    }


def main(argv=None):
    import sys
    args = list(argv or sys.argv[1:])
    if not args:
        print('usage: python -m fluxpcb.tools.susceptibility <project_dir> [pcb]')
        return 2
    r = run(args[0], args[1] if len(args) > 1 else None)
    print(json.dumps(r, ensure_ascii=False, indent=1)[:4000])
    return 0 if r.get('status') == 'ok' else 1


if __name__ == '__main__':
    raise SystemExit(main())
