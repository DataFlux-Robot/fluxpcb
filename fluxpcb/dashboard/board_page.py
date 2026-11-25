"""One board, one page: what the repair saw, tried and left.

    python -m fluxpcb.dashboard.board_page <cert.json> [<board.kicad_pcb>] [-o page.html]

The page is a product of data: every number on it is read back from a
cell's certificate (run_bench's ``cert.json``), and, when the board file
is given too, from the copper itself.  The certificate is checked before
anything is drawn — one that does not parse, or that is not a
certificate, is reported and nothing is rendered as if it were empty.

With the board, every finding gets a window of the copper around it:
the net the finding is about and the nets it was in conflict with in
colour, everything else grey, the anchors a re-plan had to keep as
crosses, the path that was drawn as a line.  That is the picture a
person needs to see why a connection is sealed — 0051's CSN between
+3V3 and CE, 0571's SD_A under the battery pad.
"""
from __future__ import annotations

import html
import json
import math
import os
import sys

FINDING_OPERATORS = ('conflict_reroute', 'connect_unconnected', 'maze_escape',
                     'nudge_clearance', 'widen_narrow_tracks')


def load_cert(path):
    """(cert, problems).  A certificate that is not one comes back with
    its problems and None."""
    try:
        with open(path, encoding='utf-8') as fh:
            cert = json.load(fh)
    except (OSError, ValueError) as ex:
        return None, [f'not JSON: {type(ex).__name__}: {ex}']
    problems = []
    if not isinstance(cert, dict):
        return None, ['not an object']
    for key in ('board', 'route', 'claims_after'):
        if key not in cert:
            problems.append(f'missing {key!r}')
    if problems:
        return None, problems
    return cert, []


def _fmt(value, digits=3):
    if value is None:
        return '—'
    if isinstance(value, float):
        return f'{value:.{digits}f}'.rstrip('0').rstrip('.')
    return str(value)


def _why_text(why):
    """One line for a "why not": the numbers, in the certificate's words."""
    if not why:
        return ''
    parts = []
    for key in ('error', 'verdict', 'why'):
        if why.get(key):
            parts.append(str(why[key]))
    profile = why.get('profile') or why.get('best_reject') or {}
    if isinstance(profile, dict):
        blk = profile.get('blocker') or {}
        if blk:
            parts.append('blocked by %s, short %s mm, move %s mm' % (
                blk.get('kind', '?'), _fmt(blk.get('short_by_mm')),
                _fmt(blk.get('move_by_mm'))))
    if why.get('margin_mm') is not None:
        parts.append(f"margin {_fmt(why['margin_mm'])} mm")
    if why.get('conflict_set') is not None:
        parts.append(f"conflict set {_fmt(why['conflict_set'])} item(s)")
    if why.get('lifts') is not None:
        parts.append(f"{why['lifts']} lift(s)")
    return '; '.join(parts)


def _tried_rows(entry):
    """What a conflict_reroute attempt searched: one row per lift / set."""
    why = entry.get('why_not') or {}
    attempt = entry.get('attempt') or {}
    rows = why.get('tried') or (attempt.get('tried') if isinstance(attempt, dict) else None) or []
    out = []
    for t in rows if isinstance(rows, list) else []:
        if not isinstance(t, dict):
            continue
        cells = []
        if 'lift' in t:
            cells.append(f"lift {t['lift']}")
        if 'set' in t:
            cells.append(f"set {t['set']}")
        if 'start_layer' in t:
            cells.append(t['start_layer'])
        cells.append(f"{t.get('candidates', 0)} free paths")
        if t.get('priced'):
            cells.append(f"cheapest crossing: {t.get('priced')}, {t.get('crossed', 0)} item(s)"
                         + (f", {t['path_mm']} mm" if t.get('path_mm') is not None else ''))
        out.append(' · '.join(cells))
    return out


