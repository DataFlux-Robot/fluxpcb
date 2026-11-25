"""Run the physical audit over a random draw of bench boards.

One board proved the chain; this asks whether it generalises.  The batch:

  * draws n boards from the 119 PCBWorld boards with a recorded seed
    (`random.Random(seed).sample(sorted(ids), n)`), so the draw is reproducible;
  * derives a spec per board (`tools.audit_spec.derive`) — the physics inputs
    are absent, so the audit reports measurements and names the unknowns;
  * runs `phys_audit.run` per board with a per-board try/except: a crash is a
    row in the summary, never the end of the batch;
  * resumes by board fingerprint, and writes `summary.json`, `summary.md` and
    an offline `index.html` (the row style of `dashboard/board_page.render_dir`).

    pool(runs) -> [board ids]
    draw(n=5, seed=20261125) -> {'seed', 'ids', 'pool', 'features'}
    run_boards(ids, out_dir, mode='full', probe_a=0.5) -> summary dict
    main(argv)
"""
from __future__ import annotations

import glob
import hashlib
import json
import os
import random
import time

RUNS = os.environ.get('FLUXPCB_BENCH_RUNS',
                      'E:/DATA/vscode/ZCODE/z_lab/fp/full4_run')
BOARDS = os.environ.get('FLUXPCB_BENCH_BOARDS', 'C:/zcode_pcbworld/boards')
ARM = '9a83911c1baa'


def pool(runs=RUNS):
    ids = set()
    for p in glob.glob(os.path.join(runs, 'cells', f'*__{ARM}.json')):
        try:
            with open(p, encoding='utf-8') as f:
                ids.add(json.load(f).get('board'))
        except Exception:
            continue
    return sorted(i for i in ids if i)


def features_of(board, runs=RUNS):
    p = os.path.join(runs, 'cells', f'{board}__{ARM}.json')
    try:
        with open(p, encoding='utf-8') as f:
            return json.load(f).get('features') or {}
    except Exception:
        return {}


def draw(n=5, seed=20261125, runs=RUNS):
    ids = pool(runs)
    if len(ids) < n:
        raise SystemExit(f'only {len(ids)} boards in the pool')
    picked = random.Random(seed).sample(ids, n)
    return {'seed': seed, 'n': n, 'ids': picked, 'pool': len(ids),
            'features': {b: features_of(b, runs) for b in picked}}


def board_path(board, runs=RUNS):
    """Our routed result, with its project; the reference board as fall back."""
    p = os.path.join(runs, 'runs', ARM, board, f'{board}.kicad_pcb')
    if os.path.exists(p):
        return p
    p = os.path.join(BOARDS, board, 'circuit.reference.kicad_pcb')
    return p if os.path.exists(p) else None


def _fp(path):
    try:
        with open(path, 'rb') as f:
            return hashlib.sha256(f.read()).hexdigest()[:16]
    except OSError:
        return None


def _row(board, status, **kw):
    row = {'board': board, 'status': status}
    row.update(kw)
    return row


def run_boards(ids, out_dir, mode='full', probe_a=0.5, runs=RUNS,
               runs_drc=3, resume=True):
    import numpy as _np  # noqa: F401  (kept for the same env as phys_audit)
    from . import audit_spec, copper_graph as cg, phys_audit

    os.makedirs(out_dir, exist_ok=True)
    rows = []
    for board in ids:
        t0 = time.time()
        path = board_path(board, runs)
        feat = features_of(board, runs)
        if not path:
            rows.append(_row(board, 'missing', error='no board file'))
            continue
        o = os.path.join(out_dir, board)
        os.makedirs(o, exist_ok=True)
        aj = os.path.join(o, 'audit.json')
        if resume and os.path.exists(aj):
            try:
                with open(aj, encoding='utf-8') as f:
                    old = json.load(f)
                if (old.get('mode') == mode
                        and (old.get('board_meta') or {}).get('fp') == _fp(path)):
                    rows.append(_summarize(board, old, 'resumed', o, feat,
                                           time.time() - t0))
                    continue
            except Exception:
                pass
        try:
            cell = (0.3 if (feat.get('area_mm2') or 0) > 8000
                    else 0.2 if (feat.get('area_mm2') or 0) > 2000 else 0.15)
            cop = cg.extract(path, cell_mm=cell)
            spec = audit_spec.derive(cop, path, features=feat, probe_a=probe_a)
            sp = audit_spec.write(spec, o)
            a = phys_audit.run(path, sp, mode=mode, out_dir=o, runs_drc=runs_drc)
            rows.append(_summarize(board, a, 'ok', o, feat, time.time() - t0))
        except Exception as e:
            rows.append(_row(board, 'crash', error=f'{type(e).__name__}: {e}',
                             out=o, seconds=round(time.time() - t0, 2),
                             features=feat))
    summary = {'generated': time.strftime('%Y-%m-%d %H:%M:%S'), 'mode': mode,
               'probe_a': probe_a, 'rows': rows,
               'ok': sum(1 for r in rows if r['status'] in ('ok', 'resumed')),
               'crashed': sum(1 for r in rows if r['status'] == 'crash'),
               'missing': sum(1 for r in rows if r['status'] == 'missing')}
    with open(os.path.join(out_dir, 'summary.json'), 'w', encoding='utf-8') as f:
        json.dump(summary, f, indent=1, ensure_ascii=False)
    with open(os.path.join(out_dir, 'summary.md'), 'w', encoding='utf-8') as f:
        f.write(summary_md(summary))
    summary['index'] = render_index(summary, out_dir)
    return summary


