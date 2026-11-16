# -*- coding: utf-8 -*-
"""Board State Kernel — constraints.py: downstream→upstream edges (C4).

The binding-time defect (points 3/6): pin assignment and placement
committed their decisions with NO downstream (routing) feedback —
35 of fc's opens were pin-assignment crossing conflicts manufactured
at allocation time, unfixable downstream. Constraint objects make the
upstream binding stages hear the downstream BEFORE they bind:

    routing failure  ->  EscapeConstraint(toward-outside direction)
                      ->  pin reassignment prefers balls on the escape
                          side (regenerate board+schema consistently)

These are data, not patches: the SAME constraint object feeds the
decision layer's candidates AND the regeneration input.
"""
from __future__ import annotations

import json
from pathlib import Path


def escape_preferences(pairs: list, taxonomy: dict) -> dict:
    """For every escape-crossing net, which direction its BGA anchor
    must escape toward (the outside anchor's bearing).

    pairs: net|x1|y1|l1|x2|y2|l2 lines (kernel conn_audit format)
    returns {net: (dx, dy)} unit bearing from the BGA anchor to the
    outside anchor — the pin-reassignment cost direction.
    """
    from .causal import bga_boxes_from_pairs, _parse_pairs, _in
    parsed = _parse_pairs(pairs)
    boxes = bga_boxes_from_pairs(parsed)
    out = {}
    want = set(taxonomy.get("escape-crossing", []))
    for net, a, b in parsed:
        if net not in want:
            continue
        a_in = any(_in(bx, a) for bx in boxes)
        inside, outside = (a, b) if a_in else (b, a)
        dx, dy = outside[0] - inside[0], outside[1] - inside[1]
        n = (dx * dx + dy * dy) ** 0.5 or 1.0
        out[net] = (dx / n, dy / n)
    return out


def write_pin_override(project_dir: Path, overrides: dict) -> Path:
    """FC_PIN_OVERRIDE.json = {signal_name: ball_position} consumed by
    fcfull.mcu_ball_nets — the regeneration contract (board + schematic
    both read it, so the two never diverge)."""
    p = project_dir / "FC_PIN_OVERRIDE.json"
    p.write_text(json.dumps(overrides, ensure_ascii=False, indent=1),
                 encoding="utf-8")
    return p


def ball_grid_xy(ball):
    """球位键 -> 几何坐标(方向空间)。真键='A1' 行列名(mcu_db 语义:
    p[0] 行字母, int(p[1:]) 列号, 行号向下=板 y 同向); 兼容 'x,y'
    数字键(旧测试/兄弟实现)。不可解析 -> None。
    曾期望 'x,y' 而真实键是 'A1' -> 恒返 None -> 换球恒空
    ('无更优球位'的真因, 合成测试键掩蔽了断裂)。"""
    import re as _re
    s = str(ball)
    if "," in s:
        try:
            xs, ys = s.split(",")
            return float(xs), float(ys)
        except ValueError:
            return None
    m = _re.match(r"^([A-Z]+)(\d+)$", s)
    if m:
        r = 0
        for ch in m.group(1):
            r = r * 26 + (ord(ch) - 64)
        return float(int(m.group(2))), float(r - 1)
    return None


def escape_crossings(assign: dict, prefs: dict, ball_xy_of) -> list:
    """平面性交叉清单: (s1, s2) 球位连线与双方逃逸方向对冲。
    assign: {sig: {"ball": ...}}; prefs: {sig: (dx, dy)} 单位方向;
    ball_xy_of(ball) -> (x, y) | None。"""
    sigs = [x for x in prefs if x in assign]
    pts = {}
    for x in sigs:
        xy = ball_xy_of(assign[x].get("ball"))
        if xy is not None:
            pts[x] = xy
    out = []
    names = sorted(pts)
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            s1, s2 = names[i], names[j]
            (x1, y1), (x2, y2) = pts[s1], pts[s2]
            dx, dy = x2 - x1, y2 - y1
            n = (dx * dx + dy * dy) ** 0.5 or 1.0
            ux, uy = dx / n, dy / n
            p1x, p1y = prefs[s1]
            p2x, p2y = prefs[s2]
            # s1 需向 s2 侧逃逸 且 s2 需向 s1 侧逃逸 = 对冲交叉
            if ux * p1x + uy * p1y > 0.2 and \
                    (-ux) * p2x + (-uy) * p2y > 0.2:
                out.append((s1, s2))
    return out