def findings(cert):
    """The failure certificates of the cell, grouped by operator, each
    with the text the page shows.  `cert['failures']` is what the bench
    collected from the stage logs; when it is absent the stage logs are
    read directly."""
    raw = cert.get('failures')
    if raw is None:
        raw = []
        for st in cert.get('repair') or []:
            for e in st.get('log') or []:
                if isinstance(e, dict):
                    raw.extend((e.get('action') or {}).get('ledger') or [])
    groups = {}
    for entry in raw:
        if not isinstance(entry, dict) or not entry.get('outcome'):
            continue
        op = entry.get('op') or entry.get('operator') or '?'
        item = entry.get('item') or {}
        attempt = entry.get('attempt') or {}
        plan = attempt.get('plan') if isinstance(attempt, dict) else None
        row = {
            'outcome': entry.get('outcome'),
            'net': item.get('net', '?'),
            'type': item.get('type', '?'),
            'pos': item.get('pos'),
            'why': _why_text(entry.get('why_not')),
            'anchors': attempt.get('anchors') if isinstance(attempt, dict) else None,
            'plan': (f"{plan.get('mode')}, {_fmt(plan.get('length_mm'))} mm, "
                     f"{plan.get('vias', 0)} via(s)" if isinstance(plan, dict) else None),
            'plan_pts': (plan or {}).get('pts') if isinstance(plan, dict) else None,
            'set': [s.get('net') for s in (attempt.get('conflict_set') or [])]
                   if isinstance(attempt.get('conflict_set'), list) and attempt['conflict_set']
                   and isinstance(attempt['conflict_set'][0], dict) else
                   list(attempt.get('conflict_set') or []) if isinstance(attempt, dict) else [],
            'tried': _tried_rows(entry),
            'stage': entry.get('stage'),
            'decision': entry.get('decision'),
        }
        groups.setdefault(op, []).append(row)
    return groups


# ------------------------------------------------- the parts a reader asks for
def open_connections(cert) -> list:
    """The connections the board is missing, with their nets and items.

    Read from the certificate only: an `unconnected` finding, or a claim
    that says so.  An empty list with `claims_after.n_unconnected > 0`
    means the certificate does not say WHICH connections — the page then
    says that instead of showing an empty table.
    """
    out = []
    for entry in (cert.get('failures') or []):
        if not isinstance(entry, dict):
            continue
        item = entry.get('item') or {}
        if str(item.get('type') or '') != 'unconnected':
            continue
        out.append({'net': item.get('net', '?'),
                    'outcome': entry.get('outcome'),
                    'items': [str(i.get('description') or '')[:80]
                              for i in (item.get('items') or [])
                              if isinstance(i, dict)],
                    'pos': item.get('pos')})
    return out


def blocking_pads(cert) -> list:
    """Pads that stood in the way, named by the attempt that hit them."""
    out = []
    for op, rows in findings(cert).items():
        for row in rows:
            why = row.get('why') or ''
            if 'pad' not in why.lower():
                continue
            out.append({'operator': op, 'net': row.get('net'),
                        'why': why, 'outcome': row.get('outcome')})
    return out


def via_sites(cert) -> list:
    """The via sites an attempt tried, when it recorded them."""
    out = []
    for entry in (cert.get('failures') or []):
        if not isinstance(entry, dict):
            continue
        sites = ((entry.get('why_not') or {}).get('via_sites')
                 or (entry.get('attempt') or {}).get('via_sites'))
        if sites:
            out.append({'operator': entry.get('op') or entry.get('operator'),
                        'sites': sites})
    return out