def _summarize(board, audit, status, out_dir, feat, seconds):
    meta = audit.get('board_meta') or {}
    s = audit.get('summary') or {}
    drc = audit.get('drc') or {}
    before = (drc.get('before') or {}) if drc else {}
    after = (drc.get('after_pour') or {}) if drc else {}
    worst = sorted((audit.get('checks') or []), key=lambda c: (
        {'P0': 0, 'P1': 1, 'P2': 2, 'P3': 3}.get(c.get('severity'), 9)))[:1]
    return {'board': board, 'status': status, 'out': out_dir,
            'seconds': round(seconds, 2),
            'area_mm2': meta.get('board_area_mm2') or feat.get('area_mm2'),
            'layers': len(meta.get('layers') or []) or feat.get('copper_layers'),
            'nets': meta.get('nets') or feat.get('nets'),
            'pads': feat.get('pads'),
            'gnd_net': meta.get('gnd_net') or (audit.get('ground') or {}).get('net'),
            'ground_why': (audit.get('ground') or {}).get('why'),
            'rails': len(audit.get('rails') or []),
            'counts': s.get('counts') or {},
            'confirmed': s.get('confirmed', 0),
            'estimate': s.get('estimate', 0),
            'unknown': s.get('unknown', 0),
            'worst': worst[0] if worst else None,
            'drc_before': ([before.get('errors'), before.get('unconnected')]
                           if drc else None),
            'drc_after': ([after.get('errors'), after.get('unconnected')]
                          if drc else None),
            'drc_ok': drc.get('ok'),
            'pour': ((audit.get('pour') or {}).get('status')),
            'page': audit.get('page'),
            'elapsed_s': audit.get('elapsed_s')}


def _fmt(x):
    if x is None:
        return '—'
    if isinstance(x, float):
        return f'{x:.2f}'
    if isinstance(x, list):
        return ' → '.join('—' if v is None else str(v) for v in x)
    return str(x)


def summary_md(s):
    lines = [f"# 物理审计批跑（{s['mode']}，probe {s['probe_a']} A）", '',
             f"生成 {s['generated']}；ok {s['ok']}，crash {s['crashed']}，"
             f"missing {s['missing']}。", '',
             '| 板 | 面积 mm² | 层 | 网 | 接地网 | 轨 | P0/P1/P2/P3 | confirmed/estimate/unknown | DRC 前→后 | 用时 s |',
             '|---|---|---|---|---|---|---|---|---|---|']
    for r in s['rows']:
        c = r.get('counts') or {}
        lines.append(
            f"| {r['board']} | {_fmt(r.get('area_mm2'))} | {_fmt(r.get('layers'))} | "
            f"{_fmt(r.get('nets'))} | {_fmt(r.get('gnd_net'))} | {_fmt(r.get('rails'))} | "
            f"{c.get('P0',0)}/{c.get('P1',0)}/{c.get('P2',0)}/{c.get('P3',0)} | "
            f"{r.get('confirmed',0)}/{r.get('estimate',0)}/{r.get('unknown',0)} | "
            f"{_fmt(r.get('drc_before'))} → {_fmt(r.get('drc_after'))} | "
            f"{_fmt(r.get('elapsed_s') or r.get('seconds'))} |")
        if r.get('status') != 'ok' and r.get('error'):
            lines.append(f"| ↑ {r['status']}: {r['error'][:140]} | | | | | | | | | |")
    return '\n'.join(lines) + '\n'


