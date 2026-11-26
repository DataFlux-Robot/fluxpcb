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

SCHEMA = 'fluxpcb-state-1'


def shape_str(sh):
    k = sh['kind']
    if k == 'seg':
        return f"seg:{sh['x1']},{sh['y1']},{sh['x2']},{sh['y2']},{sh['w']}"
    if k == 'circle':
        return f"cir:{sh['cx']},{sh['cy']},{sh['r']}"
    if k == 'poly':
        return "poly:" + ";".join(f"{x},{y}" for x, y in sh['pts'])
    raise ValueError(f'shape {k}')


def canonical(st):
    items = sorted(st['items'], key=lambda i: i['id'])
    rules = sorted(st['rules'].items())
    nets = sorted(st['netPads'].items())
    lines = ['v1', st['schema'], st['board'], ','.join(st['layers'])]
    lines += [f"i|{i['id']}|{i['net']}|{','.join(i['layers'])}|{shape_str(i['shape'])}"
              for i in items]
    lines += [f"r|{k}={v}" for k, v in rules]
    lines += [f"n|{n}:{','.join(ids)}" for n, ids in nets]
    return '\n'.join(lines)


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
    import pcbnew
    n = board.GetCopperLayerCount()
    ids = ([pcbnew.F_Cu] + [getattr(pcbnew, f'In{k}_Cu') for k in range(1, n - 1)]
           + [pcbnew.B_Cu]) if n > 2 else [pcbnew.F_Cu, pcbnew.B_Cu]
    return ids, [board.GetLayerName(l) for l in ids]


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
    items = []
    for t in b.GetTracks():
        nc = t.GetNetCode()
        nm = b.FindNet(nc)
        net = nm.GetNetname() if nm else ''
        uid = _uuid(t)
        if t.Type() == pcbnew.PCB_VIA_T:
            q = t.GetPosition()
            items.append({'id': uid, 'net': net, 'layers': list(names),
                          'shape': {'kind': 'circle', 'cx': int(q.x),
                                    'cy': int(q.y),
                                    'r': int(t.GetWidth(pcbnew.F_Cu) // 2)}})
        else:
            s, e = t.GetStart(), t.GetEnd()
            items.append({'id': uid, 'net': net,
                          'layers': [b.GetLayerName(t.GetLayer())],
                          'shape': {'kind': 'seg', 'x1': int(s.x), 'y1': int(s.y),
                                    'x2': int(e.x), 'y2': int(e.y),
                                    'w': int(t.GetWidth())}})
    net_pads = {}
    for fp in b.GetFootprints():
        for pad in fp.Pads():
            nc = pad.GetNetCode()
            if not nc:
                continue
            nm = b.FindNet(nc)
            net = nm.GetNetname() if nm else ''
            if not net:
                continue
            uid = _uuid(pad)
            layers = [b.GetLayerName(l) for l in lids if pad.IsOnLayer(l)]
            if not layers:
                continue
            lid0 = next((l for l in lids if pad.IsOnLayer(l)), None)
            items.append({'id': uid, 'net': net, 'layers': layers,
                          'shape': _pad_shape(pad, lid0)})
            net_pads.setdefault(net, set()).add(uid)
    st = {'schema': SCHEMA, 'board': str(board_path), 'layers': list(names),
          'items': items, 'rules': dict(rules or {}),
          'netPads': {n: sorted(v) for n, v in sorted(net_pads.items())}}
    return freeze(st)


def from_dict(d):
    return freeze(d)


def item_by_id(st):
    return {i['id']: i for i in st['items']}
