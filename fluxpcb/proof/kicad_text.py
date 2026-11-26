"""An independent text parser for .kicad_pcb: the second leg of the trust triangle.

The extractor (pcbnew) is the bridge every proof stands on.  This module reads
the same file as S-expressions and rebuilds the copper items (segments, arcs,
vias, pads) with their own arithmetic, so a disagreement between the two
parsers is visible instead of silent.  The Lean kernel is the third leg: it
re-derives the canonical digest from either state.

The comparison is deliberately narrow and exact: item presence by uuid, net,
layer, endpoints/position (nm), width/diameter, drill.  It does not try to
reproduce KiCad's polygonisation of pads; a pad is compared by its centre
(bbox), net, drill and copper layers.
"""
from __future__ import annotations

import math
from pathlib import Path

TOL = 1000          # nm


# ---------------------------------------------------------------- S-expressions
def tokenize(text):
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch in ' \t\r\n':
            i += 1
        elif ch in '()':
            yield ch
            i += 1
        elif ch == '"':
            j, buf = i + 1, []
            while j < n:
                if text[j] == '\\' and j + 1 < n:
                    buf.append(text[j + 1])
                    j += 2
                elif text[j] == '"':
                    break
                else:
                    buf.append(text[j])
                    j += 1
            yield ''.join(buf)
            i = j + 1
        else:
            j = i
            while j < n and text[j] not in ' \t\r\n()"':
                j += 1
            yield text[i:j]
            i = j


def parse(text):
    stack = [[]]
    for tok in tokenize(text):
        if tok == '(':
            stack.append([])
        elif tok == ')':
            node = stack.pop()
            stack[-1].append(node)
        else:
            stack[-1].append(tok)
    return stack[0][0] if stack[0] else []


def _head(n):
    return n[0] if isinstance(n, list) and n and isinstance(n[0], str) else None


def _child(n, name):
    for c in n:
        if isinstance(c, list) and _head(c) == name:
            return c
    return None


def _num(n, name, i=1):
    c = _child(n, name)
    if c is None or len(c) <= i:
        return None
    try:
        return float(c[i])
    except (TypeError, ValueError):
        return None


def _nums(n):
    out = []
    for x in n[1:]:
        try:
            out.append(float(x))
        except (TypeError, ValueError):
            pass
    return out


def _nm(x):
    return int(round(float(x) * 1e6)) if x is not None else None


def _rot(dx, dy, deg):
    c, s = math.cos(math.radians(deg or 0)), math.sin(math.radians(deg or 0))
    return dx * c + dy * s, -dx * s + dy * c


def _copper_layers(node, names):
    lay = _child(node, 'layers')
    if lay is None:
        return []
    return [x for x in lay[1:] if isinstance(x, str) and x in names]


def parse_board(path, copper_names):
    """Return {uuid: item}, arcs, duplicate uuids for segments/arcs/vias/pads."""
    root = parse(Path(path).read_text(encoding='utf-8', errors='replace'))
    out = {}
    dups = []
    arcs = []
    for node in root[1:]:
        h = _head(node)
        if h in ('segment', 'arc'):
            uid = _child(node, 'uuid')
            if uid is None:
                continue
            u = uid[1]
            if u in out:
                dups.append(u)
            st, en = _child(node, 'start'), _child(node, 'end')
            if st is None or en is None:
                continue
            w = _num(node, 'width')
            layer = _child(node, 'layer')
            net = _child(node, 'net')
            out[u] = {'kind': 'seg' if h == 'segment' else 'arc',
                      'uuid': u, 'net': net[1] if net else '',
                      'layer': layer[1] if layer else '',
                      'x1': _nm(st[1]), 'y1': _nm(st[2]),
                      'x2': _nm(en[1]), 'y2': _nm(en[2]),
                      'w': _nm(w)}
            if h == 'arc':
                arcs.append(u)
        elif h == 'via':
            uid, at = _child(node, 'uuid'), _child(node, 'at')
            if uid is None or at is None:
                continue
            lay = _copper_layers(node, copper_names)
            if uid[1] in out:
                dups.append(uid[1])
            out[uid[1]] = {'kind': 'via', 'uuid': uid[1],
                           'net': (_child(node, 'net') or ['', ''])[1],
                           'x': _nm(at[1]), 'y': _nm(at[2]),
                           'size': _nm(_num(node, 'size')),
                           'drill': _nm(_num(node, 'drill')),
                           'layers': lay}
        elif h == 'footprint':
            _footprint(node, out, copper_names, dups)
    return out, arcs, dups