def render_index(s, out_dir):
    """A page listing each board's audit page — same shape as the cert index."""
    rows = []
    for r in s['rows']:
        page = r.get('page')
        link = (f'<a href="{os.path.basename(os.path.dirname(page))}/'
                f'{os.path.basename(page)}">audit</a>') if page else '—'
        c = r.get('counts') or {}
        c = r.get('counts') or {}
        rows.append(
            f'<tr class="{"ok" if r["status"] in ("ok", "resumed") else "bad"}">'
            f'<td><b>{r["board"]}</b></td><td>{_fmt(r.get("area_mm2"))}</td>'
            f'<td>{_fmt(r.get("layers"))}</td><td>{_fmt(r.get("nets"))}</td>'
            f'<td>{_fmt(r.get("gnd_net"))}</td>'
            f'<td>{c.get("P0",0)}/{c.get("P1",0)}/{c.get("P2",0)}/{c.get("P3",0)}</td>'
            f'<td>{r.get("confirmed",0)}/{r.get("estimate",0)}/{r.get("unknown",0)}</td>'
            f'<td>{_fmt(r.get("elapsed_s") or r.get("seconds"))}</td>'
            f'<td>{link}</td></tr>')
    html = f'''<!doctype html><html lang="en"><head><meta charset="utf-8">
<title>Physical audit batch</title><style>
body {{ font:14px/1.5 -apple-system,"Segoe UI",sans-serif; max-width:1100px;
  margin:28px auto; padding:0 18px; color:#1c2430; background:#fbfcfd; }}
table {{ border-collapse:collapse; width:100%; }}
th,td {{ text-align:left; padding:6px 8px; border-bottom:1px solid #dde3ea; }}
th {{ font-size:12px; text-transform:uppercase; color:#6b7785; }}
tr.bad td {{ background:#fdf0f0; }} .dim {{ color:#6b7785; }}
code {{ font-family:ui-monospace,Consolas,monospace; }}
</style></head><body>
<h1>Physical audit — {s['ok']} board(s), {s['mode']}</h1>
<p class="dim">probe current {s['probe_a']} A · generated {s['generated']} ·
crashes {s['crashed']} · missing {s['missing']}</p>
<table><thead><tr><th>board</th><th>area mm²</th><th>layers</th><th>nets</th>
<th>ground</th><th>P0/P1/P2/P3</th><th>confirmed/estimate/unknown</th>
<th>s</th><th>page</th></tr></thead><tbody>{''.join(rows)}</tbody></table>
<p class="dim">Each page is self-contained and offline. A crashed board is a row
here, not a silent absence.</p></body></html>'''
    path = os.path.join(out_dir, 'index.html')
    with open(path, 'w', encoding='utf-8') as f:
        f.write(html)
    return path


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(prog='fluxpcb.tools.phys_batch')
    ap.add_argument('out_dir')
    ap.add_argument('--n', type=int, default=5)
    ap.add_argument('--seed', type=int, default=20261125)
    ap.add_argument('--boards', nargs='*', default=None,
                    help='explicit ids instead of a random draw')
    ap.add_argument('--mode', choices=('fast', 'full'), default='full')
    ap.add_argument('--probe-a', type=float, default=0.5)
    ap.add_argument('--runs', default=RUNS)
    a = ap.parse_args(argv)
    os.makedirs(a.out_dir, exist_ok=True)
    if a.boards:
        ids, seed = a.boards, None
    else:
        d = draw(a.n, a.seed, a.runs)
        ids, seed = d['ids'], d['seed']
        with open(os.path.join(a.out_dir, 'draw.json'), 'w', encoding='utf-8') as f:
            json.dump(d, f, indent=1, ensure_ascii=False)
    s = run_boards(ids, a.out_dir, mode=a.mode, probe_a=a.probe_a, runs=a.runs)
    print(json.dumps({'ids': ids, 'seed': seed, 'ok': s['ok'],
                      'crashed': s['crashed'], 'index': s['index']},
                     indent=1, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