def needed_information(cert) -> list:
    """What the cell could not decide, deduplicated, with who needed it."""
    out = []
    def add(text, src):
        if text and all(text != t for t, _s in out):
            out.append((text, src))
    for entry in (cert.get('failures') or []):
        if not isinstance(entry, dict):
            continue
        for m in entry.get('needed_information') or []:
            add(str(m), entry.get('op') or entry.get('operator') or '?')
        for m in (entry.get('why_not') or {}).get('needed_information') or []:
            add(str(m), entry.get('op') or entry.get('operator') or '?')
    for st in cert.get('repair') or []:
        for m in st.get('needed_information') or []:
            add(str(m), st.get('stage') or '?')
        for e in st.get('log') or []:
            if isinstance(e, dict):
                for m in (e.get('action') or {}).get('needed_information') or []:
                    add(str(m), st.get('stage') or '?')
    return [{'text': t, 'from': s} for t, s in out]


# ------------------------------------------------------------- the copper
def copper_svg(board_path, centre, half=5.0, colour=(), marks=(), lines=(),
               size=360):
    """One window of the board as SVG.  `colour` are net names drawn in
    colour, everything else grey; `marks` are (x, y) crosses; `lines`
    are polylines [[x, y], ...] drawn on top.  Needs pcbnew: the page is
    generated where KiCad's Python is."""
    import pcbnew

    b = pcbnew.LoadBoard(board_path)
    cx, cy = centre
    x0, y0 = cx - half, cy - half
    span = 2 * half
    k = size / span

    def X(x):
        return (x - x0) * k

    def Y(y):
        return (y - y0) * k

    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {size} {size}" '
           f'width="{size}" height="{size}" class="copper">',
           f'<rect width="{size}" height="{size}" fill="#fbfbf8"/>']
    for g in range(int(math.floor(x0)), int(math.ceil(x0 + span)) + 1):
        out.append(f'<line x1="{X(g):.1f}" y1="0" x2="{X(g):.1f}" y2="{size}" stroke="#e6e6e0"/>')
        out.append(f'<line x1="0" y1="{Y(g):.1f}" x2="{size}" y2="{Y(g):.1f}" stroke="#e6e6e0"/>')
        out.append(f'<text x="{X(g) + 2:.1f}" y="10" font-size="9" fill="#888">{g}</text>')
        out.append(f'<text x="2" y="{Y(g) + 10:.1f}" font-size="9" fill="#888">{g}</text>')
    palette = ['#1f77b4', '#d62728', '#2ca02c', '#9467bd', '#ff7f0e', '#17becf']
    tones = {n: palette[i % len(palette)] for i, n in enumerate(colour)}
    grey = '#b8b8b8'

    def tone(net):
        return tones.get(net, grey)

    def inside(p):
        return x0 - 1 <= p.x / 1e6 <= x0 + span + 1 and y0 - 1 <= p.y / 1e6 <= y0 + span + 1

    for fp in b.GetFootprints():
        for pad in fp.Pads():
            p = pad.GetPosition()
            if not inside(p):
                continue
            sz = pad.GetSize()
            w, h = sz.x / 1e6 * k, sz.y / 1e6 * k
            fill = tone(pad.GetNetname())
            if pad.GetNetname() not in tones:
                fill = '#cfcfcf'
            out.append(f'<rect x="{X(p.x / 1e6) - w / 2:.1f}" y="{Y(p.y / 1e6) - h / 2:.1f}" '
                       f'width="{w:.1f}" height="{h:.1f}" fill="{fill}" fill-opacity="0.55" '
                       f'stroke="#777"><title>{html.escape(fp.GetReference())}.{html.escape(pad.GetNumber())} '
                       f'{html.escape(pad.GetNetname())}</title></rect>')
    for t in b.GetTracks():
        net = t.GetNetname()
        if t.GetClass() == 'PCB_VIA':
            p = t.GetPosition()
            if not inside(p):
                continue
            r = t.GetWidth(pcbnew.F_Cu) / 2e6 * k
            out.append(f'<circle cx="{X(p.x / 1e6):.1f}" cy="{Y(p.y / 1e6):.1f}" r="{r:.1f}" '
                       f'fill="{tone(net)}" fill-opacity="0.6" stroke="#666"/>')
        else:
            s, e = t.GetStart(), t.GetEnd()
            if not (inside(s) or inside(e)):
                continue
            w = max(1.0, t.GetWidth() / 1e6 * k)
            op = 0.85 if net in tones else 0.45
            out.append(f'<line x1="{X(s.x / 1e6):.1f}" y1="{Y(s.y / 1e6):.1f}" '
                       f'x2="{X(e.x / 1e6):.1f}" y2="{Y(e.y / 1e6):.1f}" stroke="{tone(net)}" '
                       f'stroke-width="{w:.1f}" stroke-opacity="{op}" stroke-linecap="round">'
                       f'<title>{html.escape(net)}</title></line>')
    for line in lines:
        pts = ' '.join(f'{X(x):.1f},{Y(y):.1f}' for x, y in line)
        out.append(f'<polyline points="{pts}" fill="none" stroke="#000" '
                   f'stroke-width="2" stroke-dasharray="6,3"/>')
    for (x, y) in marks:
        out.append(f'<path d="M{X(x) - 5:.1f},{Y(y):.1f} h10 M{X(x):.1f},{Y(y) - 5:.1f} v10" '
                   f'stroke="#000" stroke-width="1.6"/>')
    out.append(f'<text x="{size - 8}" y="{size - 6}" font-size="10" text-anchor="end" '
               f'fill="#555">±{_fmt(half, 1)} mm · {html.escape(os.path.basename(board_path))[:28]}</text>')
    out.append('</svg>')
    return '\n'.join(out)


