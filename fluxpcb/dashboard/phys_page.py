"""The audit page: one offline HTML from audit.json, numbers and pictures.

Everything the page shows comes from `audit.json` — no solver is called, no
board is opened, no network.  Pictures are small PNGs built from the arrays
the audit stored (J fields, return-distance rasters, mode shapes, thermal
fields) and embedded as data URIs, so the file opens anywhere.

The layout answers, in order: what was measured on this board, where the
current and its return go, what the pour's modes are, what a heatsink does,
where the coupling lands, and what contradicts what.

    render(audit_json, html_out) -> path
"""
from __future__ import annotations

import base64
import io
import json
import math
import os

import numpy as np

SEV_COLOR = {'P0': '#b00020', 'P1': '#d9480f', 'P2': '#b58900', 'P3': '#4a6b8a'}

# a compact blue-cyan-green-yellow-red ramp; no matplotlib dependency
_STOPS = [(0.0, (12, 24, 64)), (0.25, (0, 110, 160)), (0.5, (30, 160, 90)),
          (0.75, (240, 190, 40)), (1.0, (190, 30, 30))]


def _cmap(x):
    """x in 0..1 -> (r,g,b), vectorised."""
    x = np.clip(np.asarray(x, dtype=float), 0.0, 1.0)
    out = np.zeros(x.shape + (3,), np.uint8)
    for (a, ca), (b, cb) in zip(_STOPS, _STOPS[1:]):
        m = (x >= a) & (x <= b)
        if not m.any():
            continue
        t = (x[m] - a) / max(b - a, 1e-9)
        for k in range(3):
            out[..., k][m] = (ca[k] + t * (cb[k] - ca[k])).astype(np.uint8)
    return out


def _png(arr, vmin=None, vmax=None, void=(238, 238, 238), log=False):
    """Render a 2-D array as a PNG data URI (rows top-to-bottom)."""
    from PIL import Image
    a = np.asarray(arr, dtype=float)
    mask = np.isfinite(a)
    if log:
        lo = math.log10(max(float(a[mask].min()), 1e-9)) if mask.any() else 0.0
        hi = math.log10(max(float(a[mask].max()), 1e-8)) if mask.any() else 1.0
        if hi - lo < 1e-9:
            hi = lo + 1.0
        t = (np.log10(np.maximum(a, 10 ** lo)) - lo) / (hi - lo)
    else:
        lo = float(a[mask].min()) if mask.any() else 0.0
        hi = float(a[mask].max()) if mask.any() else 1.0
        if vmin is not None:
            lo = vmin
        if vmax is not None:
            hi = vmax
        if hi - lo < 1e-12:
            hi = lo + 1.0
        t = (a - lo) / (hi - lo)
    rgb = _cmap(np.nan_to_num(t, nan=0.0))
    rgb[~mask] = void
    img = Image.fromarray(np.flipud(rgb), 'RGB')
    img = img.resize((img.width * 3, img.height * 3), Image.NEAREST)
    buf = io.BytesIO()
    img.save(buf, format='PNG', optimize=True)
    return ('data:image/png;base64,'
            + base64.b64encode(buf.getvalue()).decode('ascii'))


def _f(x, nd=2):
    if x is None:
        return '—'
    if isinstance(x, float):
        return f'{x:.{nd}f}'
    if isinstance(x, list):
        return ', '.join(_f(v, nd) for v in x)
    return str(x)


def _table(rows, head, cls=''):
    h = ''.join(f'<th>{c}</th>' for c in head)
    body = []
    for r in rows:
        body.append('<tr>' + ''.join(f'<td>{c}</td>' for c in r) + '</tr>')
    return (f'<table class="{cls}"><thead><tr>{h}</tr></thead>'
            f'<tbody>{"".join(body)}</tbody></table>')


def _stack(st):
    if not st:
        return 'not declared'
    return (f"{st.get('thickness_mm')} mm, {st.get('copper_mm')} mm copper, "
            f"er={st.get('er')} — {st.get('source')}")


