# -*- coding: utf-8 -*-
"""geomir — the board's geometric IR (M1): the MAP the decision layer
has been missing.

The essence defect (M-round): the loop passed aggregate counters and
class LABELS to the decider, while closing a stubborn open requires
OBJECT-LEVEL spatial understanding — which island, which free corridor,
which exact line. The reference project's 30-minute finish was one
map-reasoned intervention ("draw from (1.93,7.2) to (1.22,2.54) along
the left edge"). This module is that map, as a queryable IR:

  islands_of(net)      -> islands with pad members + anchors + bbox
  free_path(a,b,...)   -> candidate polylines VERIFIED against world
                          geometry (same rules as commit_gate)
  obstacles_near(p, r) -> what is blocking, whose net, how big
  plan_net(net)        -> the full decision package: islands + verified
                          paths + blockers + summary text + SVG window

Pure planning core (candidate_polylines / verify_polyline / summarize /
render_plan) is pcbnew-free and unit-testable; board extraction is a
thin adapter. CLI:  geomir.py PCB NET  ->  one JSON plan on stdout,
neighborhood SVG at <PCB>.<NET>.plan.svg.

State contract (C0): every plan carries the board fingerprint — the
map, like every number, is bound to the state it depicts.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

# DRC-TRUE clearance (0.15): the verifier must answer the SAME question
# the DRC answers. 0.17 (routegrid's margin) rejected 0.021mm-near-miss
# paths that would actually PASS DRC — the map must not lie on the safe
# side so hard that it refuses legal fixes.
CLEAR = float(__import__("os").environ.get("FLUXNT_GEOMIR_CLEAR", "0.15"))
# SMD-to-SMD rule (KiCad default netclass has (clearance 37.5 (type
# smd_smd)) = 0.0375mm): dense BGA/module pads sit 0.1-0.2mm apart BY
# DESIGN and the DRC accepts it. The verifier must ask the DRC's exact
# question per obstacle class — a uniform 0.15 rejected every path
# whose start anchor sits in a dense pad field (the anchor itself).
CLEAR_SMD = float(__import__("os").environ.get("FLUXNT_GEOMIR_CLEAR_SMD",
                                              "0.0375"))


# ===================================================== pure planning core
def via_ok(x, y, nc, segs_by_layer, rects_by_layer, via_r=None,
           rules=None):
    """Through-via legality (T1): the barrel crosses EVERY layer, so it
    must clear foreign copper on ALL of them. The 10 hop-repair shorts
    came from checking only the path's layer. hyuhang822 rule: via
    legality = strictest of the three constraints (barrel-vs-copper per
    layer; hole-to-hole stays with DRC at commit)."""
    from routegrid import seg_seg_dist, pt_rect_dist
    _clear = (rules or {}).get("clearance", CLEAR)
    # T2-fix: via_r is the size of the via WE PLACE (rg.add_via annulus
    # 0.25 -> 0.5 dia), NOT the design-rule MINIMUM (0.4/2=0.2 made the
    # verifier looser — the T2 regression's root cause)
    via_r = 0.25 if via_r is None else via_r
    for li in range(len(segs_by_layer)):
        for net, s, e, hw in segs_by_layer[li]:
            if net == nc:
                continue
            if seg_seg_dist((x, y), (x, y), s, e) < _clear + hw + via_r:
                return False, {"type": "via-vs-track", "net": net,
                               "at": [round(x, 2), round(y, 2)]}
        for net, rect in rects_by_layer[li]:
            if net == nc:
                continue
            if pt_pad_dist(x, y, rect) < _clear + via_r:
                return False, {"type": "via-vs-pad", "net": net,
                               "at": [round(x, 2), round(y, 2)]}
    return True, None


def candidate_polylines(a, b, spans=None, bounds=None):
    """Ordered candidate polylines from a=(x,y) to b=(x,y): the two L
    shapes, then Z shapes with perpendicular mid-offsets both ways
    (the manual_links/i2c1_reroute fold patterns), then BOARD-EDGE
    corridor detours (bounds=(x0,y0,x1,y1) — the proven pattern: the
    reference project's one manual fix routed exactly along the left
    board edge; the perimeter strip is the primary free-space resource
    on a crowded board). Pure geometry — the VERIFIER picks what
    survives."""
    (x1, y1), (x2, y2) = a, b
    spans = spans or [2.0, 4.0, 7.0, 11.0]
    out = []
    # L shapes
    out.append([(x1, y1), (x2, y1), (x2, y2)])
    out.append([(x1, y1), (x1, y2), (x2, y2)])
    # edge corridors: run to the perimeter strip, along it, back in
    if bounds:
        x0, y0, x1b, y1b = bounds
        for ex, ey in ((x0 + 0.8, None), (x1b - 0.8, None),
                       (None, y0 + 0.8), (None, y1b - 0.8)):
            if ex is not None:
                out.append([(x1, y1), (ex, y1), (ex, y2), (x2, y2)])
                out.append([(x1, y1), (ex, y1), (ex, y2), (x2, y2)])
            else:
                out.append([(x1, y1), (x1, ey), (x2, ey), (x2, y2)])
                out.append([(x1, y1), (x1, ey), (x2, ey), (x2, y2)])
    # Z shapes: midpoint offset perpendicular to a->b
    dx, dy = x2 - x1, y2 - y1
    L = math.hypot(dx, dy) or 1.0
    px, py = -dy / L, dx / L           # unit perpendicular
    for s in spans:
        for sgn in (1, -1):
            mx, my = (x1 + x2) / 2 + px * s * sgn, \
                (y1 + y2) / 2 + py * s * sgn
            out.append([(x1, y1), (mx, my), (x2, y2)])
    # staircases (two perpendicular jogs near each end — captures the
    # "slide along the free channel then cross" pattern of edge detours)
    for s in spans:
        for sgn in (1, -1):
            jx, jy = px * s * sgn, py * s * sgn
            out.append([(x1, y1), (x1 + jx, y1 + jy),
                        (x2 + jx, y2 + jy), (x2, y2)])
    return out


def slot_waypoints(a, b, segs_by_layer, rects_by_layer, nc, width):
    """Channel-midpoint candidates (the y=10.32 discovery, automated):
    free-slot centers are derived from FACING OBSTACLE EDGES — two edges
    with a gap >= 2*(clearance+own) leave a passable slot whose center
    is the natural waypoint. Axis-aligned slots both ways + corner
    combinations. This is how the expert loop found the 0.84mm seam
    between module pad columns; now the map finds it itself."""
    (x1, y1), (x2, y2) = a, b
    need = CLEAR + width / 2 + 0.02
    xs, ys = [], []
    for li in range(len(segs_by_layer)):
        for net, s, e, hw in segs_by_layer[li]:
            if net == nc:
                continue
            xs += [min(s[0], e[0]) - hw, max(s[0], e[0]) + hw]
            ys += [min(s[1], e[1]) - hw, max(s[1], e[1]) + hw]
        for net, rect in rects_by_layer[li]:
            if net == nc:
                continue
            cx, cy, hw2, hh2, _a = rect[:5]   # 6元组带圆标志
            xs += [cx - hw2, cx + hw2]
            ys += [cy - hh2, cy + hh2]

    def slots(edges, lo, hi):
        # SUPERSET of slot centers: midpoint of every consecutive edge
        # pair. Global gap math lies (edges at different x/z positions
        # fake-split a local seam) — emit more, let the exact verifier
        # be the single judge.
        out = []
        es = sorted(set(round(e, 3) for e in edges
                        if lo - 2.5 <= e <= hi + 2.5))
        out.append(lo - need - 0.1)
        out.append(hi + need + 0.1)
        for u, v in zip(es, es[1:]):
            out.append((u + v) / 2)
        return out

    out = []
    for sy in slots(ys, min(y1, y2), max(y1, y2)):
        out.append([(x1, y1), (x1, sy), (x2, sy), (x2, y2)])
    for sx in slots(xs, min(x1, x2), max(x1, x2)):
        out.append([(x1, y1), (sx, y1), (sx, y2), (x2, y2)])
    # corner combinations (slot x slot) — the expert 2-bend shapes
    sxs = slots(xs, min(x1, x2), max(x1, x2))[:6]
    sys_ = slots(ys, min(y1, y2), max(y1, y2))[:6]
    for sx in sxs:
        for sy in sys_:
            out.append([(x1, y1), (sx, y1), (sx, sy), (x2, sy), (x2, y2)])
    # FULL expert shape: west dodge + slot + east column (two x-slots) —
    # the y=10.32 discovery needed BOTH the x=22.x dodge past the module
    # pad column AND the x=33.x corridor for the final vertical
    for sx1 in sxs:
        for sx2 in sxs:
            if abs(sx1 - sx2) < 1.0:
                continue
            for sy in sys_:
                out.append([(x1, y1), (sx1, y1), (sx1, sy),
                            (sx2, sy), (sx2, y2), (x2, y2)])
    return out


def verify_polyline(pts, width, nc, segs, rects, rules=None):
    """World-geometry verification of one polyline against fixed copper
    lists (same rules as commit_gate — the single geometric truth).

    segs: [(net, (x1,y1), (x2,y2), half_w)]   rects: [(net, rect)]
    Returns (ok, blocker) where blocker describes the first violation
    for the fix-it feedback ("violated: X, by how much").
    """
    from routegrid import seg_seg_dist, pt_rect_dist
    # T2: rule values come from the ONE table when provided (module
    # constants remain the pure-test defaults)
    _clear = (rules or {}).get("clearance", CLEAR)
    _smd = (rules or {}).get("clearance_smd", CLEAR_SMD)
    own = width / 2
    for (p, q) in zip(pts, pts[1:]):
        pad = own + CLEAR + 0.05
        x0, x1 = min(p[0], q[0]) - pad, max(p[0], q[0]) + pad
        y0, y1 = min(p[1], q[1]) - pad, max(p[1], q[1]) + pad
        for net, s, e, hw in segs:
            if net == nc:
                continue
            if seg_seg_dist(p, q, s, e) < _clear + hw + own:
                return False, {"type": "track", "net": net,
                               "at": [round((s[0] + e[0]) / 2, 2),
                                      round((s[1] + e[1]) / 2, 2)],
                               "short_by_mm": round(
                                   _clear + hw + own -
                                   seg_seg_dist(p, q, s, e), 3)}
        for net, rect in rects:
            if net == nc:
                continue
            n = max(2, int(math.hypot(q[0] - p[0], q[1] - p[1]) / 0.1))
            for k in range(n + 1):
                t = k / n
                x, y = p[0] + (q[0] - p[0]) * t, p[1] + (q[1] - p[1]) * t
                d = pt_pad_dist(x, y, rect)
                if d < _smd + own:
                    return False, {"type": "pad", "net": net,
                                   "at": [round(rect[0], 2),
                                          round(rect[1], 2)],
                                   "short_by_mm": round(
                                       _smd + own - d, 3)}
    return True, None


def best_paths(a, b, width, nc, segs, rects, limit=3, bounds=None):
    """Verified candidate paths sorted by length — the actionable part
    of the fix-it: each carries its pts (the decision is a PATH)."""
    verified, rejects = [], []
    for cand in candidate_polylines(a, b, bounds=bounds):
        ok, blocker = verify_polyline(cand, width, nc, segs, rects)
        if ok:
            length = sum(math.dist(p, q)
                         for p, q in zip(cand, cand[1:]))
            verified.append({"pts": [[round(x, 3), round(y, 3)]
                                     for x, y in cand],
                             "length_mm": round(length, 2)})
        elif len(rejects) < 3:
            rejects.append({"pts": [[round(x, 3), round(y, 3)]
                                    for x, y in cand],
                            "blocked_by": blocker})
    verified.sort(key=lambda v: v["length_mm"])
    return verified[:limit], rejects[:3]


def summarize(net, islands, plans, rejects):
    """The one-paragraph map digest the decider reads (point 4's final
    form: failure info = map + executable path candidates)."""
    parts = [f"{net}: {len(islands)}岛"]
    for i, isl in enumerate(islands):
        parts.append(f"岛{i + 1}({isl['anchor'][0]:.2f},{isl['anchor'][1]:.2f}"
                     f",{len(isl['pads'])}pad)")
    if plans:
        p0 = plans[0]
        pts = "->".join(f"({p[0]:.1f},{p[1]:.1f},L{p[2] if len(p) > 2 else 0})"
                        for p in p0["pts"])
        parts.append(f"可行路径{len(plans)}条,最短{p0['length_mm']}mm: {pts}")
    elif rejects:
        bb = rejects[0].get("blocked_by") or {}
        parts.append(f"全部候选被挡: {bb.get('net','?')}@{bb.get('at')} "
                     f"差{bb.get('short_by_mm')}mm")
    return "; ".join(parts)


def render_plan(plan, out_path, window=None):
    """Neighborhood SVG: islands green, obstacles gray, verified paths
    blue, rejected best red — the map the LLM looks at."""
    w_px = 420
    if window is None:
        allpts = [tuple(p[:2]) for isl in plan["islands"]
                  for p in isl["pads"]]
        for pl in plan["paths"]:
            allpts += [tuple(p[:2]) for p in pl["pts"]]
        for r in plan.get("rejects", []):
            allpts += [tuple(p) for p in r["pts"]]
        if not allpts:
            allpts = [(0, 0), (1, 1)]
        x0 = min(p[0] for p in allpts) - 3
        x1 = max(p[0] for p in allpts) + 3
        y0 = min(p[1] for p in allpts) - 3
        y1 = max(p[1] for p in allpts) + 3
    else:
        x0, y0, x1, y1 = window
    span = max(x1 - x0, y1 - y0) or 1
    scale = w_px / span
    h_px = int((y1 - y0) * scale) + 30

    def px(x):
        return round((x - x0) * scale + 10, 1)

    def py(y):
        return round((y - y0) * scale + 10, 1)
    s = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{w_px + 20}" '
         f'height="{h_px}" font-family="monospace" font-size="10">']
    s.append(f'<rect x="0" y="0" width="{w_px + 20}" height="{h_px}" '
             f'fill="white"/>')
    s.append(f'<text x="8" y="14">{plan["net"]} — '
             f'{plan["summary"][:110]}</text>')
    for (net, (ax, ay), (bx, by), _hw) in plan.get("segs", []):
        s.append(f'<line x1="{px(ax)}" y1="{py(ay)}" x2="{px(bx)}" '
                 f'y2="{py(by)}" stroke="#bbb" stroke-width="2"/>')
    for (net, rect) in plan.get("rects", []):
        cx, cy, hw, hh, _a = rect[:5]     # 6元组带圆标志
        s.append(f'<rect x="{px(cx - hw)}" y="{py(cy - hh)}" '
                 f'width="{hw * 2 * scale:.1f}" height="{hh * 2 * scale:.1f}" '
                 f'fill="#ddd" stroke="#999"/>')
    for isl in plan["islands"]:
        for (x, y) in isl["pads"]:
            s.append(f'<circle cx="{px(x)}" cy="{py(y)}" r="4" '
                     f'fill="#2a2" stroke="none" opacity="0.85"/>')
    for pl in plan["paths"]:
        d = " ".join(f"{px(x)},{py(y)}" for x, y in pl["pts"])
        s.append(f'<polyline points="{d}" fill="none" stroke="#26c" '
                 f'stroke-width="2.5"/>')
    for r in plan.get("rejects", []):
        d = " ".join(f"{px(x)},{py(y)}" for x, y in r["pts"])
        s.append(f'<polyline points="{d}" fill="none" stroke="#c22" '
                 f'stroke-width="1.5" stroke-dasharray="4,3"/>')
    s.append("</svg>")
    Path(out_path).write_text("\n".join(s), encoding="utf-8")
    return str(out_path)




WIDTH_LADDER_DEFAULT = (0.2, 0.15, 0.127, 0.11)


def plan_paths(a, b, width, nc, segs_by_layer, rects_by_layer,
               bounds=None, limit=4, width_ladder=None, rules=None,
               planes_by_layer=None):
    """Layer-resolved path search — THE single-net repair shape
    (i2c1_reroute: F.Cu -> via -> B.Cu -> via -> F.Cu):
      * plain polylines on EACH layer (a layer-0 path only fights
        layer-0 obstacles — dodges congestion on the other side)
      * via-detour candidates: hop layers along the line where an
        F.Cu track intercepts (the y=14.29 crossing pattern)
      * CHANNEL-MIDPOINT slots derived from facing obstacle edges —
        the map discovers seams like the expert's y=10.32 on its own
      * WIDTH CASCADE: seam feasibility is width-dependent (a 0.52mm
        gap fits 0.11 but not 0.2) — the expert loop's 0.2->0.15->0.11
        iteration runs automatically, widest-first.
    Returns paths with pts as (x, y, layer) + width — direct_link's
    spec shape.
    """
    verified, rejects = [], []
    nlay = len(segs_by_layer)
    ladder = width_ladder or WIDTH_LADDER_DEFAULT
    for width in (width,) + tuple(w for w in ladder if w < width):
        all_cands = (candidate_polylines(a, b, bounds=bounds)
                     + slot_waypoints(a, b, segs_by_layer, rects_by_layer,
                                      nc, width))
        _plan_paths_at_width(a, b, width, nc, segs_by_layer,
                             rects_by_layer, nlay, all_cands,
                             verified, rejects, limit, rules)
        if len(verified) >= limit:
            break
    if not verified and rejects:
        # stream ②: the best near-miss gets perturbed and retried —
        # a candidate missing by 0.015mm is a false negative, not a wall
        verified.extend(near_miss_retry(a, b, width, nc, segs_by_layer,
                                        rects_by_layer, rejects,
                                        rules=rules))
    # ② 平面完整性优先: 跨平面数是第一排序键(切 GND 平面 = 制造
    # power-fragment 开路), 长度次之
    if planes_by_layer:
        for v in verified:
            v["plane_cut"] = plane_cut_count(v["pts"], planes_by_layer)
        verified.sort(key=lambda v: (v["plane_cut"], v["length_mm"],
                                     -v.get("width", 0.2)))
    else:
        verified.sort(key=lambda v: (v["length_mm"],
                                     -v.get("width", 0.2)))
    return verified[:limit], rejects[:4]


def _plan_paths_at_width(a, b, width, nc, segs_by_layer, rects_by_layer,
                         nlay, all_cands, verified, rejects, limit,
                         rules=None):
    for li in range(nlay):
        for cand0 in all_cands:
            if len(verified) >= limit:
                break       # 满额早停(修复后全搜索成本暴涨的直接来源)
            # "验你所发": 出库 pts 是 round(x,3) —— 验证必须对圆整后
            # 坐标做, 否则 plan 过、execute 拒(USART6 3μm 边际拒案)
            cand = [(round(x, 3), round(y, 3)) for x, y in cand0]
            bad_seg = None
            for (p, q) in zip(cand, cand[1:]):
                ok, blocker = verify_polyline(
                    [(p[0], p[1]), (q[0], q[1])], width, nc,
                    segs_by_layer[li], rects_by_layer[li], rules)
                if not ok:
                    bad_seg = (p, q, blocker)
                    break
            if bad_seg is None:
                length = sum(math.dist(p, q)
                             for p, q in zip(cand, cand[1:]))
                verified.append({"pts": [[round(x, 3), round(y, 3), li]
                                         for x, y in cand],
                                 "length_mm": round(length, 2),
                                 "width": width,
                                 "mode": f"layer{li}"})
                continue
            p, q, blocker = bad_seg
            # CROSSING REPAIR: a cornered polyline blocked on ONE segment
            # = a track intercepting the slot; hop that segment to the
            # quiet layer with vias at the corners (the expert's y=14.29
            # F-via-B-via-F crossing, automated)
            if nlay >= 2:
                quiet = 1 - li
                ok2, blk2 = verify_polyline(
                    [(p[0], p[1]), (q[0], q[1])], width, nc,
                    segs_by_layer[quiet], rects_by_layer[quiet], rules)
                # T1: the vias' barrels cross ALL layers — verify them
                # in 3D before accepting the hop (the 10-shorts bug)
                v_ok = (via_ok(p[0], p[1], nc, segs_by_layer,
                               rects_by_layer, rules=rules)[0]
                        and via_ok(q[0], q[1], nc, segs_by_layer,
                                   rects_by_layer, rules=rules)[0])
                if ok2 and v_ok:
                    out = []
                    for pt in cand:
                        out.append([round(pt[0], 3), round(pt[1], 3), li])
                    # replace the blocked segment with the quiet hop:
                    # duplicate its endpoints as via pairs (li->quiet,
                    # run, quiet->li)
                    i0 = next((i for i, pt in enumerate(out)
                               if abs(pt[0] - round(p[0], 3)) < 1e-6
                               and abs(pt[1] - round(p[1], 3)) < 1e-6),
                              None)
                    i1 = next((i for i, pt in enumerate(out)
                               if abs(pt[0] - round(q[0], 3)) < 1e-6
                               and abs(pt[1] - round(q[1], 3)) < 1e-6),
                              None)
                    if i0 is not None and i1 is not None and i1 == i0 + 1:
                        quiet_i = quiet % nlay    # 负索引规范化(li≥2 时
                                                  # 1-li 为负, 验证靠回绕
                                                  # 一致但绘制端会错层)
                        seg = [out[i0], [out[i0][0], out[i0][1], quiet_i],
                               [out[i1][0], out[i1][1], quiet_i], out[i1]]
                        out = out[:i0] + seg + out[i1 + 1:]
                        # hop 修复只验了被挡段+过孔 —— **其余段必须全量
                        # 重验**(USART6 拒案: 验证在第一段 break, 尾巴
                        # 未验就被发出, reverify 以 0.003 差拒之)
                        tail_ok = True
                        for (u, v2) in zip(out, out[1:]):
                            if u[2] != v2[2]:
                                continue    # 层跳由 v_ok 覆盖
                            if not verify_polyline(
                                    [(u[0], u[1]), (v2[0], v2[1])], width,
                                    nc, segs_by_layer[u[2]],
                                    rects_by_layer[u[2]], rules)[0]:
                                tail_ok = False
                                break
                        if not tail_ok:
                            # 整条弃: 记 reject 后继续下一候选
                            if len(rejects) < 3:
                                rejects.append({
                                    "pts": [[round(x, 3), round(y, 3), li]
                                            for x, y in cand],
                                    "blocked_by": blocker})
                            continue
                        length = sum(
                            math.dist(x[:2], y[:2])
                            for x, y in zip(out, out[1:]))
                        verified.append({
                            "pts": out,
                            "length_mm": round(length, 2),
                            "width": width,
                            "mode": f"hop-repair-L{li}->{quiet}"})
                        continue
            if len(rejects) < 3:
                rejects.append({"pts": [[round(x, 3), round(y, 3), li]
                                        for x, y in cand],
                                "blocked_by": blocker})
    # via-detour: F(0) -> via -> quiet layer -> via -> F(0). Via sites
    # sampled ALONG the direct line (the crossing-track pattern: hop to
    # B.Cu exactly where an F.Cu track intercepts — the expert's
    # y=14.29 crossing is now found by sampling, not hand-picked)
    if nlay >= 2:
        import math as _m
        L = _m.hypot(b[0] - a[0], b[1] - a[1]) or 1.0
        px, py = -(b[1] - a[1]) / L, (b[0] - a[0]) / L
        ts = [0.25, 0.4, 0.55, 0.7]
        perps = (0.0, 0.4, -0.4, 0.8, -0.8)
        sites = []
        for t in ts:
            for off in perps:
                sites.append((a[0] + (b[0] - a[0]) * t + px * off,
                              a[1] + (b[1] - a[1]) * t + py * off))
        for quiet in range(1, nlay):
            for va in sites:
                if not via_ok(va[0], va[1], nc, segs_by_layer,
                              rects_by_layer, rules=rules)[0]:
                    continue
                for vb in sites:
                    if abs(va[0] - vb[0]) + abs(va[1] - vb[1]) < 1.5:
                        continue
                    if not via_ok(vb[0], vb[1], nc, segs_by_layer,
                                  rects_by_layer, rules=rules)[0]:
                        continue
                    if len(verified) >= 3:
                        break
                    cand = [(a[0], a[1], 0), (va[0], va[1], 0),
                            (va[0], va[1], quiet), (vb[0], vb[1], quiet),
                            (vb[0], vb[1], 0), (b[0], b[1], 0)]
                    bad = None
                    for (p, q) in zip(cand, cand[1:]):
                        if p[2] != q[2]:
                            continue          # via hop: checked below
                        li = p[2]
                        ok, blocker = verify_polyline(
                            [(p[0], p[1]), (q[0], q[1])], width, nc,
                            segs_by_layer[li], rects_by_layer[li])
                        if not ok:
                            bad = blocker
                            break
                    if bad is None:
                        length = sum(
                            math.dist((p[0], p[1]), (q[0], q[1]))
                            for p, q in zip(cand, cand[1:]))
                        verified.append({
                            "pts": [[round(x, 3), round(y, 3), li]
                                    for x, y, li in cand],
                            "length_mm": round(length, 2),
                            "width": width,
                            "mode": f"via-detour-L{quiet}"})
                    elif len(rejects) < 4:
                        rejects.append({"pts": [], "blocked_by": bad})
    verified.sort(key=lambda v: v["length_mm"])
    return verified[:limit], rejects[:4]


# ==================================================== board adapter layer
def extract_objects(board, layer_ids):
    import pcbnew
    """Plain-geometry extraction, PER LAYER (consumed by verify_polyline
    — the same lists commit_gate feeds, i.e. ONE geometric truth).

    Returns (segs_by_layer, rects_by_layer): each is a list indexed by
    router-layer with (net, geom, ...) tuples — layer-resolved so a
    path on B.Cu only fights B.Cu obstacles (the i2c1_reroute F-via-B
    pattern is how a single stubborn net dodges congestion)."""
    import routegrid as rg
    segs = [[] for _ in layer_ids]
    rects = [[] for _ in layer_ids]
    MM = 1000000
    for fp in board.GetFootprints():
        for pad in fp.Pads():
            try:
                nc = pad.GetNetCode()
                cx, cy, hw, hh, ang = rg.pad_rect(pad)
                # 形状标志: 真圆焊盘(BGA 球)走圆距离; 方形焊盘保持
                # 矩形语义(圆化会放松方角 -> 布线穿角)
                try:
                    _rnd = (int(pad.GetShape()) ==
                            int(pcbnew.PAD_SHAPE_CIRCLE))
                except (AttributeError, TypeError):
                    _rnd = False
                for li, lid in enumerate(layer_ids):
                    if pad.IsOnLayer(lid):
                        rects[li].append(
                            (nc, (cx, cy, hw, hh, ang, _rnd)))
            except (AttributeError, TypeError):
                continue    # ok-silent: 畸形焊盘逐个跳过
    for it in board.GetTracks():
        try:
            nc = it.GetNetCode()
            if it.Type() == pcbnew_via_t(board):
                q = it.GetPosition()
                x, y = q.x / MM, q.y / MM
                r = it.GetWidth(0) / MM / 2
                for li in range(len(layer_ids)):
                    segs[li].append((nc, (x, y), (x, y), r))
            else:
                lid = it.GetLayer()
                if lid not in layer_ids:
                    continue
                li = layer_ids.index(lid)
                s, e = it.GetStart(), it.GetEnd()
                w = it.GetWidth() / MM
                segs[li].append((nc, (s.x / MM, s.y / MM),
                                 (e.x / MM, e.y / MM), w / 2))
        except (AttributeError, TypeError):
            continue    # ok-silent: 畸形走线逐个跳过
    return segs, rects


def pcbnew_via_t(board):
    import pcbnew
    return pcbnew.PCB_VIA_T


def pairs_by_net(pairs_lines):
    """conn_audit pairs 行 'net|x|y|l|x|y|l' -> {net: [((x,y),(x,y)),...]}
    —— ④ 规划端点单源: 直接用测量器报的断点对, 不再用岛质心。"""
    out = {}
    for ln in pairs_lines or []:
        parts = str(ln).split("|")
        if len(parts) < 7:
            continue
        net = parts[0]
        # 格式 net|x|y|layer|x|y|layer — layer 在 [3]/[6]
        a = (round(float(parts[1]), 3), round(float(parts[2]), 3))
        b = (round(float(parts[4]), 3), round(float(parts[5]), 3))
        out.setdefault(net, []).append((a, b))
    return out


def select_nets(pairs_lines, fragmented=None, k=8):
    """④ 选网单源: 开路对所属网按碎片化严重度降序(岛数多者先),
    平局字母序 —— 旧版纯字母序把严重度信息丢在选网处。"""
    fragmented = fragmented or {}
    nets = {str(ln).split("|")[0] for ln in (pairs_lines or [])
            if str(ln).strip()}
    return sorted(nets,
                  key=lambda n: (-(int(fragmented.get(n, 0))), n))[:k]


def group_pads_by_cluster(pads, connected_of, key_of):
    """④ 单源岛划分: 焊盘按 KiCad 连通簇分组 —— 两个焊盘同岛当且仅当
    同一 GetConnectedItems 簇(zone 连通的焊盘在彼此簇里直现)。
    键 = key_of(pad)(位置+层), 只有焊盘参与 union —— 旧版三个 bug
    (走线只入 START 死分支 / zone 轮廓点当连接键 / 无层键叠放误合并)
    在簇语义下全部不存在: 簇本身就是 KiCad 的连通真值。
    pads: [(pad, key)];  connected_of(pad) -> iterable of pads;
    纯函数, 假 conn 可测。"""
    class UF:
        def __init__(self):
            self.p = {}

        def find(self, x):
            self.p.setdefault(x, x)
            while self.p[x] != x:
                self.p[x] = self.p[self.p[x]]
                x = self.p[x]
            return x

        def union(self, a, b):
            ra, rb = self.find(a), self.find(b)
            if ra != rb:
                self.p[ra] = rb

    by_key = {k: k for (_p, k) in pads}
    uf = UF()
    for _p, k in pads:
        uf.find(k)
    for pad, k in pads:
        for nb in connected_of(pad):
            nk = key_of(nb)
            if nk in by_key:
                uf.union(k, nk)
    groups = {}
    for pad, k in pads:
        groups.setdefault(uf.find(k), []).append(pad)
    return list(groups.values())


def islands_of(board, nc):
    """Islands of a net — ④ 单源: 与 conn_audit/ratsnest 同一簇语义
    (group_pads_by_cluster)。输出形状保持 {pads/anchor/bbox} 兼容。"""
    class UF:
        def __init__(self):
            self.p = {}

        def find(self, x):
            self.p.setdefault(x, x)
            while self.p[x] != x:
                self.p[x] = self.p[self.p[x]]
                x = self.p[x]
            return x

        def union(self, a, b):
            ra, rb = self.find(a), self.find(b)
            if ra != rb:
                self.p[ra] = rb

    MM = 1000000
    import pcbnew
    board.BuildConnectivity()
    conn = board.GetConnectivity()

    def _key(it):
        # 位置+层集合: 叠放焊盘(同XY异层)必须是不同键(旧 bug 的根源)
        q = it.GetPosition()
        try:
            lays = tuple(sorted(int(ly) for ly in it.GetLayerSet().Seq()))
        except AttributeError:
            try:
                lays = tuple(sorted(
                    ly for ly in (pcbnew.F_Cu, pcbnew.B_Cu)
                    if it.IsOnLayer(ly)))
            except AttributeError:
                lays = ()
        return (q.x, q.y, lays)

    pads = []
    for fp in board.GetFootprints():
        for pad in fp.Pads():
            if pad.GetNetCode() != nc:
                continue
            pads.append((pad, _key(pad)))

    def _cluster(pad):
        try:
            return [it for it in conn.GetConnectedItems(pad)
                    if getattr(it, "GetNetCode", lambda: 0)() == nc]
        except (AttributeError, TypeError):
            return []    # ok-silent: 畸形对象逐个跳过(单对象不拖垮提取)

    groups = group_pads_by_cluster(pads, _cluster, _key)
    islands = []
    for grp in groups:
        pts = [(_p.GetPosition().x / MM, _p.GetPosition().y / MM)
               for _p in grp]
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        islands.append({
            "pads": [[round(x, 3), round(y, 3)] for x, y in pts],
            "anchor": [round(sum(xs) / len(xs), 3),
                       round(sum(ys) / len(ys), 3)],
            "bbox": [round(min(xs), 2), round(min(ys), 2),
                     round(max(xs), 2), round(max(ys), 2)],
        })
    islands.sort(key=lambda i: -len(i["pads"]))
    return islands


def near_miss_retry(a, b, width, nc, segs_by_layer, rects_by_layer,
                    rejects, rules=None, n_shifts=8):
    """Stream ② — the false-negative killer (hyuhang: 'why does it say
    unroutable when space exists?'): the best rejected candidate missed
    by a hair (0.015-0.3mm) — shift the polyline PERPENDICULARLY AWAY
    from its blocker and retry. This recovers the seams that candidate
    generation happens to miss by one width of margin."""
    from routegrid import seg_seg_dist
    if not rejects:
        return []
    best = min(rejects,
               key=lambda r: (r.get("blocked_by") or {}).get(
                   "short_by_mm", 9))
    bl = best.get("blocked_by") or {}
    pts = best.get("pts") or []
    at = bl.get("at")
    if not pts or not at:
        return []
    # shift axis: perpendicular to the failing segment, away from the
    # blocker point
    (x1, y1), (x2, y2) = tuple(pts[0][:2]), tuple(pts[-1][:2])
    dx, dy = x2 - x1, y2 - y1
    L = math.hypot(dx, dy) or 1.0
    px, py = -dy / L, dx / L
    bx, by = at
    # sign: away from the blocker
    if (bx - x1) * px + (by - y1) * py > 0:
        px, py = -px, -py
    out = []
    for k in range(1, n_shifts + 1):
        off = k * 0.15
        # 验你所发: 圆整后再验(出库即 round(x,3))
        cand = [(round(x + px * off, 3), round(y + py * off, 3))
                for (x, y) in [tuple(pt[:2]) for pt in pts]]
        ok = True
        for (p, q) in zip(cand, cand[1:]):
            li = pts[0][2] if len(pts[0]) > 2 else 0
            v, _bl = verify_polyline([p, q], width, nc,
                                     segs_by_layer[li],
                                     rects_by_layer[li], rules)
            if not v:
                ok = False
                break
        if ok:
            li = pts[0][2] if len(pts[0]) > 2 else 0
            out.append({"pts": [[round(x, 3), round(y, 3), li]
                                for x, y in cand],
                        "length_mm": round(sum(
                            math.dist(p, q)
                            for p, q in zip(cand, cand[1:])), 2),
                        "width": width,
                        "mode": f"near-miss-shift-{off:.2f}"})
            if len(out) >= 2:
                break
    return out


def extract_planes(board, layer_ids):
    """② 平面掩膜: 每层的外来网 zone 填充多边形(顶点列, 每多边形
    截断 256 顶点) — 地图此前完全看不见 zone, 38mm 直连把 GND 平面
    切碎(power-fragment)而规划器毫无感知。planes_by_layer[li] =
    [(nc, [(x,y),...]), ...]。"""
    MM = 1000000.0
    out = [[] for _ in layer_ids]
    try:
        zones = board.Zones()
    except Exception:
        return out
    for z in zones:
        nc = z.GetNetCode()
        if not nc:
            continue        # keepout/无网 zone 不是平面
        for li, lid in enumerate(layer_ids):
            if not z.IsOnLayer(lid):
                continue
            try:
                ps = z.GetFilledPolysList(lid)
                for oi in range(ps.OutlineCount()):
                    ol = ps.Outline(oi)
                    pts = []
                    for pi in range(min(ol.PointCount(), 256)):
                        cp = ol.CPoint(pi)
                        pts.append((cp.x / MM, cp.y / MM))
                    if len(pts) >= 3:
                        out[li].append((nc, pts))
            except Exception:
                pass    # ok-silent: 单 zone 提取失败不拖垮规划(代价
                        # 缺失退回纯长度排序, DRC 终审仍在)
    return out


def plane_cut_count(pts, planes_by_layer):
    """路径跨平面数: 线段与外来网 zone 多边形相交计数(代价项)."""
    def _seg_poly_hit(p, q, poly):
        n = len(poly)
        for (x, y) in (p, q):
            inside = False
            for i in range(n):
                x1, y1 = poly[i]
                x2, y2 = poly[(i + 1) % n]
                if (y1 > y) != (y2 > y) and \
                        x < (x2 - x1) * (y - y1) / (y2 - y1 + 1e-12) + x1:
                    inside = not inside
            if inside:
                return True
        for i in range(n):
            x1, y1 = poly[i]
            x2, y2 = poly[(i + 1) % n]
            d = ((x2 - x1) * (p[1] - y1) - (y2 - y1) * (p[0] - x1))
            e = ((x2 - x1) * (q[1] - y1) - (y2 - y1) * (q[0] - x1))
            f = ((q[0] - p[0]) * (y1 - p[1]) - (q[1] - p[1]) * (x1 - p[0]))
            g = ((q[0] - p[0]) * (y2 - p[1]) - (q[1] - p[1]) * (x2 - p[0]))
            if (d * e < 0) and (f * g < 0):
                return True
        return False

    cuts = 0
    for (p, q) in zip(pts, pts[1:]):
        li = p[2] if len(p) > 2 else 0
        cross_layer = (len(p) > 2 and len(q) > 2 and p[2] != q[2])
        if cross_layer:
            continue        # 跨层段(过孔)不切平面
        p2 = (p[0], p[1])
        q2 = (q[0], q[1])
        for (nc, poly) in (planes_by_layer[li]
                           if li < len(planes_by_layer) else []):
            if _seg_poly_hit(p2, q2, poly):
                cuts += 1
    return cuts


def pt_pad_dist(x, y, rect):
    """点到焊盘距离 — **真圆焊盘**(extract_objects 的形状标志)按圆
    算: BGA 球是圆的, 矩形近似把对角净空从 0.366 压到 0.283,
    0.8mm 间距的狗骨过孔位被自己的模型挡死(实测归因)。
    其余焊盘保持矩形语义 — 圆化方形焊盘会放松方角(布线穿角)。"""
    cx, cy, w, h = rect[0], rect[1], rect[2], rect[3]
    rot = rect[4] if len(rect) > 4 else 0.0
    is_round = len(rect) > 5 and bool(rect[5])
    if is_round and not rot:
        # 元组第 3 位是**半维**(pad_rect 返回 hw/hh) — 半径就是 w。
        # 曾用 w/2: 球距 0.2557 被算成 0.356 假通过(FC_L7 验尸)
        return max(0.0, math.hypot(x - cx, y - cy) - w)
    from routegrid import pt_rect_dist
    return pt_rect_dist(x, y, rect)


def _seg_rect_dist(a, b, rect):
    """segment-to-axis-aligned-rect outline distance (0 if crossing).
    rect = (cx, cy, w, h, rot); rot ignored in the proxy (the DRC
    terminal authority handles rotated pads — this is a fast oracle)."""
    cx, cy, w, h = rect[0], rect[1], rect[2], rect[3]
    x0, x1 = cx - w / 2, cx + w / 2
    y0, y1 = cy - h / 2, cy + h / 2
    corners = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    from routegrid import seg_seg_dist as _ssd
    return min(_ssd(a, b, corners[k], corners[(k + 1) % 4])
               for k in range(4))


def _rect_rect_overlap(r1, r2):
    """axis-aligned pad-pad overlap (rot ignored — the proxy's honest
    approximation; rotated cases fall to the DRC authority)."""
    x1, y1, w1, h1 = r1[0], r1[1], r1[2], r1[3]
    x2, y2, w2, h2 = r2[0], r2[1], r2[2], r2[3]
    return (abs(x1 - x2) < (w1 + w2) / 2
            and abs(y1 - y2) < (h1 + h2) / 2)


def count_overlaps_geom(segs, rects):
    """Pure-geometry cross-net overlap counter (the stream ③ oracle).
    THRESHOLD contract: overlap means copper edges INTERPENETRATE —
    centerline distance < half-width sum. The first version compared
    against FULL width sums and counted legal near-pairs as shorts
    (a false-positive factory).
    COVERAGE contract: seg-seg + seg-rect + rect-rect. The FC_L4
    escape (jumper placed a pad ONTO a foreign pad, committed at
    delta 0/0) was a pad-pad short the v1 coverage never counted —
    any copper class the oracle cannot see is a gate blind spot."""
    from routegrid import seg_seg_dist
    n = 0
    for li in range(len(segs)):
        s_list = segs[li]
        r_list = rects[li]
        for i in range(len(s_list)):
            net1, a1, b1, w1 = s_list[i]
            for j in range(i + 1, len(s_list)):
                net2, a2, b2, w2 = s_list[j]
                if net1 == net2 or not net1 or not net2:
                    continue
                if seg_seg_dist(a1, b1, a2, b2) < (w1 + w2) / 2:
                    n += 1
                    if n > 500:
                        return n
        for (net1, a1, b1, w1) in s_list:
            for (net2, rect) in r_list:
                if net1 == net2 or not net1 or not net2:
                    continue
                if _seg_rect_dist(a1, b1, rect) < w1 / 2:
                    n += 1
                    if n > 500:
                        return n
        for i in range(len(r_list)):
            net1, r1 = r_list[i]
            for j in range(i + 1, len(r_list)):
                net2, r2 = r_list[j]
                if net1 == net2 or not net1 or not net2:
                    continue
                if _rect_rect_overlap(r1, r2):
                    n += 1
                    if n > 500:
                        return n
    return n


def count_overlaps(board, layer_ids):
    """Stream ③ cheap-oracle proxy for SHORTS (pcbfork two-tier):
    pairwise cross-net geometric overlaps — O(n^2) but n~500 in C-speed
    tuple math; the strict gate can use this instead of a 60s DRC, with
    the full DRC as the terminal authority at loop end."""
    segs, rects = extract_objects(board, layer_ids)
    return count_overlaps_geom(segs, rects)


_PLAN_CACHE = {}
_EXTRACT_CACHE = {}


def plan_net(board, net_name, width=0.2, board_sha=None, pairs=None):
    # stream ③: cache plans keyed by board state + net — unchanged
    # board means unchanged geometry, so the 20s-per-net plan cost
    # vanishes for repeated queries in one loop iteration
    key = (board_sha or "", net_name, width)
    if key in _PLAN_CACHE:
        return _PLAN_CACHE[key]
    """THE decision package for one stubborn net: islands + verified
    paths between island anchors + what blocks the rest + summary."""
    net = board.FindNet(net_name)
    if net is None:
        return {"net": net_name, "error": "net not found"}
    nc = net.GetNetCode()
    import pcbnew
    lids = [pcbnew.F_Cu, pcbnew.B_Cu]
    _layer_fb = False
    try:
        import routegrid as rg
        # 提取全铜层(验证/经由校验的契约视图); 候选仍按层生成
        lids = rg.all_copper_ids(board)
    except Exception:
        # 多层板上静默降级到两层=漏检内层障碍 — 必须带标记
        _layer_fb = True
    # fc 泛化关键: 全板提取只做一次(逐网重扫是 fc 上批量出图
    # 超时的根因 — 8 网 = 8 次全板扫描). 单进程内板不变, 缓存安全;
    # 跨进程天然失效(板文件变 -> sha 变).
    global _EXTRACT_CACHE
    _ex_key = (board_sha or id(board), tuple(lids))
    _ex = _EXTRACT_CACHE.get(_ex_key)
    if _ex is not None:
        segs, rects = _ex
    else:
        segs, rects = extract_objects(board, lids)
        _EXTRACT_CACHE[_ex_key] = (segs, rects)
    # T2: the ONE rule table (provenance-checked; fallbacks surfaced)
    try:
        import sys as _s2
        _s2.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from kernel import rules as _kr
        rule_table = (_kr.load_rules(Path(_s2.argv[1]))
                      if len(_s2.argv) > 1
                      and __import__("os").environ.get(
                          "FLUXNT_V_RULES", "1") != "0"
                      else None)
    except Exception:
        rule_table = None
    islands = islands_of(board, nc)
    paths, rejects = [], []
    try:
        bb = board.GetBoardEdgesBoundingBox()
        MM = 1000000
        bounds = (bb.GetX() / MM, bb.GetY() / MM,
                  (bb.GetX() + bb.GetWidth()) / MM,
                  (bb.GetY() + bb.GetHeight()) / MM)
    except Exception:
        bounds = None
    # ④ 规划端点单源: pairs(测量器报的断点对)优先, 岛锚点兜底
    _planes = extract_planes(board, lids)
    if pairs:
        for pi, (pa, pb) in enumerate(pairs):
            ok, rej = plan_paths(pa, pb, width, nc, segs, rects,
                                 bounds=bounds, rules=rule_table,
                                 planes_by_layer=_planes)
            for p in ok:
                p["between"] = ["pair", pi]
            paths += ok
            rejects += rej
    elif len(islands) >= 2:
        for i in range(1, len(islands)):
            a = tuple(islands[0]["anchor"])
            b = tuple(islands[i]["anchor"])
            ok, rej = plan_paths(a, b, width, nc, segs, rects,
                                 bounds=bounds, rules=rule_table,
                                 planes_by_layer=_planes)
            for p in ok:
                p["between"] = [0, i]
            paths += ok
            rejects += rej
    # neighborhood window obstacles (the map around the failure)
    flat_segs = [x for li_ in segs for x in li_]
    flat_rects = [x for li_ in rects for x in li_]
    win_segs = [s for s in flat_segs if s[0] != nc][:400]
    win_rects = [r for r in flat_rects if r[0] != nc][:400]
    # blocker netcodes -> names (the decider needs to ACT on blockers:
    # the reference pattern is "reroute the blocker away, then link")
    nc2name = {}
    try:
        for _nc, _ni in board.GetNetInfo().NetsByNetcode().items():
            nc2name[int(_nc)] = _ni.GetNetname()
    except Exception:
        pass    # ok-silent: 名字表尽力而为(缺名回落netcode)
    for r in rejects:
        bl = r.get("blocked_by")
        if bl and isinstance(bl.get("net"), int):
            bl["net_name"] = nc2name.get(bl["net"], str(bl["net"]))
    summary = summarize(net_name, islands, paths, rejects)
    result = {"net": net_name, "netcode": nc, "islands": islands,
            "layer_fallback": _layer_fb,
            "paths": paths, "rejects": rejects,
            "blockers": sorted({r["blocked_by"].get("net_name")
                                for r in rejects
                                if r.get("blocked_by")}
                               - {net_name, "0", ""}),
            "segs": win_segs, "rects": win_rects,
            "summary": summary}
    _PLAN_CACHE[key] = result
    return result


# ==================================================== CLI
if __name__ == "__main__":
    import sys
    import pcbnew
    pcb, nets_arg = sys.argv[1], sys.argv[2]
    out_prefix = sys.argv[3] if len(sys.argv) > 3 else pcb
    pairs_file = Path(sys.argv[4]) if len(sys.argv) > 4 else None
    b = pcbnew.LoadBoard(pcb)
    if nets_arg == "__overlaps__":
        import routegrid as _rg
        lids = _rg.all_copper_ids(b)   # oracle 也必须看见全铜层
        print(json.dumps({"overlaps": count_overlaps(b, lids)}))
        sys.exit(0)
    try:
        board_sha = __import__("hashlib").sha256(
            Path(pcb).read_bytes()).hexdigest()
    except Exception:
        board_sha = ""
    # stream ③: comma-separated nets planned in ONE process — one board
    # load + shared extraction instead of one subprocess per net
    _pbn = {}
    if pairs_file and pairs_file.exists():
        _pbn = pairs_by_net(pairs_file.read_text(
            encoding="utf-8").splitlines())
    for net_name in [x for x in nets_arg.split(",") if x.strip()]:
        plan = plan_net(b, net_name.strip(), board_sha=board_sha,
                        pairs=_pbn.get(net_name.strip()))
        svg = f"{out_prefix}.{net_name.strip()}.plan.svg"
        try:
            render_plan(plan, svg)
            plan["svg"] = svg
        except Exception as e:
            plan["svg_error"] = str(e)[:80]
        print(json.dumps(plan, ensure_ascii=False))
