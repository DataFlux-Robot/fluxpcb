# -*- coding: utf-8 -*-
"""direct_link — the directed intervention executor (M3).

The manual_links.py pattern, systematized: draw ONE exact polyline
(the decision is a PATH on the geomir map), endpoints landing on the
net's own island anchors so connectivity is kept by construction,
then refill+save. This is the loop's answer to a stubborn open when
the map shows a free path — a 10-line intervention instead of a
global re-route war.

Usage:  direct_link.py PCB SPEC.json
SPEC = {"net": "EN", "pts": [[x,y,layer],...], "width": 0.2}
Prints one JSON line: {drawn, segs, error?}

层契约(球位键断裂第四例修复后): pts 的层索引 = routegrid.
all_copper_ids(CuStack 序)位置, 与 geomir 规划端同一视图。
端点三态: 焊盘同层直连 / 借道同网狗骨过孔 / 打附着过孔(via_ok 把关)。
"""
import json
import math
import sys
from pathlib import Path

import pcbnew

MM = 1000000

pcb_path = sys.argv[1]
spec = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
net_name = spec["net"]
width = float(spec.get("width", 0.2))
pts = [[p[0], p[1], p[2]] for p in spec["pts"]]

b = pcbnew.LoadBoard(pcb_path)
net = b.FindNet(net_name)
if net is None:
    print(json.dumps({"drawn": 0, "error": f"net {net_name} missing"}))
    sys.exit(0)
_nc = net.GetNetCode()

# 层契约单源: 规划/验证/绘制共用 CuStack 序索引
import routegrid as _rg                          # noqa: E402
_LIDS = [int(l) for l in _rg.all_copper_ids(b)]


def layer_id(k):
    return _LIDS[k] if abs(k) < len(_LIDS) else pcbnew.F_Cu


# 规则表单源: plan 端 T2 用 kernel.rules 表(kicad_dru), reverify 旧版
# 走默认常量 —— 差 0.0074 把 5μm 边际的合法路拒成 stale。同一张表。
try:
    import sys as _s3
    _s3.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from kernel import rules as _kr
    _rules = _kr.load_rules(Path(pcb_path))
except Exception:
    _rules = None

# ── 端点三态解析(在验证之前, 保证最终几何被验证覆盖) ──
# plan 语义缺口: geomir 的验证路只保证几何净空, 不保证端点层可达 ——
# 路在内层而焊盘在表层时同层画线 = 线浮空(实测 drawn=3 连通不变)。
#   ① 焊盘在路层 -> 原地连通;
#   ② 同网过孔在附近(fanout_repair 狗骨) -> 借道: 端点改指过孔;
#   ③ 否则端点打附着过孔(via_ok 全层把关, 不过诚实失败)。
via_pts = []          # [(x, y, why)] 全部待打过孔位置
attach_log = []       # 端点三态日志(证书消费)
for idx in (0, -1):
    x, y, li = pts[idx]
    target_layer = layer_id(li)
    pad_here, pad_on = None, False
    for fp in b.GetFootprints():
        for pad in fp.Pads():
            if pad.GetNetCode() != _nc:
                continue
            pp = pad.GetPosition()
            if abs(pp.x / MM - x) > 0.05 or abs(pp.y / MM - y) > 0.05:
                continue
            pad_here = pad
            try:
                on = [int(l) for l in pad.GetLayerSet().CuStack()]
            except AttributeError:
                on = [pcbnew.F_Cu, pcbnew.B_Cu]
            pad_on = target_layer in on
    if pad_here is not None and pad_on:
        attach_log.append({"endpoint": idx, "mode": "same-layer"})
        continue                    # ① 同层直连
    # ② 借道: 最近同网过孔(狗骨) — 端点改指过孔位置
    near = None
    for t in b.GetTracks():
        if t.GetClass() != "PCB_VIA" or t.GetNetCode() != _nc:
            continue
        vp = t.GetPosition()
        d = math.hypot(vp.x / MM - x, vp.y / MM - y)
        if d <= 1.2 and (near is None or d < near[0]):
            near = (d, vp.x / MM, vp.y / MM)
    if near is not None:
        pts[idx] = [round(near[1], 3), round(near[2], 3), li]
        attach_log.append({"endpoint": idx, "mode": "via-borrow",
                           "via_at": [round(near[1], 3),
                                      round(near[2], 3)]})
        continue
    # ③ 附着过孔
    via_pts.append((x, y, "endpoint-attach"))
    attach_log.append({"endpoint": idx, "mode": "attach-via",
                       "via_ok": False})