def _chips(counts):
    return ' '.join(f'<span class="chip" style="background:{SEV_COLOR.get(k, "#666")}">'
                    f'{k} {v}</span>' for k, v in sorted(counts.items()))


def render(audit_json, html_out):
    with open(audit_json, encoding='utf-8') as f:
        d = json.load(f)
    bm = d.get('board_meta') or {}
    s = d.get('summary') or {}
    has_pour = (d.get('pour') or {}).get('status') == 'ok'
    board_state = 'poured on the audit copy' if has_pour else 'as routed'
    parts = []
    parts.append(f'''<header>
<h1>Physical audit — {os.path.basename(d.get('board', ''))}</h1>
<p class="sub">mode <b>{d.get('mode')}</b> · board <code>{d.get('board')}</code> ·
fingerprint <code>{bm.get('fp')}</code> · {bm.get('size_mm')} mm ·
{bm.get('board_area_mm2')} mm² · {bm.get('nets')} nets · {'/'.join(bm.get('layers') or [])} ·
{bm.get('segments')} track segments · {bm.get('zones')} zones
({board_state})</p>
<p class="sub">stackup: {_stack(d.get('stackup') or {})}</p>
<p>{_chips(s.get('counts', {}))}</p>
</header>''')

    # ---------------------------------------------------------- what/assumed
    run = d.get('run') or {}
    parts.append('<section><h2>What ran, what was assumed, what is missing</h2><div class="three">')
    for title, items in (('ran', run.get('what') or []),
                         ('assumed (inputs)', [f"{i['kind']}: {i['what']} "
                                               + ' '.join(f'{k}={v}' for k, v in (i.get('value') or {}).items())
                                               + f" — {i['source']}"
                                               for i in (d.get('inputs') or [])]),
                         ('missing (needed_information)', run.get('missing') or [])):
        lis = ''.join(f'<li>{x}</li>' for x in items) or '<li class="dim">none</li>'
        parts.append(f'<div class="col"><h3>{title}</h3><ul>{lis}</ul></div>')
    parts.append('</div></section>')

    # ------------------------------------------------------------- findings
    checks = [c for c in d.get('checks') or [] if 'rule_id' in c]
    order = {'P0': 0, 'P1': 1, 'P2': 2, 'P3': 3}
    checks.sort(key=lambda c: (order.get(c.get('severity'), 9), c.get('rule_id') or ''))
    rows = []
    for c in checks:
        w = c.get('where') or {}
        where = w.get('text') if isinstance(w, dict) else str(w)
        rows.append([
            f'<span class="chip" style="background:{SEV_COLOR.get(c.get("severity"), "#666")}">'
            f'{c.get("severity")}</span>',
            f'<code>{c.get("rule_id")}</code>',
            _f(where),
            f'{_f(c.get("measured"))} {c.get("units") or ""}'
            + (f' <span class="dim">/ {_f(c.get("limit"))}</span>' if c.get('limit') is not None else ''),
            c.get('status'), _f(c.get('assumption')),
        ])
    parts.append('<section><h2>Findings</h2>'
                 + _table(rows, ['sev', 'rule', 'where', 'measured / limit',
                                 'status', 'assumption'], 'findings')
                 + '<p class="note">Every row carries its rule id, where it is, what was '
                   'measured against what limit, and the assumption it rests on. '
                   'status=<b>confirmed</b> means measured on this copper; '
                   '<b>estimate</b> is model-limited; <b>unknown</b> means the input '
                   'was missing and the row says what is missing.</p></section>')

    # ---------------------------------------------------------------- current
    rails = d.get('rails') or []
    if rails:
        rows, figs = [], []
        for r in rails:
            f = r.get('field') or {}
            nw = r.get('narrowest') or {}
            rows.append([f'<b>{r["net"]}</b>', _f(r.get('current_a')),
                         _f(nw.get('width_mm'), 3), _f(r.get('j_geom_a_mm2'), 1),
                         _f(f.get('j_robust_a_mm2'), 1), _f((f.get('drop_v') or 0) * 1e3, 2),
                         _f(f.get('drop_pct'), 3),
                         _f(r.get('ipc2221_i_max_a_10c'), 2),
                         _f(r.get('ipc_use_pct'), 1),
                         _f(r.get('via_current_max_a'), 3)])
            if f.get('j_map') is not None:
                figs.append(f'<figure><img src="{_png(f["j_map"], vmin=0)}" '
                            f'alt="J {r["net"]}"><figcaption>{r["net"]}: smoothed '
                            f'J, 0..{_f(max(max(x) if isinstance(x, list) else x for x in f["j_map"]), 1)} A/mm²'
                            f' · 2 mm max {_f(f.get("j_robust_a_mm2"), 1)} A/mm²</figcaption></figure>')
        parts.append('<section><h2>Current</h2>'
                     + _table(rows, ['rail', 'I (A)', 'narrowest (mm)', 'J geom (A/mm²)',
                                     'J field (2 mm max)', 'drop (mV)', 'drop (%)',
                                     'IPC-2221 I max (A)', 'IPC use (%)',
                                     'max barrel (A)'], 'num')
                     + '<div class="figs">' + ''.join(figs) + '</div>'
                     + '<p class="note">J field is the 95th percentile of a 2 mm-smoothed '
                       'solve on the real copper (raw J has a singularity at every re-entrant '
                       'corner: a 45° bend reads ~1.8× the geometric value). The geometric '
                       'column is I/(w·t) at the narrowest segment — an upper bound when the '
                       'net branches. The cross-check table below says where the two disagree '
                       'and why.</p></section>')

    # ----------------------------------------------------------------- return
    ret = d.get('return') or {}
    sigs = ret.get('signals') or []
    if sigs:
        rows = []
        for s_ in sigs:
            b = s_.get('base') or {}
            p = s_.get('poured') or {}
            db = b.get('dc') or {}
            dp = p.get('dc') or {}
            rows.append([f'<b>{s_.get("name") or s_["net"]}</b>',
                         f'{_f(b.get("max_gap_mm"), 1)} → {_f(p.get("max_gap_mm"), 1)}',
                         f'{_f(b.get("gnd_share_mean"), 2)} → {_f(p.get("gnd_share_mean"), 2)}',
                         f'{_f(db.get("gnd_resistance_mohm"), 1)} → {_f(dp.get("gnd_resistance_mohm"), 1)}',
                         f'{_f(s_.get("analytic_fraction_3h"), 2)}'])
        parts.append('<section><h2>Return path</h2>'
                     + _table(rows, ['signal', 'gap to GND (mm) base → poured',
                                     'GND share in 3h disc', 'GND resistance (mΩ)',
                                     'infinite-plane 3h benchmark'], 'num')
                     + '<p class="note">gap = largest distance from the route to any GND '
                       'copper; on a board with no plane the route can be 18 mm from a return '
                       'conductor. The GND resistance is a DC solve between the driver and '
                       'load ground pads — a power-return number, <i>not</i> a score of AC '
                       'return (a DC solve legitimately puts the current on the '
                       'least-resistance path, away from a short trace). AC return is the '
                       'availability question: is there continuous GND copper within 3h?</p>')
    maps = ret.get('maps') or {}
    if maps.get('base') is not None:
        b64b = _png(maps['base'], vmin=0, vmax=maps.get('scale_mm') or 20)
        b64p = (_png(maps['poured'], vmin=0, vmax=maps.get('scale_mm') or 20)
                if maps.get('poured') is not None else None)
        figs = (f'<figure><img src="{b64b}" alt="gap base"><figcaption>as routed: '
                f'distance to GND copper (0–{maps.get("scale_mm")} mm)</figcaption></figure>')
        if b64p:
            figs += (f'<figure><img src="{b64p}" alt="gap poured"><figcaption>after the pour: '
                     f'same scale</figcaption></figure>')
        parts.append('<div class="figs">' + figs + '</div>')
    pl = ret.get('plane')
    if pl:
        rows = [[_f(v['area_mm2'], 1), f'{_f(v["at"])}', ', '.join(v.get('footprints') or [])]
                for v in pl.get('voids') or []]
        parts.append('<h3>Plane coverage</h3>'
                     f'<p>{_f(pl["coverage_pct"], 1)}% of the {pl["board_area_mm2"]} mm² '
                     f'board is GND copper on the plane layer; {len(pl.get("voids") or [])} '
                     f'interior voids ≥ 1 mm².</p>'
                     + _table(rows, ['void (mm²)', 'at (mm)', 'footprints inside'])
                     + '<p class="note">A large interior void is usually an island the '
                       'filler removed because no GND pad or via reaches it — a pour '
                       'parameter, not a routing defect. The fix is a stitch, not a reroute.</p>')

    # ----------------------------------------------------------------- cavity
    cav = d.get('cavity') or {}
    if cav.get('status') == 'ok':
        rows = [[_f(m.get('f_ghz'), 3), m.get('label') or '', _f(m.get('nodal_frac'), 2)]
                for m in cav.get('modes') or []]
        figs = []
        for mo in (cav.get('mode_fields') or [])[:4]:
            if mo.get('phi') is None:
                continue
            phi = np.asarray(mo['phi'])
            figs.append(f'<figure><img src="{_png(np.abs(phi))}" alt="mode">'
                        f'<figcaption>{_f(mo.get("f_ghz"), 3)} GHz · nodal '
                        f'{_f(mo.get("nodal_frac"), 2)}</figcaption></figure>')
        note = ('what-if: this board has one GND pour and no second plane, so a '
                'parallel-plate cavity does not exist here. The modes below treat the other '
                'layer as an ideal reference — they show the pour\'s shape, not a resonance '
                'this board can have.'
                if cav.get('what_if') else
                'Neumann modes on the real pour, paired against emitter harmonics.')
        parts.append('<section><h2>Plane modes</h2>'
                     + _table(rows, ['mode (GHz)', '', 'nodal fraction'])
                     + '<div class="figs">' + ''.join(figs) + '</div>'
                     + f'<p class="note">{note} Solid-rectangle first mode: '
                       f'{_f(cav.get("solid_rect_first_ghz"), 3)} GHz; fragmentation ratio '
                       f'{_f(cav.get("fragmentation_ratio"))}. Pairs with emitter harmonics: '
                       f'{len(cav.get("pairs") or [])}.</p></section>')

    # ---------------------------------------------------------------- thermal
    th = d.get('thermal') or {}
    if th.get('status') == 'skipped':
        rows = [[_f(p.get('ref')), '—', _f(p.get('tjmax_c'), 1)]
                for p in (th.get('parts') or [])]
        parts.append('<section><h2>Thermal</h2>'
                     + _table(rows, ['part', 'P (W)', 'Tjmax (°C)'])
                     + '<p class="note">No part power is declared, so no thermal '
                       'field was solved and no junction temperature is claimed. '
                       'This is a missing input, not a pass.</p></section>')
    if th.get('status') == 'ok':
        base, sink = th.get('base') or {}, th.get('with_sink') or {}
        figs = []
        vmax = max([base.get('tmax_c') or 0, sink.get('tmax_c') or 0, 1])
        for blk, label in ((base, 'no heatsink'), (sink, 'with heatsink')):
            if blk.get('t') is not None:
                figs.append(f'<figure><img src="{_png(blk["t"], vmin=25, vmax=vmax)}" '
                            f'alt="{label}"><figcaption>{label}: max '
                            f'{_f(blk.get("tmax_c"), 1)} °C, mean {_f(blk.get("tmean_c"), 1)} °C, '
                            f'energy balance {_f(blk.get("balance_err"), 4)}</figcaption></figure>')
        rows = []
        for p in th.get('parts') or []:
            rows.append([f'<b>{p["ref"]}</b>', _f(p.get('power_w'), 3),
                         _f(p.get('t_board_base_c'), 1), _f(p.get('t_board_sink_c'), 1),
                         _f((p.get('tj_base') or {}).get('lumped_c'), 1),
                         _f((p.get('tj_base') or {}).get('field_c'), 1),
                         _f((p.get('tj_sink') or {}).get('lumped_c'), 1),
                         _f(p.get('tj_drop_c'), 1), _f(p.get('margin_pct'), 1)])
        parts.append('<section><h2>Thermal: before / after the heatsink</h2>'
                     '<p>Same grid, same colour scale (25 °C → '
                     f'{_f(vmax, 0)} °C). P_total = {_f(th.get("p_total_w"), 2)} W.</p>'
                     '<div class="figs">' + ''.join(figs) + '</div>'
                     + _table(rows, ['part', 'P (W)', 'T board base', 'T board sink',
                                     'Tj lumped base', 'Tj field base', 'Tj lumped sink',
                                     'ΔTj (K)', 'sink as % of Tjmax'], 'num')
                     + '<p class="note">Tj is computed twice: the theta chain '
                       '(T_amb + P·θ) and the board field (T_board + P·θjc). Where they '
                       'disagree the row is flagged THERMAL.coupling — a part can be hot '
                       'because its neighbour is, which a per-part θja table cannot show.</p>'
                       '</section>')

    # -------------------------------------------------------------------- em
    em = d.get('em') or {}
    if em.get('status') == 'ok':
        rows = [[f'<b>{v["net"]}</b>', _f(v.get('level_db'), 1), v.get('grade'),
                 _f(v.get('worst_aggressor'))] for v in em.get('victims') or []]
        parts.append('<section><h2>Susceptibility map</h2>'
                     f'<figure class="wide"><img src="{_png(em["map_db"], vmin=-60, vmax=0)}" '
                     f'alt="coupling dB"><figcaption>relative coupling, 0 dB = strongest on '
                     f'the board; emitters: '
                     f'{", ".join(e["net"] for e in em.get("emitters") or [])}</figcaption></figure>'
                     + _table(rows, ['victim', 'level (dB)', 'grade', 'worst aggressor'], 'num')
                     + f'<p class="note">{em.get("assumption")}</p></section>')

    # ------------------------------------------------------------ cross-checks
    rows = []
    for r in rails:
        cc = r.get('crosscheck')
        if not cc:
            continue
        rows.append([r['net'], _f(cc.get('j_geom_a_mm2'), 1), _f(cc.get('j_field_p95'), 1),
                     _f(cc.get('ratio')), 'yes' if cc.get('agrees') else 'no',
                     _f(cc.get('note'))])
    for p in (th.get('parts') or []):
        for tag, key in (('base', 'tj_base'), ('sink', 'tj_sink')):
            t = p.get(key)
            if not t:
                continue
            rows.append([f'{p["ref"]} Tj ({tag})', _f(t.get('lumped_c'), 1),
                         _f(t.get('field_c'), 1), _f((t.get('field_vs_lumped_pct') or 0) / 100, 3),
                         'no' if t.get('disputed') else 'yes', _f(t.get('note'))])
    if cav.get('modes'):
        rows.append(['cavity mode 1', _f(cav.get('solid_rect_first_ghz'), 3),
                     _f(cav['modes'][0].get('f_ghz'), 3),
                     _f((cav['modes'][0].get('f_ghz') or 0) /
                        max(cav.get('solid_rect_first_ghz') or 1, 1e-9)),
                     'n/a', 'real pour vs solid rectangle: the pour is fragmented'])
    drc = d.get('drc') or {}
    if drc.get('before'):
        rows.append(['DRC error count before / after pour',
                     _f((drc['before'] or {}).get('errors')),
                     _f((drc.get('after_pour') or {}).get('errors')),
                     '', 'yes' if drc.get('ok') else 'no',
                     'the pour introduced no new error-severity violations'])
    parts.append('<section><h2>Verification: two paths per number</h2>'
                 + _table(rows, ['quantity', 'path A', 'path B', 'A/B',
                                 'agree (15%)', 'why not'], 'num')
                 + '<p class="note">Every number the report acts on is produced twice: '
                   'a field solve against a closed-form estimate, a lumped chain against '
                   'a board field, the real pour against the solid rectangle, the DRC '
                   'before against after. Where they disagree the report says why rather '
                   'than picking one.</p></section>')

    # --------------------------------------------------------------- footer
    parts.append(f'<footer><p>audit {d.get("audit_version")} · '
                 f'{d.get("started")} · {d.get("elapsed_s")} s · '
                 f'{s.get("confirmed", 0)} confirmed, {s.get("estimate", 0)} estimate, '
                 f'{s.get("unknown", 0)} unknown. '
                 'Images are data URIs; this file needs no network.</p></footer>')

    html = f'''<!doctype html><html lang="en"><head><meta charset="utf-8">
<title>Physical audit — {os.path.basename(d.get('board', ''))}</title>
<style>
:root {{ --ink:#1c2430; --dim:#6b7785; --line:#dde3ea; --bg:#fbfcfd; }}
* {{ box-sizing:border-box; }}
body {{ margin:0 auto; max-width:1120px; padding:28px 22px 60px; background:var(--bg);
  color:var(--ink); font:15px/1.5 -apple-system,"Segoe UI",Roboto,"Helvetica Neue",sans-serif; }}
h1 {{ font-size:26px; margin:0 0 6px; letter-spacing:-.01em; }}
h2 {{ font-size:19px; margin:34px 0 10px; padding-bottom:6px; border-bottom:1px solid var(--line); }}
h3 {{ font-size:15px; margin:16px 0 6px; }}
code {{ font:13px/1.4 ui-monospace,SFMono-Regular,Consolas,monospace; }}
.sub {{ color:var(--dim); font-size:13px; margin:2px 0; }}
.dim {{ color:var(--dim); }}
.note {{ color:var(--dim); font-size:13px; max-width:78ch; }}
table {{ border-collapse:collapse; width:100%; margin:10px 0 6px; font-size:13.5px; }}
th {{ text-align:left; font-weight:600; color:var(--dim); border-bottom:1px solid var(--line);
  padding:6px 8px; font-size:12.5px; text-transform:uppercase; letter-spacing:.03em; }}
td {{ padding:6px 8px; border-bottom:1px solid var(--line); vertical-align:top; }}
table.num td:nth-child(n+2) {{ font-variant-numeric:tabular-nums; white-space:nowrap; }}
table.findings td {{ font-size:13px; }}
.chip {{ display:inline-block; color:#fff; border-radius:10px; padding:1px 8px;
  font-size:12px; font-weight:600; margin-right:4px; }}
.three {{ display:grid; grid-template-columns:1fr 1fr 1fr; gap:18px; }}
.col ul {{ margin:6px 0; padding-left:18px; font-size:13px; }}
.col li {{ margin:4px 0; }}
.figs {{ display:flex; flex-wrap:wrap; gap:16px; margin:12px 0; }}
figure {{ margin:0; }}
figure img {{ display:block; border:1px solid var(--line); border-radius:4px;
  image-rendering:pixelated; max-width:430px; height:auto; }}
figure.wide img {{ max-width:100%; }}
figcaption {{ color:var(--dim); font-size:12px; margin-top:4px; max-width:430px; }}
footer {{ margin-top:40px; padding-top:12px; border-top:1px solid var(--line);
  color:var(--dim); font-size:12.5px; }}
@media (max-width:820px) {{ .three {{ grid-template-columns:1fr; }} }}
</style></head><body>{''.join(parts)}</body></html>'''
    with open(html_out, 'w', encoding='utf-8') as f:
        f.write(html)
    return html_out


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(prog='fluxpcb.dashboard.phys_page')
    ap.add_argument('audit_json')
    ap.add_argument('-o', '--out')
    a = ap.parse_args(argv)
    out = a.out or os.path.join(os.path.dirname(a.audit_json), 'audit.html')
    print(render(a.audit_json, out))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
