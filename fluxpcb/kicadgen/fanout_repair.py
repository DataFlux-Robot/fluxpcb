# -*- coding: utf-8 -*-
"""fanout_repair — 定向 BGA 扇出重构(① escape-crossing 主攻).

route_session 的 dogbone_fanout 是全量一次性、不可定向、不可重复跑
(重复跑双重放置); 闭环需要的是**按网定向、有界代价、可反复调用**的
动作原语。本工具:
  1. 找目标网在 BGA(>=60 SMD pad 封装)内的**未逃逸焊盘**(同网簇里
     无 via 的焊盘),
  2. 清死桩(盒内 <0.3mm 且远端悬空的同网残段 — 旧失败逃逸的尸体),
  3. 重建 dog-bone: 对角短桩+过孔(棋盘奇偶位+向板心回退), 全层
     via 校验(T1)+桩几何校验,
  4. 全局验收交给闭环(strict gate + Txn) — 本工具只保证每步几何合法。

Usage: fanout_repair.py PCB NET1,NET2,...
Prints: {"placed", "ripped_dead", "skipped", "nets", "unescaped"}
"""
import json
import math
import sys
from pathlib import Path

import pcbnew

MM = 1000000
BGA_PADS_MIN = 60          # route_session/clear_bcup 同款启发式
STUB_W = 0.15              # dog-bone 短桩宽
VIA_R = float(__import__("os").environ.get(
    "FANOUT_VIA_R", "0.2"))   # BGA dog-bone 过孔 annulus(默认0.4mm
                              # via); 小过孔档 0.15=0.3mm 外径(JLC
                              # 多层最小 0.25 外径, 0.2 钻孔常规档) ——
                              # 0.4mm 全灭的球场死位在小过孔下复测
VIA_DRILL = float(__import__("os").environ.get(
    "FANOUT_VIA_DRILL", "0.1"))
DEAD_STUB_MM = 0.3         # 死桩判定长度上限

sys.path.insert(0, str(Path(__file__).resolve().parent))
import geomir                                        # noqa: E402
import routegrid as rg                               # noqa: E402

pcb_path = Path(sys.argv[1])
net_names = [x for x in (sys.argv[2] if len(sys.argv) > 2 else "")
             .split(",") if x.strip()]
# H2 扇出-走廊融合: 走廊方向提示(可选, "dx,dy") —— 狗骨朝验证走廊
# 方向放, 而不是只按棋盘奇偶/板心序(H1 教训: 球场死位的自由通道
# 在走廊侧, 板心序候选全灭)
DIR = None
if len(sys.argv) > 3 and "," in (sys.argv[3] or ""):
    try:
        _dx, _dy = sys.argv[3].split(",")
        DIR = (float(_dx), float(_dy))
    except ValueError:
        DIR = None
# H2b 逃逸破封: 狗骨全灭时撕邻网堵线(D12 实测被 net48/net51
# 内层逃逸线物理封死)。FANOUT_RIP=1 = **只撕不放**(单进程单变更
# —— SWIG 板变更后再用旧代理实测 segfault, 破封与放置分进程),
# 撕后本工具正常模式(无 RIP)重跑即放狗骨。
RIP = __import__("os").environ.get("FANOUT_RIP", "0") == "1"
b = pcbnew.LoadBoard(str(pcb_path))
b.BuildConnectivity()
conn = b.GetConnectivity()

# ---- BGA 盒(route_session.bga_boxes 同构) ----
boxes = []
for fp in b.GetFootprints():
    pads = [p for p in fp.Pads()
            if p.GetAttribute() != pcbnew.PAD_ATTRIB_PTH
            and p.IsOnLayer(pcbnew.F_Cu)]
    if len(pads) < BGA_PADS_MIN:
        continue
    bb = fp.GetBoundingBox()
    boxes.append((bb.GetX() / MM - 0.5, bb.GetY() / MM - 0.5,
                  (bb.GetX() + bb.GetWidth()) / MM + 0.5,
                  (bb.GetY() + bb.GetHeight()) / MM + 0.5,
                  pads))

if not boxes:
    print(json.dumps({"placed": 0, "ripped_dead": 0, "skipped": 0,
                      "nets": net_names, "unescaped": 0,
                      "note": "no BGA footprint"}))
    sys.exit(0)

bb_all = b.GetBoardEdgesBoundingBox()
cx = (bb_all.GetX() + bb_all.GetWidth() / 2) / MM
cy = (bb_all.GetY() + bb_all.GetHeight() / 2) / MM