def _window_centre(row):
    """Where to look for a finding: its anchors' middle, else its pos."""
    anchors = row.get('anchors')
    if anchors and len(anchors) == 2:
        return ((anchors[0][0] + anchors[1][0]) / 2, (anchors[0][1] + anchors[1][1]) / 2)
    pts = row.get('plan_pts')
    if pts:
        return (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))
    pos = row.get('pos')
    if pos and len(pos) >= 2:
        return (float(pos[0]), float(pos[1]))
    return None


def render(cert, board_path=None, problems=()):
    e = html.escape
    route = cert.get('route') or {}
    claims = cert.get('claims_after') or {}
    dang = cert.get('dangling') or {}
    out = ['<!doctype html><html lang="en"><head><meta charset="utf-8">',
           '<title>' + e(str(cert.get('board', '?'))) + ' — repair</title>',
           '<style>'
           'body{font:13px/1.45 -apple-system,Segoe UI,Roboto,sans-serif;margin:20px;color:#222}'
           'h1{font-size:18px;margin:0 0 4px}h2{font-size:15px;margin:22px 0 6px;border-bottom:1px solid #ddd;padding-bottom:3px}'
           'table{border-collapse:collapse;margin:4px 0}td,th{border:1px solid #ddd;padding:3px 7px;text-align:left;vertical-align:top;font-size:12px}'
           'th{background:#f3f3ef}.ok{color:#1a7f37}.bad{color:#b42318}.warn{color:#9a6700}'
           '.finding{display:inline-block;margin:8px 10px 8px 0;vertical-align:top;max-width:372px}'
           '.finding svg{border:1px solid #ccc;display:block}.finding .cap{font-size:11px;padding:3px 0;max-width:360px}'
           '.problems{background:#fff4f4;border:1px solid #f0b4b4;padding:8px 10px}'
           '</style></head><body>']
    board = str(cert.get('board', '?'))
    total = claims.get('total')
    verdict = ('clean' if total == 0 else f'{claims.get("n_errors", "?")} error(s), '
               f'{claims.get("n_unconnected", "?")} open(s)')
    cls = 'ok' if total == 0 else 'bad'
    out.append(f'<h1>{e(board)}</h1>')
    out.append(f'<div>arm <code>{e(str((cert.get("arm") or {}).get("name", "?")))}</code> · '
               f'<span class="{cls}">{e(verdict)}</span>'
               + (f' · <span class="warn">{sum(dang.values())} stub(s) left</span>' if sum(dang.values()) else '')
               + f' · wall {_fmt(cert.get("wall_seconds"), 1)} s</div>')
    attempts = route.get('attempts') or []
    if attempts:
        out.append('<div>router: ' + ' → '.join(
            f"{a.get('threads')} thr, cap {a.get('timeout_s')} s: "
            + ('routed' if a.get('routed') else 'timed out' if a.get('timed_out') else e(str(a.get('error'))))
            + f", {_fmt(a.get('seconds'), 1)} s, {a.get('passes', 0)} pass(es)" for a in attempts) + '</div>')
    elif route:
        out.append(f'<div>router: {route.get("passes", 0)} pass(es) in {_fmt(route.get("seconds"), 1)} s'
                   + (f' · <span class="bad">{e(str(route.get("error")))}</span>' if route.get('error') else '')
                   + '</div>')
    if problems:
        out.append('<div class="problems"><b>Not rendered as a result:</b><ul>')
        out.extend(f'<li>{e(str(p))}</li>' for p in problems)
        out.append('</ul></div>')

    out.append('<h2>Stages</h2><table><tr><th>stage</th><th>mode</th><th>committed</th>'
               '<th>rolled back</th><th>denied</th><th>note</th></tr>')
    for st in cert.get('repair') or []:
        note = (f"removed {st.get('removed')} stub(s) in {st.get('rounds')} round(s)"
                if st.get('stage') == 'trim' else '')
        if st.get('mode') == 'steps':
            note = f"{len(st.get('log') or [])} operator step(s)"
        out.append(f'<tr><td>{e(str(st.get("stage")))}</td><td>{e(str(st.get("mode")))}</td>'
                   f'<td>{st.get("committed", 0)}</td><td>{st.get("rolled_back", 0)}</td>'
                   f'<td>{st.get("denied", 0)}</td><td>{e(note)}</td></tr>')
    out.append('</table>')

    groups = findings(cert)
    out.append('<h2>What was tried, and why not</h2>')
    if not groups:
        out.append('<div>No failure on record: nothing was tried and refused.</div>')
    for op in sorted(groups):
        rows = groups[op]
        out.append(f'<h2><code>{e(op)}</code> — {len(rows)} attempt(s)</h2>')
        if op == 'conflict_reroute' and board_path:
            out.append('<div>')
            for row in rows:
                centre = _window_centre(row)
                colour = [row['net']] + [n for n in row.get('set') or [] if n != row['net']]
                marks = [tuple(a) for a in (row.get('anchors') or [])]
                lines = [row['plan_pts']] if row.get('plan_pts') else []
                cap = (f"<b>{e(row['net'])}</b> ({e(str(row['type']))}) · "
                       f"<span class=\"{'ok' if row['outcome'] == 'ok' else 'bad'}\">{e(str(row['outcome']))}</span>")
                if row.get('plan'):
                    cap += f'<br>drawn: {e(row["plan"])}'
                if row.get('set'):
                    cap += f'<br>through: {e(", ".join(row["set"]))}'
                if row.get('why'):
                    cap += f'<br>{e(row["why"])}'
                for t in row['tried'][:6]:
                    cap += f'<br><span style="color:#555">{e(t)}</span>'
                out.append('<div class="finding">')
                if centre:
                    try:
                        out.append(copper_svg(board_path, centre, colour=colour,
                                              marks=marks, lines=lines))
                    except Exception as ex:          # the picture is a bonus
                        out.append(f'<div class="problems">copper not drawn: {e(type(ex).__name__)}: {e(str(ex))}</div>')
                else:
                    out.append('<div class="problems">no position on record</div>')
                out.append(f'<div class="cap">{cap}</div></div>')
            out.append('</div>')
        else:
            out.append('<table><tr><th>outcome</th><th>net</th><th>why not</th><th>tried</th></tr>')
            for row in rows:
                tried = '<br>'.join(e(t) for t in row['tried'][:6])
                cls = 'ok' if row['outcome'] == 'ok' else ('warn' if row['outcome'] == 'not_applicable' else 'bad')
                out.append(f'<tr><td class="{cls}">{e(str(row["outcome"]))}</td>'
                           f'<td>{e(row["net"])}</td><td>{e(row["why"])}</td><td>{tried}</td></tr>')
            out.append('</table>')

    opens = open_connections(cert)
    claimed = (cert.get('claims_after') or {}).get('n_unconnected') or 0
    if opens or claimed:
        out.append('<h2>Open connections</h2>')
        if opens:
            out.append('<table><tr><th>net</th><th>attempt</th><th>items</th></tr>')
            for r in opens:
                out.append(f'<tr><td>{e(str(r["net"]))}</td>'
                           f'<td>{e(str(r["outcome"]))}</td>'
                           f'<td>{e(" / ".join(r["items"]))}</td></tr>')
            out.append('</table>')
        else:
            out.append(f'<div class="problems">{int(claimed)} open connection(s) '
                       f'claimed, but the certificate does not say which: '
                       f'needed information.</div>')

    pads = blocking_pads(cert)
    if pads:
        out.append('<h2>Pads in the way</h2><table><tr><th>operator</th>'
                   '<th>net</th><th>outcome</th><th>blocker</th></tr>')
        for r in pads:
            out.append(f'<tr><td><code>{e(str(r["operator"]))}</code></td>'
                       f'<td>{e(str(r["net"]))}</td><td>{e(str(r["outcome"]))}</td>'
                       f'<td>{e(r["why"])}</td></tr>')
        out.append('</table>')

    sites = via_sites(cert)
    if sites:
        out.append('<h2>Via sites tried</h2><table><tr><th>operator</th>'
                   '<th>sites</th></tr>')
        for r in sites:
            out.append(f'<tr><td><code>{e(str(r["operator"]))}</code></td>'
                       f'<td>{e(json.dumps(r["sites"], ensure_ascii=False, default=str)[:600])}</td></tr>')
        out.append('</table>')

    missing = needed_information(cert)
    if missing:
        out.append('<h2>Needed information</h2><ul>')
        for m in missing:
            out.append(f'<li>{e(m["text"])} <span class="warn">— {e(m["from"])}</span></li>')
        out.append('</ul>')

    residue = cert.get('residue') or {}
    if residue:
        out.append('<h2>Left on the board</h2><pre>' + e(json.dumps(residue, ensure_ascii=False, indent=1)[:3000]) + '</pre>')
    out.append('</body></html>')
    return '\n'.join(out)