for (px, py, pli), (qx, qy, qli) in zip(pts, pts[1:]):
    if pli != qli and (px, py) not in [(v[0], v[1]) for v in via_pts]:
        via_pts.append((px, py, "layer-change"))

# ── T1b plan->execute re-verification(覆盖端点解析后的最终几何) ──
# the plan was verified against the board AT PLANNING TIME; between
# plan and draw the board may have changed (T2/T3 runs: stale plans
# drew +5 shorts). RE-VERIFY the whole polyline against the CURRENT
# board before placing one item — the C0 contract applies to plans too.
try:
    import geomir
    _segs, _rects = geomir.extract_objects(b, _LIDS)
    _bad = None
    for (_p, _q) in zip(pts, pts[1:]):
        if _p[2] != _q[2]:
            continue        # 层间跳由 via_pts 的 via_ok 覆盖
        ok, bl = geomir.verify_polyline(
            [(_p[0], _p[1]), (_q[0], _q[1])], width, _nc,
            _segs[_p[2]], _rects[_p[2]], rules=_rules)
        if not ok:
            _bad = f"seg@{_p[:2]}->{_q[:2]}: {bl}"
            break
    for (vx, vy, why) in via_pts:
        ok, bl = geomir.via_ok(vx, vy, _nc, _segs, _rects, rules=_rules)
        if not ok:
            _bad = f"via@{why}{[vx, vy]}: {bl}"
            break
    if _bad is not None:
        print(json.dumps({"drawn": 0,
                          "error": "stale-plan rejected: " + _bad}))
        sys.exit(0)
except Exception as _e:
    print(json.dumps({"drawn": 0,
                      "error": "reverify failed: " + str(_e)[:80]}))
    sys.exit(0)
for _a in attach_log:
    if _a.get("mode") == "attach-via":
        _a["via_ok"] = True        # via_pts 全过核才走到这

items = []
prev = None
for (x, y, li) in pts:
    if prev is not None:
        px, py, pli = prev
        if abs(px - x) > 0.01 or abs(py - y) > 0.01:
            t = pcbnew.PCB_TRACK(b)
            t.SetStart(pcbnew.VECTOR2I(int(px * MM), int(py * MM)))
            t.SetEnd(pcbnew.VECTOR2I(int(x * MM), int(y * MM)))
            t.SetWidth(int(width * MM))
            t.SetLayer(layer_id(li))
            t.SetNet(net)
            b.Add(t)
            items.append(t)
    prev = (x, y, li)
for (vx, vy, _why) in via_pts:
    v = pcbnew.PCB_VIA(b)
    v.SetPosition(pcbnew.VECTOR2I(int(vx * MM), int(vy * MM)))
    v.SetWidth(int(0.6 * MM))
    v.SetDrill(int(0.3 * MM))
    v.SetNet(net)
    b.Add(v)
    items.append(v)

fill_err = None
try:
    pcbnew.ZONE_FILLER(b).Fill(b.Zones())
except Exception as e:
    # fill 失败必须随报告可见 — 静默会让下游测量读到空填充
    fill_err = str(e)[:80]

# 连通实测(闭合=测量语义): 保存前后同进程 islands 计数
import geomir as _gm2
b.BuildConnectivity()
_islands_after = len(_gm2.islands_of(b, _nc))

b.Save(pcb_path)

# 出闭合证书(方案④: 小核复核 + 实时对账由 grader 做)
try:
    import sys as _s4
    _s4.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from kernel import proofs as _pf
    _cert = _pf.make_closure_cert(
        net=net_name, path_pts=pts,
        endpoint_attach=attach_log,
        rule_table_ref="kernel.rules:" + str(
            (_rules or {}).get("parse", {})
            .get("source", {}).get("clearance", "fallback")),
        measured_after={"islands_after": _islands_after,
                        "drawn": len(items),
                        "fill_error": fill_err})
    _pf.write_cert(_cert, Path(pcb_path).parent / "certs.jsonl")
except Exception as _ce:
    # 证书缺失 = 上层按不合格处理, 不冒充 —— 但失败必须响亮
    print(json.dumps({"drawn": len(items), "net": net_name,
                      "cert_error": str(_ce)[:80],
                      "islands_after": _islands_after,
                      "fill_error": fill_err}))
    raise SystemExit(0)
print(json.dumps({"drawn": len(items), "net": net_name,
                  "segs": len(pts) - 1, "vias": len(via_pts),
                  "islands_after": _islands_after,
                  "fill_error": fill_err}))