def _footprint(fp, out, copper_names, dups):
    at = _child(fp, 'at')
    if at is None:
        return
    fx, fy = float(at[1]), float(at[2])
    frot = float(at[3]) if len(at) > 3 else 0.0
    ref, value = '', ''
    for c in fp:
        if isinstance(c, list) and _head(c) == 'property' and len(c) > 2:
            if c[1] == 'Reference':
                ref = c[2]
            elif c[1] == 'Value':
                value = c[2]
    for pad in fp:
        if not (isinstance(pad, list) and _head(pad) == 'pad'):
            continue
        pat = _child(pad, 'at')
        uid = _child(pad, 'uuid')
        if pat is None or uid is None:
            continue
        dx, dy = float(pat[1]), float(pat[2])
        rx, ry = _rot(dx, dy, frot)
        px, py = _nm(fx + rx), _nm(fy + ry)
        size = _child(pad, 'size')
        drill = _child(pad, 'drill')
        dnums = _nums(drill) if drill is not None else []
        drill_x = _nm(dnums[-2] if len(dnums) > 1 else
                      (dnums[0] if dnums else None))
        drill_y = _nm(dnums[-1]) if len(dnums) > 1 else None
        # a drill offset moves the copper in KiCad's plot: the Gerber flash
        # sits at anchor + R(pad orientation)*(offset), so the text parser
        # must report the copper centre, not the anchor
        off = _child(drill, 'offset') if drill is not None else None
        if off is not None and len(off) > 2:
            prot = float(pat[3]) if len(pat) > 3 else 0.0
            ox, oy = _rot(float(off[1]), float(off[2]), prot)
            px, py = _nm(float(at[1]) + rx + ox), _nm(float(at[2]) + ry + oy)
        net = _child(pad, 'net')
        if uid[1] in out:
            dups.append(uid[1])
        out[uid[1]] = {'kind': 'pad', 'uuid': uid[1], 'ref': ref,
                       'value': value, 'number': pad[1] if len(pad) > 1 else '',
                       'type': pad[2] if len(pad) > 2 else '',
                       'shape': pad[3] if len(pad) > 3 else '',
                       'x': px, 'y': py,
                       'size': (_nm(size[1]), _nm(size[2])) if size else None,
                       'drill': drill_x, 'drill_y': drill_y,
                       'layers': _copper_layers(pad, copper_names),
                       'npth': (len(pad) > 2 and pad[2] == 'np_thru_hole'),
                       'netname': net[1] if net else ''}


# ---------------------------------------------------------------- comparison
def _state_center(sh):
    if sh['kind'] == 'circle':
        return sh['cx'], sh['cy']
    xs = [p[0] for p in sh['pts']]
    ys = [p[1] for p in sh['pts']]
    return (min(xs) + max(xs)) // 2, (min(ys) + max(ys)) // 2