def render_dir(certs_dir, boards_dir=None, out_dir=None, limit=0,
               index_name='index.html') -> dict:
    """Every certificate in a directory -> one self-contained page each.

    A certificate that does not load is listed on the index with its
    problems and gets no page: the index is a product of the data too.
    `boards_dir` is searched by file name for the board a certificate
    refers to (the copper windows); without it the pages are text-only.
    """
    import glob

    certs = sorted(glob.glob(os.path.join(str(certs_dir), '*.json')))
    if limit:
        certs = certs[:limit]
    out_dir = str(out_dir or os.path.join(str(certs_dir), 'pages'))
    os.makedirs(out_dir, exist_ok=True)
    boards = {}
    if boards_dir:
        for f in glob.glob(os.path.join(str(boards_dir), '**', '*.kicad_pcb'),
                           recursive=True):
            boards[os.path.basename(f)] = f
    rows, made = [], 0
    for path in certs:
        cert, problems = load_cert(path)
        name = os.path.basename(path)
        if cert is None:
            # a bench cell record points at its certificate; follow it
            try:
                with open(path, encoding='utf-8') as fh:
                    cell = json.load(fh)
            except (OSError, ValueError):
                cell = None
            rel = (cell or {}).get('cert') if isinstance(cell, dict) else None
            if rel:
                base = os.path.dirname(os.path.dirname(os.path.abspath(path)))
                cand = os.path.join(base, rel)
                if os.path.exists(cand):
                    cert, problems = load_cert(cand)
        if cert is None:
            rows.append({'cert': name, 'board': '', 'ok': False,
                         'problems': problems, 'page': ''})
            continue
        board = boards.get(os.path.basename(str(cert.get('board') or '')))
        page = render(cert, board, problems)
        fname = os.path.splitext(name)[0] + '.html'
        with open(os.path.join(out_dir, fname), 'w', encoding='utf-8') as fh:
            fh.write(page)
        made += 1
        claims = cert.get('claims_after') or {}
        rows.append({'cert': name, 'board': str(cert.get('board') or ''),
                     'ok': True, 'problems': [],
                     'page': fname,
                     'verdict': ('clean' if claims.get('total') == 0
                                 else f'{claims.get("n_errors", "?")} error(s)'),
                     'needed': len(needed_information(cert))})
    e = html.escape
    parts = ['<!doctype html><html lang="en"><head><meta charset="utf-8">',
             '<title>fluxpcb — board pages</title>',
             '<style>body{font:13px/1.45 -apple-system,Segoe UI,Roboto,sans-serif;'
             'margin:20px;color:#222}table{border-collapse:collapse}'
             'td,th{border:1px solid #ddd;padding:3px 7px;font-size:12px;text-align:left}'
             'th{background:#f3f3ef}.ok{color:#1a7f37}.bad{color:#b42318}</style>'
             '</head><body>',
             f'<h1>{len(rows)} certificates, {made} pages</h1>',
             '<table><tr><th>certificate</th><th>board</th><th>verdict</th>'
             '<th>needed information</th><th>page</th></tr>']
    for r in rows:
        if not r['ok']:
            parts.append(f'<tr><td>{e(r["cert"])}</td><td>—</td>'
                         f'<td class="bad">{e("; ".join(r["problems"]))}</td>'
                         f'<td>—</td><td>—</td></tr>')
        else:
            cls = 'ok' if r['verdict'] == 'clean' else 'bad'
            parts.append(f'<tr><td>{e(r["cert"])}</td><td>{e(r["board"])}</td>'
                         f'<td class="{cls}">{e(r["verdict"])}</td>'
                         f'<td>{r["needed"]}</td>'
                         f'<td><a href="{e(r["page"])}">{e(r["page"])}</a></td></tr>')
    parts.append('</table></body></html>')
    idx = os.path.join(out_dir, index_name)
    with open(idx, 'w', encoding='utf-8') as fh:
        fh.write('\n'.join(parts))
    return {'pages': made, 'index': idx, 'rows': rows, 'out_dir': out_dir}


def main(argv=None):
    import argparse

    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('cert', help='a cert.json, or a directory of them')
    ap.add_argument('board', nargs='?', help='the .kicad_pcb for the copper windows')
    ap.add_argument('-o', '--out', default=None)
    ap.add_argument('--boards', default=None,
                    help='directory searched by name for the boards (batch mode)')
    ap.add_argument('--limit', type=int, default=0)
    args = ap.parse_args(argv)
    if os.path.isdir(args.cert):
        res = render_dir(args.cert, args.boards or args.board, args.out or None,
                         limit=args.limit)
        print(res['index'])
        return 0
    cert, problems = load_cert(args.cert)
    if cert is None:
        print('\n'.join(problems), file=sys.stderr)
        return 2
    if args.board and not os.path.exists(args.board):
        problems.append(f'board not found: {args.board}')
        args.board = None
    page = render(cert, args.board, problems)
    out = args.out or os.path.splitext(args.cert)[0] + '.board.html'
    with open(out, 'w', encoding='utf-8') as fh:
        fh.write(page)
    print(out)
    return 0


if __name__ == '__main__':
    sys.exit(main())