# 验证视图必须全铜层(via_ok 全层契约) — 布线层策略另论
lids = rg.all_copper_ids(b)
segs, rects = geomir.extract_objects(b, lids)
li_f = lids.index(pcbnew.F_Cu) if pcbnew.F_Cu in lids else 0


def _pitch(pads):
    pts = [(p.GetPosition().x / MM, p.GetPosition().y / MM)
           for p in pads]
    nn = []
    for i, (x, y) in enumerate(pts):
        d = min((math.hypot(x - x2, y - y2)
                 for j, (x2, y2) in enumerate(pts) if j != i),
                default=0)
        if d > 1e-6:
            nn.append(d)
    nn.sort()
    return nn[len(nn) // 2] if nn else 0.8


def _cluster_has_via(pad, nc):
    try:
        for it in conn.GetConnectedItems(pad):
            if type(it).__name__ == "PCB_VIA" and \
                    it.GetNetCode() == nc:
                return True
    except (AttributeError, TypeError):
        pass    # ok-silent: 畸形对象逐个跳过(单对象不拖垮提取)
    return False


placed = ripped = skipped = unescaped = 0
placed_vias = {}        # H2: {net: [x, y]} 狗骨 via = 走廊规划端点身份
report = []
for net_name in net_names:
    net = b.FindNet(net_name)
    if net is None:
        report.append({"net": net_name, "error": "net missing"})
        continue
    nc = net.GetNetCode()
    # 该网在 BGA 盒内的焊盘
    pads_in = []
    for (x0, y0, x1, y1, bpads) in boxes:
        for p in bpads:
            if p.GetNetCode() != nc:
                continue
            q = p.GetPosition()
            pads_in.append((p, q.x / MM, q.y / MM))
    if not pads_in:
        report.append({"net": net_name, "error": "no BGA pads"})
        continue

    # 1. 清死桩: 盒内 <0.3mm 同网残段, 远端不落在 pad/via/同网走线上
    for t in list(b.GetTracks()):
        if type(t).__name__ != "PCB_TRACK" or t.GetNetCode() != nc:
            continue
        s, e = t.GetStart(), t.GetEnd()
        sx, sy, ex, ey = s.x / MM, s.y / MM, e.x / MM, e.y / MM
        if math.hypot(ex - sx, ey - sy) > DEAD_STUB_MM:
            continue
        in_box = any(x0 <= sx <= x1 and y0 <= sy <= y1
                     for (x0, y0, x1, y1, _p) in boxes)
        if not in_box:
            continue
        dead = True
        for (p, px, py) in pads_in:
            if math.hypot(sx - px, sy - py) < 0.05 or \
                    math.hypot(ex - px, ey - py) < 0.05:
                dead = False
                break
        if dead:
            for v in b.GetTracks():
                if type(v).__name__ != "PCB_VIA":
                    continue
                vp = v.GetPosition()
                if math.hypot(sx - vp.x / MM, sy - vp.y / MM) < 0.1 or \
                        math.hypot(ex - vp.x / MM,
                                   ey - vp.y / MM) < 0.1:
                    dead = False
                    break
        if dead:
            b.Remove(t)
            ripped += 1

    # 2. 重建未逃逸焊盘
    half = _pitch([p for (p, _x, _y) in pads_in]) / 2.0
    for (p, px, py) in pads_in:
        if _cluster_has_via(p, nc):
            continue
        unescaped += 1
        # 棋盘奇偶 home + 向板心回退(dogbone_fanout 同款候选序)
        gx, gy = int(round(px / (half * 2))), int(round(py / (half * 2)))
        sgn = 1 if (gx + gy) % 2 == 0 else -1
        cands = [(sgn * half, sgn * half),
                 (sgn * half, -sgn * half),
                 (-sgn * half, sgn * half),
                 (-sgn * half, -sgn * half)]
        # 向板心回退 + 向外逃逸(外圈球的正确方向: dog-bone 惯例
        # 中 edge row 向板外逃) + 1.5x 半距(外圈余量)
        ox, oy = px - cx, py - cy
        ol = math.hypot(ox, oy) or 1.0
        cands += [(ox / ol * half * 1.5, oy / ol * half * 1.5),
                  (-ox / ol * half * 1.5, -oy / ol * half * 1.5)]
        if DIR:
            # 走廊方向候选(±0.5 半距垂直偏移 + 1x/2.5x 径向)
            hx, hy = DIR
            hl = math.hypot(hx, hy) or 1.0
            hx, hy = hx / hl, hy / hl
            px_, py_ = -hy, hx
            cands += [(hx * half * 1.0, hy * half * 1.0),
                      (hx * half * 1.5 + px_ * half * 0.5,
                       hy * half * 1.5 + py_ * half * 0.5),
                      (hx * half * 1.5 - px_ * half * 0.5,
                       hy * half * 1.5 - py_ * half * 0.5),
                      (hx * half * 2.5, hy * half * 2.5)]
            # 走廊方向优先排序(dot 降序), 板心序退居
            cands.sort(key=lambda d: -(d[0] * hx + d[1] * hy))
        else:
            cands.sort(key=lambda d: math.hypot(px + d[0] - cx,
                                                py + d[1] - cy))
        done = False
        for (dx, dy) in cands:
            vx, vy = px + dx, py + dy
            ok_v, _bl = geomir.via_ok(vx, vy, nc, segs, rects,
                                      via_r=VIA_R)
            if not ok_v:
                continue
            ok_s, _bl2 = geomir.verify_polyline(
                [(px, py), (vx, vy)], STUB_W, nc,
                segs[li_f], rects[li_f])
            if not ok_s:
                continue
            # 落板: 短桩 + 过孔(T1 全层已验)
            t = pcbnew.PCB_TRACK(b)
            t.SetStart(pcbnew.VECTOR2I(int(px * MM), int(py * MM)))
            t.SetEnd(pcbnew.VECTOR2I(int(vx * MM), int(vy * MM)))
            t.SetWidth(int(STUB_W * MM))
            t.SetLayer(pcbnew.F_Cu)
            t.SetNet(net)
            b.Add(t)
            v = pcbnew.PCB_VIA(b)
            v.SetPosition(pcbnew.VECTOR2I(int(vx * MM), int(vy * MM)))
            v.SetWidth(int(VIA_R * 2 * MM))
            v.SetDrill(int(VIA_DRILL * 2 * MM))
            v.SetNet(net)
            b.Add(v)
            placed += 1
            placed_vias[net_name] = [round(vx, 3), round(vy, 3)]
            done = True
            break
        if not done and RIP:
            # H2b 破封(只撕不放): 收集全候选失败的阻塞网, 撕球周
            # 2.5*half 内该网走线; 放置留给下一次正常模式运行
            blockers = set()
            for (dx, dy) in cands:
                vx, vy = px + dx, py + dy
                ok_v, bl_v = geomir.via_ok(vx, vy, nc, segs, rects,
                                           via_r=VIA_R)
                if not ok_v and isinstance(bl_v, dict):
                    bn = bl_v.get("net")
                    if isinstance(bn, int) and bn != nc:
                        blockers.add(bn)
            ripped_here = 0
            for t in list(b.GetTracks()):
                if type(t).__name__ != "PCB_TRACK":
                    continue
                if t.GetNetCode() not in blockers:
                    continue
                s, e = t.GetStart(), t.GetEnd()
                d1 = math.hypot(s.x / MM - px, s.y / MM - py)
                d2 = math.hypot(e.x / MM - px, e.y / MM - py)
                if min(d1, d2) <= half * 2.5:
                    b.Remove(t)
                    ripped_here += 1
            if ripped_here:
                ripped += ripped_here
                report.append({
                    "net": net_name, "mode": "rip-unseal",
                    "ripped_nets": sorted(blockers),
                    "ripped_segs": ripped_here,
                    "next": "rerun fanout_repair (normal mode) "
                            "to place dogbone"})
        if not done:
            skipped += 1

fill_err = None
if not RIP:
    # RIP 模式跳过 fill: KiCad10 SWIG 的 Remove+ZONE_FILLER 实测
    # segfault(b.Zones() 访问违例) —— 撕模式只存板, 重填交给后续
    # 工具(route_session/direct_link/放置模式都会 ZONE_FILLER)
    try:
        pcbnew.ZONE_FILLER(b).Fill(b.Zones())
    except Exception as e:
        fill_err = str(e)[:80]    # fill 失败必须随报告可见
b.Save(str(pcb_path))
print(json.dumps({"placed": placed, "ripped_dead": ripped,
                  "skipped": skipped, "nets": net_names,
                  "unescaped": unescaped, "report": report[:6],
                  "placed_vias": placed_vias,
                  "fill_error": fill_err}))