def compare(board_path, st=None, copper_names=None):
    """Compare the text parse against the pcbnew state (item by item)."""
    if st is None:
        from . import state as S
        st = S.extract(board_path, S.board_rules(board_path)[0])
    names = copper_names or st['layers']
    text, arcs, dups = parse_board(board_path, names)
    by_id = {i['id']: i for i in st['items']}
    mism, missing, extra = [], [], []
    for u, t in text.items():
        if u in dups:
            continue                      # the duplicate report is the verdict
        if t['kind'] == 'pad' and t['npth']:
            # an NPTH pad has no copper in the plotted/geometry state but the
            # state keeps it as a hole item; compare only the hole
            s = by_id.get(u)
            if s is None:
                missing.append(u)
                continue
            if s.get('drill') != t['drill']:
                mism.append({'uuid': u, 'field': 'npth_drill',
                             'text': t['drill'], 'state': s.get('drill')})
            continue
        s = by_id.get(u)
        if s is None:
            missing.append(u)
            continue
        tnet = t.get('netname') or t.get('net', '')
        if (s.get('net') or '').startswith('~'):
            if tnet:
                pass                     # anonymous in state, named in text
        elif s.get('net') != tnet and t['kind'] != 'pad':
            mism.append({'uuid': u, 'field': 'net', 'text': tnet,
                         'state': s.get('net')})
        if t['kind'] == 'pad':
            cx, cy = _state_center(s['shape'])
            if abs(cx - t['x']) > TOL or abs(cy - t['y']) > TOL:
                mism.append({'uuid': u, 'field': 'position',
                             'text': (t['x'], t['y']), 'state': (cx, cy)})
            if not (s.get('net') or '').startswith('~') and \
                    s.get('net') != t['netname']:
                mism.append({'uuid': u, 'field': 'net', 'text': t['netname'],
                             'state': s.get('net')})
            if t['drill'] is not None and t['drill'] >= 0:
                if s.get('drill') != t['drill'] or \
                        (t['drill_y'] is not None and
                         s.get('drill_y') != t['drill_y']):
                    mism.append({'uuid': u, 'field': 'drill',
                                 'text': (t['drill'], t['drill_y']),
                                 'state': (s.get('drill'), s.get('drill_y'))})
            if bool(s.get('npth')) != bool(t['npth']):
                mism.append({'uuid': u, 'field': 'npth', 'text': t['npth'],
                             'state': s.get('npth')})
        elif t['kind'] == 'via':
            if abs(s['shape']['cx'] - t['x']) > TOL or \
                    abs(s['shape']['cy'] - t['y']) > TOL:
                mism.append({'uuid': u, 'field': 'position',
                             'text': (t['x'], t['y']),
                             'state': (s['shape']['cx'], s['shape']['cy'])})
            if s['shape']['kind'] == 'circle' and \
                    abs(2 * s['shape']['r'] - t['size']) > TOL:
                mism.append({'uuid': u, 'field': 'size', 'text': t['size'],
                             'state': 2 * s['shape']['r']})
            if s.get('drill') != t['drill']:
                mism.append({'uuid': u, 'field': 'drill', 'text': t['drill'],
                             'state': s.get('drill')})
        else:                           # segment / arc
            sh = s['shape']
            if sh['kind'] != 'seg':
                mism.append({'uuid': u, 'field': 'shape', 'text': 'seg',
                             'state': sh['kind']})
                continue
            if abs(sh['x1'] - t['x1']) > TOL or abs(sh['y1'] - t['y1']) > TOL \
                    or abs(sh['x2'] - t['x2']) > TOL or \
                    abs(sh['y2'] - t['y2']) > TOL:
                mism.append({'uuid': u, 'field': 'endpoints',
                             'text': (t['x1'], t['y1'], t['x2'], t['y2']),
                             'state': (sh['x1'], sh['y1'], sh['x2'],
                                       sh['y2'])})
            if t['w'] is not None and sh.get('w') != t['w']:
                mism.append({'uuid': u, 'field': 'width', 'text': t['w'],
                             'state': sh.get('w')})
    for u in by_id:
        if u not in text:
            extra.append(u)
    kinds = {'text': len(text), 'state': len(by_id),
             'pads': sum(1 for t in text.values() if t['kind'] == 'pad'),
             'segments': sum(1 for t in text.values()
                             if t['kind'] in ('seg', 'arc')),
             'arcs': len(arcs), 'vias': sum(1 for t in text.values()
                                            if t['kind'] == 'via')}
    if dups:
        mism.append({'uuid': dups[0], 'field': 'duplicate_uuid',
                     'text': len(dups), 'state': None})
    return {'ok': not mism and not missing and not extra,
            'mismatches': mism[:20], 'n_mismatch': len(mism),
            'missing': missing[:20], 'n_missing': len(missing),
            'extra': extra[:20], 'n_extra': len(extra),
            'arcs_approximated': len(arcs), 'counts': kinds}


def check(board_path):
    try:
        c = compare(board_path)
    except ValueError as e:
        return {'ok': False, 'reasons': [str(e)], 'mismatches': [],
                'n_mismatch': 1, 'missing': [], 'n_missing': 0,
                'extra': [], 'n_extra': 0, 'arcs_approximated': 0,
                'counts': {},
                'theorem': {'id': 'TRUST:text', 'kernel': 'kicad_text',
                            'version': '1', 'ok': False, 'digest': ''}}
    payload = {'mismatch': c['n_mismatch'], 'missing': c['n_missing'],
               'extra': c['n_extra'], 'arcs': c['arcs_approximated'],
               'counts': c['counts']}
    import hashlib
    import json
    dig = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()
                         ).hexdigest()[:16]
    c['theorem'] = {'id': 'TRUST:text', 'kernel': 'kicad_text', 'version': '1',
                    'claims': ['pcbnew state == text parse (item by item)'],
                    'ok': c['ok'], 'digest': dig}
    return c
