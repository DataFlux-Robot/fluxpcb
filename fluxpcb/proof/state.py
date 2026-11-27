"""Canonical board state: the object every kernel reasons about.

The extractor (pcbnew) is the trusted bridge; the digest is the drift check.
Python builds `canonical(state)` and Lean rebuilds it from the parsed JSON —
any field the parser drops or renames changes the digest and the kernel
refuses the request (`drift: true`).

Zones are not extracted yet: every benchmark board has zero zones, and the
H7 pour is a later milestone.  A board with zones yields `unknown` fab claims
rather than a silent pass.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

SCHEMA = 'fluxpcb-state-2'


def board_rules(board_path, defaults=None):
    """The fab rules for a board, from its project file, with provenance.

    KiCad enforces ``max(design_settings.rules.min_clearance, netclass
    clearance)`` per pair.  One global fab rule can only be the *least*
    requirement any pair faces — ``max(global, min(class))`` — because a
    stricter rule would refute pairs the board's own DRC accepts.  A zero
    (KiCad's "unconstrained") falls back to the default netclass.
    """
    rules = dict(defaults or {'clearance': 200000, 'track_min': 150000})
    src = {'clearance': 'default 0.2 mm (project declares none)',
           'track_min': 'default 0.15 mm (project declares none)'}
    pro = Path(board_path).with_suffix('.kicad_pro')
    data = {}
    if pro.exists():
        try:
            data = json.loads(pro.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            data = {}
    board = data.get('board') or {}
    ds = board.get('design_settings') or {}
    r = ds.get('rules') or {}
    ns = data.get('net_settings') or board.get('net_settings') or {}
    classes = [float(c['clearance']) for c in ns.get('classes', [])
               if c.get('clearance')]
    c = r.get('min_clearance') or 0.0
    if classes or c:
        val = max(float(c), min(classes)) if classes else float(c)
        rules['clearance'] = int(round(val * 1e6))
        src['clearance'] = (f'netclass clearance min {min(classes)} mm'
                            if classes else f'project min_clearance {c} mm')
        if classes and c:
            src['clearance'] += f' (board floor {c} mm)'
    w = r.get('min_track_width')
    if w:
        rules['track_min'] = int(round(float(w) * 1e6))
        src['track_min'] = f'project min_track_width {w} mm'
    # these four only matter when the project's own DRC treats them as
    # errors; a warning-level rule must not change the bench verdict.
    sev = ds.get('rule_severities') or {}
    for key, field, drc in (('annular', 'min_via_annular_width',
                             'annular_width'),
                            ('hole_to_hole', 'min_hole_to_hole', 'hole_to_hole'),
                            ('edge', 'min_copper_edge_clearance',
                             'copper_edge_clearance'),
                            ('drill_min', 'min_through_hole_diameter',
                             'drill_out_of_range')):
        v = r.get(field)
        if v and sev.get(drc) == 'error':
            rules[key] = int(round(float(v) * 1e6))
            src[key] = f'project {field} {v} mm (severity error)'
    return rules, src


def process_rules(profile='jlc-1oz-2to4l-2026-11-25'):
    """Manufacturability limits from the fab profile (nm), as extra rules."""
    d = Path(__file__).resolve().parent.parent / 'mfg' / 'data'
    files = sorted(d.glob('jlc_*.json'))
    data = None
    for f in files:
        try:
            cand = json.loads(f.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            continue
        if cand.get('profile_id') == profile or profile in ('jlc', 'jlc-1oz'):
            data = cand
            break
    if data is None:
        return {}
    p = data.get('parameters') or {}
    out = {}
    for key, field in (('annular', 'annular_ring'), ('hole_to_hole',
                                                     'hole_to_hole'),
                       ('edge', 'copper_edge'), ('drill_min', 'via_drill')):
        v = (p.get(field) or {}).get('limit_mm')
        if v:
            out[key] = int(round(float(v) * 1e6))
    return out


def shape_str(sh):
    k = sh['kind']
    if k == 'seg':
        return f"seg:{sh['x1']},{sh['y1']},{sh['x2']},{sh['y2']},{sh['w']}"
    if k == 'circle':
        return f"cir:{sh['cx']},{sh['cy']},{sh['r']}"
    if k == 'poly':
        return "poly:" + ";".join(f"{x},{y}" for x, y in sh['pts'])
    raise ValueError(f'shape {k}')


def item_line(i):
    line = f"i|{i['id']}|{i['net']}|{','.join(i['layers'])}|{shape_str(i['shape'])}"
    if i.get('drill') is not None and i['drill'] >= 0:
        line += f"|d={i['drill']}"
        if i.get('drill_y') is not None and i['drill_y'] >= 0:
            line += f",{i['drill_y']}"
    if i.get('span'):
        line += f"|s={i['span']}"
    if i.get('npth'):
        line += "|npth"
    if i.get('graphic'):
        line += "|gr"
    return line


def canonical(st):
    items = sorted(st['items'], key=lambda i: i['id'])
    rules = sorted(st['rules'].items())
    nets = sorted(st['netPads'].items())
    fps = sorted((st.get('footprints') or {}).items())
    lines = ['v2', st['schema'], st['board'], ','.join(st['layers'])]
    lines += [item_line(i) for i in items]
    lines += [f"r|{k}={v}" for k, v in rules]
    lines += [f"n|{n}:{','.join(ids)}" for n, ids in nets]
    lines += [f"f|{r}:{','.join(ids)}" for r, ids in fps]
    lines += [f"z|{st.get('zones', 0)}"]
    ol = st.get('outline') or []
    if ol:
        lines += ['o|' + ';'.join(f"{x},{y}" for x, y in ol)]
    return '\n'.join(lines)


def item_hashes(st):
    """id -> hash of that item's canonical line (for proof-store freshness)."""
    return {i['id']: hashlib.sha256(item_line(i).encode('utf-8')).hexdigest()[:16]
            for i in st['items']}


def fnv1a(s):
    h = 14695981039346656037
    for b in s.encode('utf-8'):
        h ^= b
        h = (h * 1099511628211) & ((1 << 64) - 1)
    return h


def digest(st):
    return str(fnv1a(canonical(st)))


def freeze(st):
    st = dict(st)
    st.setdefault('schema', SCHEMA)
    st['digest'] = digest(st)
    return st


def board_fp(st):
    """Content hash of the item set, independent of the digest field."""
    return hashlib.sha256(canonical(st).encode('utf-8')).hexdigest()[:16]


# ------------------------------------------------------------------ extract
def _copper_names(board):
    """(layer ids in stack order, unique keys).

    The stack order is what the Gerber `L<k>` headers number, so it must come
    from CuStack(), not from the layer-id order.  Custom stacks can give two
    physical layers the same name (H7: two `ground_copper` and two
    `power_copper`); a name alone would conflate them in every geometric
    predicate, so duplicated names get the layer id appended.
    """
    import pcbnew
    try:
        ids = list(board.GetEnabledLayers().CuStack())
    except Exception:
        n = board.GetCopperLayerCount()
        ids = ([pcbnew.F_Cu] + [getattr(pcbnew, f'In{k}_Cu')
                                for k in range(1, n - 1)]
               + [pcbnew.B_Cu]) if n > 2 else [pcbnew.F_Cu, pcbnew.B_Cu]
    names = [board.GetLayerName(l) for l in ids]
    return ids, _unique_layer_keys(names, ids)


def _unique_layer_keys(names, ids):
    """Disambiguate duplicate copper layer names by the physical layer id."""
    from collections import Counter
    dup = {n for n, c in Counter(names).items() if c > 1}
    return [f'{n}#{l}' if n in dup else n for n, l in zip(names, ids)]


def _uuid(obj):
    for attr in ('m_Uuid', 'GetUuid'):
        v = getattr(obj, attr, None)
        if v is None:
            continue
        s = v.AsString() if hasattr(v, 'AsString') else str(v)
        return str(s)
    return ''


def _pad_shape(pad, layer_id=None):
    """The pad's real copper on one layer.

    KiCad's own polygonisation is used for every shape except a circle: an
    oval or roundrect pad approximated as a rectangle produced a false
    clearance violation on 0344 (the track passed 0.037 mm from the bbox
    corner, but the oval's corner is 0.86 mm inside it).  Holes in a custom
    pad are not subtracted yet (documented; no benchmark board has one).
    """
    import pcbnew
    p = pad.GetPosition()
    sz = pad.GetSize()
    if pad.GetShape() == pcbnew.PAD_SHAPE_CIRCLE:
        return {'kind': 'circle', 'cx': int(p.x), 'cy': int(p.y),
                'r': int(sz.x) // 2}
    if layer_id is not None:
        try:
            poly = pad.GetEffectivePolygon(layer_id)
            if poly.OutlineCount() > 0:
                ch = poly.Outline(0)
                pts = [[int(ch.CPoint(k).x), int(ch.CPoint(k).y)]
                       for k in range(ch.PointCount())]
                if len(pts) >= 3:
                    return {'kind': 'poly', 'pts': pts}
        except Exception:
            pass
    import math
    cx, cy = int(p.x), int(p.y)
    w, h = int(sz.x), int(sz.y)
    ang = math.radians(pad.GetOrientation().AsDegrees())
    ca, sa = math.cos(ang), math.sin(ang)
    hw, hh = w / 2.0, h / 2.0
    pts = []
    for (sx, sy) in ((1, 1), (-1, 1), (-1, -1), (1, -1)):
        lx, ly = sx * hw, sy * hh
        pts.append([int(round(cx + lx * ca - ly * sa)),
                    int(round(cy + lx * sa + ly * ca))])
    return {'kind': 'poly', 'pts': pts}


def extract(board_path, rules=None):
    """Read the board into the canonical state (tracks, vias, pads)."""
    import pcbnew

    b = pcbnew.LoadBoard(str(board_path))
    lids, names = _copper_names(b)
    key_of = dict(zip(lids, names))
    items = []
    for t in b.GetTracks():
        nc = t.GetNetCode()
        nm = b.FindNet(nc)
        net = nm.GetNetname() if nm else ''
        uid = _uuid(t)
        if not net:
            # no-net copper still exists on the plot and must be checked;
            # a unique synthetic net keeps it from being exempted as "same
            # net" against anything else.
            net = f'~{uid}'
        if t.Type() == pcbnew.PCB_VIA_T:
            q = t.GetPosition()
            top = b.GetLayerName(t.GetLayer())
            bot = b.GetLayerName(t.BottomLayer())
            items.append({'id': uid, 'net': net, 'layers': list(names),
                          'drill': int(t.GetDrillValue()),
                          'span': f'{top}-{bot}',
                          'shape': {'kind': 'circle', 'cx': int(q.x),
                                    'cy': int(q.y),
                                    'r': int(t.GetWidth(pcbnew.F_Cu) // 2)}})
        else:
            s, e = t.GetStart(), t.GetEnd()
            items.append({'id': uid, 'net': net,
                          'layers': [key_of.get(t.GetLayer(),
                                                b.GetLayerName(t.GetLayer()))],
                          'shape': {'kind': 'seg', 'x1': int(s.x), 'y1': int(s.y),
                                    'x2': int(e.x), 'y2': int(e.y),
                                    'w': int(t.GetWidth())}})
    # copper graphics (logos, drawing shapes on F.Cu/B.Cu) are copper too:
    # KiCad plots them, the fab checks them, and skipping them left the fab
    # kernel blind to real copper on 12 benchmark boards
    def _graphics(shapes, ref=None):
        for sp in shapes:
            if sp.GetClass() != 'PCB_SHAPE':
                continue
            lid = sp.GetLayer()
            if lid not in lids:
                continue
            lname = key_of.get(lid, names[lids.index(lid)])
            uid = _uuid(sp)
            w = int(sp.GetWidth() or 0)
            t = sp.GetShape()
            try:
                filled = sp.GetFillMode() != 0
            except Exception:
                filled = False
            parts = []
            try:
                if t == pcbnew.SHAPE_T_SEGMENT:
                    a, c = sp.GetStart(), sp.GetEnd()
                    parts.append({'kind': 'seg', 'x1': int(a.x), 'y1': int(a.y),
                                  'x2': int(c.x), 'y2': int(c.y), 'w': w})
                elif t == pcbnew.SHAPE_T_RECT:
                    a, c = sp.GetStart(), sp.GetEnd()
                    x0, x1 = sorted((int(a.x), int(c.x)))
                    y0, y1 = sorted((int(a.y), int(c.y)))
                    pts = [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]
                    if filled:
                        parts.append({'kind': 'poly', 'pts': pts})
                    if w:
                        for k in range(4):
                            p, q = pts[k], pts[(k + 1) % 4]
                            parts.append({'kind': 'seg', 'x1': p[0], 'y1': p[1],
                                          'x2': q[0], 'y2': q[1], 'w': w})
                    else:
                        parts.append({'kind': 'poly', 'pts': pts})
                elif t == pcbnew.SHAPE_T_CIRCLE:
                    cc = sp.GetCenter()
                    r = int(sp.GetRadius())
                    if filled:
                        parts.append({'kind': 'circle', 'cx': int(cc.x),
                                      'cy': int(cc.y), 'r': r})
                    if w:
                        n = 48
                        for k in range(n):
                            a0 = 2 * math.pi * k / n
                            a1 = 2 * math.pi * (k + 1) / n
                            parts.append({'kind': 'seg',
                                          'x1': int(cc.x + r * math.cos(a0)),
                                          'y1': int(cc.y + r * math.sin(a0)),
                                          'x2': int(cc.x + r * math.cos(a1)),
                                          'y2': int(cc.y + r * math.sin(a1)),
                                          'w': w})
                elif t == pcbnew.SHAPE_T_POLY:
                    poly = sp.GetPolyShape()
                    if poly.OutlineCount() > 0:
                        ch = poly.Outline(0)
                        pts = [[int(ch.CPoint(k).x), int(ch.CPoint(k).y)]
                               for k in range(ch.PointCount())]
                        if filled:
                            parts.append({'kind': 'poly', 'pts': pts})
                        if w:
                            for k in range(len(pts)):
                                p, q = pts[k], pts[(k + 1) % len(pts)]
                                parts.append({'kind': 'seg', 'x1': p[0], 'y1': p[1],
                                              'x2': q[0], 'y2': q[1], 'w': w})
                elif t == pcbnew.SHAPE_T_ARC:
                    a, c = sp.GetStart(), sp.GetEnd()
                    parts.append({'kind': 'seg', 'x1': int(a.x), 'y1': int(a.y),
                                  'x2': int(c.x), 'y2': int(c.y), 'w': w})
            except Exception:
                continue
            net = f'~{uid}'
            for k, sh in enumerate(parts):
                it = {'id': f'{uid}:{k}' if len(parts) > 1 else uid,
                      'net': net, 'layers': [lname], 'shape': sh,
                      'graphic': True}
                if ref:
                    it['ref'] = ref
                items.append(it)
                if ref:
                    footprints.setdefault(ref, set()).add(it['id'])

    net_pads, footprints = {}, {}
    for fp in b.GetFootprints():
        ref = str(fp.GetReference())
        for pad in fp.Pads():
            nc = pad.GetNetCode()
            nm = b.FindNet(nc) if nc else None
            net = nm.GetNetname() if nm else ''
            uid = _uuid(pad)
            anon = not net
            if anon:
                net = f'~{uid}'
            layers = [key_of[l] for l in lids if pad.IsOnLayer(l)]
            if not layers:
                continue
            lid0 = next((l for l in lids if pad.IsOnLayer(l)), None)
            ds = pad.GetDrillSize()
            drill = int(max(ds.x, ds.y)) if (ds.x or ds.y) else -1
            npth = pad.GetAttribute() == pcbnew.PAD_ATTRIB_NPTH
            item = {'id': uid, 'net': net, 'layers': layers,
                    'ref': ref, 'drill': drill,
                    'shape': _pad_shape(pad, lid0)}
            if npth:
                # a non-plated hole is not copper: keep it for the hole
                # predicates, but it has no layer to clear against
                item['npth'] = True
                item['layers'] = []
                pos = pad.GetPosition()
                item['shape'] = {'kind': 'circle', 'cx': int(pos.x),
                                 'cy': int(pos.y), 'r': max(drill, 1) // 2}
            if ds.x and ds.y and ds.x != ds.y:
                # a slot: both axes matter for the annular ring, and the
                # drill's local axes must be rotated into board axes by the
                # pad orientation (J10 on H7 is a 90-degree rotated slot)
                ang = math.radians(pad.GetOrientation().AsDegrees())
                c, sn = abs(math.cos(ang)), abs(math.sin(ang))
                ex = int(round(abs(ds.x) * c + abs(ds.y) * sn))
                ey = int(round(abs(ds.x) * sn + abs(ds.y) * c))
                item['drill'] = ex
                item['drill_y'] = ey
            items.append(item)
            if not anon:
                net_pads.setdefault(net, set()).add(uid)
            footprints.setdefault(ref, set()).add(uid)
    _graphics(b.GetDrawings())
    for fp in b.GetFootprints():
        _graphics(fp.GraphicalItems(), str(fp.GetReference()))
    outline = []
    try:
        poly = pcbnew.SHAPE_POLY_SET()
        if b.GetBoardPolygonOutlines(poly, True) and poly.OutlineCount() > 0:
            ch = poly.Outline(0)
            outline = [[int(ch.CPoint(k).x), int(ch.CPoint(k).y)]
                       for k in range(ch.PointCount())]
    except Exception:
        outline = []
    from collections import Counter as _C
    dup = [k for k, n in _C(i['id'] for i in items).items() if n > 1]
    if dup:
        # two items with one id make itemById ambiguous: no proof about them
        # can be trusted, so refuse the state instead of picking one
        raise ValueError(f'duplicate item ids ({len(dup)}): '
                         + ','.join(sorted(dup)[:6]))
    st = {'schema': SCHEMA, 'board': str(board_path), 'layers': list(names),
          'items': items, 'rules': dict(rules or {}),
          'netPads': {n: sorted(v) for n, v in sorted(net_pads.items())},
          'footprints': {r: sorted(v) for r, v in sorted(footprints.items())},
          'zones': int(b.GetAreaCount()), 'outline': outline}
    return freeze(st)


def from_dict(d):
    return freeze(d)


def item_by_id(st):
    return {i['id']: i for i in st['items']}
