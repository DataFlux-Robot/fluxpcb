"""Exact integer geometry, mirroring lean/Fluxpcb/Geom.lean.

The kernel verifies every connectivity edge with its own copy of these
predicates; this Python copy is only used to *propose* certificates.  If the
two disagree the kernel returns `invalid` for the edge — that is the check.
All coordinates are integer nanometres, all comparisons cross-multiplied.
"""
from __future__ import annotations

Pt = tuple


def cross(o, a, b):
    return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])


def between(a, b, p):
    return (min(a[0], b[0]) <= p[0] <= max(a[0], b[0])
            and min(a[1], b[1]) <= p[1] <= max(a[1], b[1]))


def on_seg(a, b, p):
    return cross(a, b, p) == 0 and between(a, b, p)


def segs_intersect(a, b, c, d):
    if on_seg(a, b, c) or on_seg(a, b, d) or on_seg(c, d, a) or on_seg(c, d, b):
        return True
    o1, o2 = cross(a, b, c), cross(a, b, d)
    o3, o4 = cross(c, d, a), cross(c, d, b)
    return (o1 > 0) != (o2 > 0) and (o3 > 0) != (o4 > 0)


def pt_seg_d2(p, a, b):
    abx, aby = b[0] - a[0], b[1] - a[1]
    den = abx * abx + aby * aby
    if den == 0:
        return ((p[0] - a[0]) ** 2 + (p[1] - a[1]) ** 2, 1)
    apx, apy = p[0] - a[0], p[1] - a[1]
    dot = apx * abx + apy * aby
    if dot <= 0:
        return (apx * apx + apy * apy, 1)
    if dot >= den:
        return ((p[0] - b[0]) ** 2 + (p[1] - b[1]) ** 2, 1)
    cr = abx * apy - aby * apx
    return (cr * cr, den)


def pt_seg_le(p, a, b, r2):
    num, den = pt_seg_d2(p, a, b)
    return num <= r2 * den


def pt_in_poly(p, pts):
    n = len(pts)
    if n < 3:
        return False
    inside = False
    for i in range(n):
        a, b = pts[i], pts[(i + 1) % n]
        if (a[1] > p[1]) != (b[1] > p[1]):
            lhs = (p[0] - a[0]) * (b[1] - a[1])
            rhs = (b[0] - a[0]) * (p[1] - a[1])
            if (lhs < rhs) if b[1] > a[1] else (lhs > rhs):
                inside = not inside
    return inside


def poly_edges(pts):
    n = len(pts)
    if n < 2:
        return []
    return [(pts[i], pts[(i + 1) % n]) for i in range(n)]


def any_edge(pts, f):
    return any(f(a, b) for a, b in poly_edges(pts))


def circle_circle(c1, c2):
    x1, y1, r1 = c1
    x2, y2, r2 = c2
    dx, dy = x1 - x2, y1 - y2
    return dx * dx + dy * dy <= (r1 + r2) ** 2


def circle_seg(c, a, b, w):
    cx, cy, r = c
    return pt_seg_le((cx, cy), a, b, (2 * r + w) ** 2)


def circle_poly(c, pts):
    cx, cy, r = c
    if pt_in_poly((cx, cy), pts):
        return True
    return any_edge(pts, lambda a, b: pt_seg_le((cx, cy), a, b, 4 * r * r))


def seg_seg(a, b, w1, c, d, w2):
    if segs_intersect(a, b, c, d):
        return True
    s = w1 + w2
    return (pt_seg_le(a, c, d, s * s) or pt_seg_le(b, c, d, s * s)
            or pt_seg_le(c, a, b, s * s) or pt_seg_le(d, a, b, s * s))


def seg_poly(a, b, w, pts):
    if pt_in_poly(a, pts) or pt_in_poly(b, pts):
        return True
    if any_edge(pts, lambda c, d: segs_intersect(a, b, c, d)):
        return True
    return any_edge(pts, lambda c, _d: pt_seg_le(c, a, b, w * w))


def poly_poly(p, q):
    if any_edge(p, lambda a, b: any_edge(q, lambda c, d: segs_intersect(a, b, c, d))):
        return True
    if any(pt_in_poly(v, q) for v in p):
        return True
    return any(pt_in_poly(v, p) for v in q)


def _pts(sh):
    return [tuple(p) for p in sh['pts']]


def shape_touch(s1, s2):
    k1, k2 = s1['kind'], s2['kind']
    if k1 == 'seg' and k2 == 'seg':
        return seg_seg((s1['x1'], s1['y1']), (s1['x2'], s1['y2']), s1['w'],
                       (s2['x1'], s2['y1']), (s2['x2'], s2['y2']), s2['w'])
    if k1 == 'seg' and k2 == 'circle':
        return circle_seg((s2['cx'], s2['cy'], s2['r']),
                          (s1['x1'], s1['y1']), (s1['x2'], s1['y2']), s1['w'])
    if k1 == 'circle' and k2 == 'seg':
        return circle_seg((s1['cx'], s1['cy'], s1['r']),
                          (s2['x1'], s2['y1']), (s2['x2'], s2['y2']), s2['w'])
    if k1 == 'circle' and k2 == 'circle':
        return circle_circle((s1['cx'], s1['cy'], s1['r']),
                             (s2['cx'], s2['cy'], s2['r']))
    if k1 == 'circle' and k2 == 'poly':
        return circle_poly((s1['cx'], s1['cy'], s1['r']), _pts(s2))
    if k1 == 'poly' and k2 == 'circle':
        return circle_poly((s2['cx'], s2['cy'], s2['r']), _pts(s1))
    if k1 == 'seg' and k2 == 'poly':
        return seg_poly((s1['x1'], s1['y1']), (s1['x2'], s1['y2']), s1['w'], _pts(s2))
    if k1 == 'poly' and k2 == 'seg':
        return seg_poly((s2['x1'], s2['y1']), (s2['x2'], s2['y2']), s2['w'], _pts(s1))
    if k1 == 'poly' and k2 == 'poly':
        return poly_poly(_pts(s1), _pts(s2))
    return False


def item_touch(a, b):
    if a['net'] != b['net']:
        return False
    if not any(l in b['layers'] for l in a['layers']):
        return False
    return shape_touch(a['shape'], b['shape'])


def bbox(sh):
    k = sh['kind']
    if k == 'seg':
        xs = (sh['x1'], sh['x2'])
        ys = (sh['y1'], sh['y2'])
        hw = sh['w'] // 2
        return (min(xs) - hw, min(ys) - hw, max(xs) + hw, max(ys) + hw)
    if k == 'circle':
        return (sh['cx'] - sh['r'], sh['cy'] - sh['r'],
                sh['cx'] + sh['r'], sh['cy'] + sh['r'])
    xs = [p[0] for p in sh['pts']]
    ys = [p[1] for p in sh['pts']]
    return (min(xs), min(ys), max(xs), max(ys))


def bbox_gap(a, b):
    gx = max(a[0] - b[2], b[0] - a[2], 0)
    gy = max(a[1] - b[3], b[1] - a[3], 0)
    return (gx * gx + gy * gy) ** 0.5