def reassign_proposal(ball_nets: dict, prefs: dict, pins_db: dict,
                      mcu_center, limit: int = 12,
                      mode: str = "") -> dict:
    """Propose ball swaps for escape-crossing nets.

    ball_nets: {signal: {"ball", ...}} current assignment (mcu_db form)
    prefs:     {net/signal: (dx,dy)} escape bearing
    pins_db:   {ball: {"signals": [...]}} AF multiplexing table
    mcu_center: (x_mm, y_mm)
    mode: 搜索深度门(修 mode 死字段: 三个换球算子此前执行同一
      搜索) — 'swap_pair'=仅成对交换 / 'swap_chain3'=仅三元旋转 /
      'single_drift'=仅单球漂移 / ''=全阶(兼容旧调用)。

    Score = alignment of (ball - center) bearing with the escape
    bearing; a candidate ball must carry the signal in its AF family
    and be currently occupied by an AF-swappable signal. Returns the
    override dict {signal: new_ball} (empty when nothing helps).
    """
    import math
    # reverse index: signal -> [balls carrying it]
    rev = {}
    for ball, info in pins_db.items():
        for sig in info.get("signals", []):
            rev.setdefault(sig, []).append(ball)

    def ball_xy(ball):
        # 真键='A1' 行列名, 兼容 'x,y'(见 ball_grid_xy 契约)
        return ball_grid_xy(ball)

    # Phase B 的对齐中心: 球空间质心('A1' 形键时 mcu_center 是板 mm,
    # 两个空间混算 cos 是旧断裂之二 — 质心在球空间内自洽)
    _bxy = [ball_xy(b) for b in pins_db]
    _bxy = [p for p in _bxy if p is not None]
    if _bxy:
        _cx = sum(p[0] for p in _bxy) / len(_bxy)
        _cy = sum(p[1] for p in _bxy) / len(_bxy)
    else:
        _cx, _cy = mcu_center

    owner_of = {}
    for sig, d in ball_nets.items():
        owner_of[d["ball"]] = sig

    out = {}
    # ── Phase A: 成对交换局搜(交叉计数目标, K21 "重分配可解") ──
    def _xy_of(ball):
        return ball_xy(ball)

    assign = {sig: d for sig, d in ball_nets.items()}
    cur_cross = escape_crossings(assign, prefs, _xy_of)
    rounds = 0
    _do_pair = mode in ("", "swap_pair")
    _do_c3 = mode in ("", "swap_chain3")
    while (cur_cross and rounds < 3 and len(out) < limit
           and mode != "single_drift"):
        rounds += 1
        best_swap = None   # (cross_after, s1, b2, s2, b1)
        for (s1, s2) in (cur_cross if _do_pair else ()):
            c1 = ball_nets.get(s1, {}).get("ball")
            c2 = ball_nets.get(s2, {}).get("ball")
            if not c1 or not c2 or c1 == c2:
                continue
            # AF 约束: 交换后每个信号必须被新球承载
            if c2 not in rev.get(s1, []) or c1 not in rev.get(s2, []):
                continue
            trial = dict(assign)
            trial[s1] = {"ball": c2}
            trial[s2] = {"ball": c1}
            after = escape_crossings(trial, prefs, _xy_of)
            if len(after) < len(cur_cross):
                key = (len(after), s1, c2, s2, c1)
                if best_swap is None or key < best_swap:
                    best_swap = key
        if best_swap is None and _do_c3:
            # Phase A2: 三元旋转(swap_chain3) — 两两交换解不动的
            # 三体对冲, A→B→C→A 旋转可能整体解序
            best_c3 = None
            # 三元组从交叉集信号生成(不相交对凑不出纯三信号对冲);
            # 限窗 12 信号 = 220 三元组 x 2 旋向, 防组合爆炸
            from itertools import combinations as _combos
            sigs3 = []
            for _p1, _p2 in cur_cross:
                for _x in (_p1, _p2):
                    if _x not in sigs3:
                        sigs3.append(_x)
            for trio in _combos(sigs3[:12], 3):
                for order in (trio, (trio[0], trio[2], trio[1])):
                    a1, a2, a3 = order
                    c1 = ball_nets.get(a1, {}).get("ball")
                    c2 = ball_nets.get(a2, {}).get("ball")
                    c3 = ball_nets.get(a3, {}).get("ball")
                    if not (c1 and c2 and c3) or len(
                            {c1, c2, c3}) < 3:
                        continue
                    # AF: a1->c2, a2->c3, a3->c1
                    if not (c2 in rev.get(a1, [])
                            and c3 in rev.get(a2, [])
                            and c1 in rev.get(a3, [])):
                        continue
                    trial = dict(assign)
                    trial[a1] = {"ball": c2}
                    trial[a2] = {"ball": c3}
                    trial[a3] = {"ball": c1}
                    after = escape_crossings(trial, prefs, _xy_of)
                    if len(after) < len(cur_cross):
                        key = (len(after), a1, a2, a3)
                        if best_c3 is None or key < best_c3:
                            best_c3 = key
            if best_c3 is None:
                break
            _na, a1, a2, a3 = best_c3
            c1 = ball_nets.get(a1, {}).get("ball")
            c2 = ball_nets.get(a2, {}).get("ball")
            c3 = ball_nets.get(a3, {}).get("ball")
            out[a1] = c2
            out[a2] = c3
            out[a3] = c1
            assign[a1] = {"ball": c2}
            assign[a2] = {"ball": c3}
            assign[a3] = {"ball": c1}
            cur_cross = escape_crossings(assign, prefs, _xy_of)
            continue
        if best_swap is None:
            break        # mode=swap_pair 无成对解 / chain3 已试尽
        _na, s1, b2, s2, b1 = best_swap
        out[s1] = b2
        out[s2] = b1
        assign[s1] = {"ball": b2}
        assign[s2] = {"ball": b1}
        cur_cross = escape_crossings(assign, prefs, _xy_of)
    if not cur_cross:
        return out

    # ── Phase B: 单球改善(交换解不掉的残余, 旧启发式保留) ──
    for sig, (dx, dy) in (list(prefs.items())
                          if mode in ("", "single_drift") else ()):
        if sig not in rev or sig in out or len(out) >= limit:
            continue
        cur = ball_nets.get(sig)
        if not cur:
            continue
        cur_xy = ball_xy(cur["ball"])
        if cur_xy is None:
            continue
        cx, cy = _cx, _cy
        best = None
        for ball in rev[sig]:
            xy = ball_xy(ball)
            if xy is None or ball == cur["ball"]:
                continue
            bx, by = xy[0] - cx, xy[1] - cy
            n = (bx * bx + by * by) ** 0.5 or 1.0
            align = (bx / n) * dx + (by / n) * dy   # cos(angle)
            cxx, cyy = cur_xy[0] - cx, cur_xy[1] - cy
            n2 = (cxx * cxx + cyy * cyy) ** 0.5 or 1.0
            cur_align = (cxx / n2) * dx + (cyy / n2) * dy
            if align > cur_align + 0.05 and (best is None
                                             or align > best[1]):
                best = (ball, align)
        if best:
            out[sig] = best[0]
    return out
