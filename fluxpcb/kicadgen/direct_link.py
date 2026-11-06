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
stubs = []            # offset-attach 引出短线 (x1,y1,x2,y2,layer)
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
    # ③ 附着过孔 —— 端点正中被邻网堵时走 offset-attach 环形候选
    #   (实测 3 案 网码 68/2/11 via@endpoint-attach 全被邻网拒,
    #    一次挪位即过 —— 端点三态升级为端点多态)
    _pick = None
    try:
        import geomir as _g3
        _sg3, _rg3 = _g3.extract_objects(b, _LIDS)
        _cands = [(x, y)]
        for _r in (0.25, 0.4, 0.55, 0.8, 1.1):
            for _k in range(16):
                _ang = _k * math.pi / 8
                _cands.append((x + _r * math.cos(_ang),
                               y + _r * math.sin(_ang)))
        for (_cx, _cy) in _cands:
            _ok3, _bl3 = _g3.via_ok(_cx, _cy, _nc, _sg3, _rg3,
                                    rules=_rules)
            if _ok3:
                _pick = (_cx, _cy)
                break
    except Exception:
        _pick = (x, y)      # 预筛异常退回原位, 交终验证裁决
    if _pick is None:
        # ④ 逃逸附着: 直线 stub 被墙堵死时, geomir 微规划多段逃逸
        #   折线到环外自由槽(实测: UART4_TX 端点被邻铜焊死在盒子里,
        #   2.8mm 内直线 stub 全堵, 逃逸折线是钥匙)
        import subprocess as _sp
        import sys as _sy
        import tempfile as _tf
        _gdir = Path(__file__).resolve().parent
        # 逃逸必须从焊盘所在层出发(教训: 按路层画=焊盘浮空假闭合)
        _eli = li
        if pad_here is not None and not pad_on:
            try:
                _pid = int(pad_here.GetLayerSet().CuStack()[0])
                _eli = _LIDS.index(_pid)
            except Exception:
                _eli = li
        for _r in (1.4, 1.8, 2.2):
            if _pick is not None:
                break
            for _k in range(16):
                _ang = _k * math.pi / 8
                _cx = x + _r * math.cos(_ang)
                _cy = y + _r * math.sin(_ang)
                try:
                    _ok4, _ = _g3.via_ok(_cx, _cy, _nc, _sg3, _rg3,
                                         rules=_rules)
                except Exception:
                    _ok4 = False
                if not _ok4:
                    continue
                # geomir 微规划: 端点 -> 槽 (焊盘层逃逸折线)
                try:
                    _tmp = Path(_tf.mkdtemp())
                    _pf = _tmp / 'esc_pairs.txt'
                    _pf.write_text(
                        '%s|%.3f|%.3f|%d|%.3f|%.3f|%d'
                        % (net_name, x, y, _eli, _cx, _cy, _eli))
                    _pr = _sp.run(
                        [_sy.executable, str(_gdir / 'geomir.py'),
                         str(pcb_path), net_name,
                         str(_tmp / 'plan'), str(_pf)],
                        capture_output=True, text=True,
                        encoding='utf-8', errors='replace', timeout=120)
                    _jl = [l.strip() for l in (_pr.stdout or '').splitlines()
                           if l.strip().startswith('{')]
                    _pj = json.loads(_jl[-1]) if _jl else {}
                    _paths = _pj.get('paths') or []
                    if not _paths:
                        continue
                    _esc = _paths[0].get('pts') or []
                    if len(_esc) < 2:
                        continue
                    # 提交: 折线各段入 stubs(验证/绘制已覆盖), 孔落槽
                    for (_p1, _p2) in zip(_esc, _esc[1:]):
                        stubs.append((_p1[0], _p1[1],
                                      _p2[0], _p2[1], _p1[2]))
                    pts[idx] = [round(_cx, 3), round(_cy, 3), li]
                    via_pts.append((_cx, _cy, "endpoint-attach-escape"))
                    attach_log.append(
                        {"endpoint": idx, "mode": "attach-via-escape",
                         "via_at": [round(_cx, 3), round(_cy, 3)],
                         "esc_segs": len(_esc) - 1})
                    _pick = 'escape'      # 已处理, 跳过原位回落
                    break
                except Exception:
                    continue
        if _pick is None:
            _pick = (x, y)  # ④也灭 -> 诚实交终验证拒(不假成功)
        if _pick == 'escape':
            continue
    _ox, _oy = _pick
    if abs(_ox - x) > 0.01 or abs(_oy - y) > 0.01:
        pts[idx] = [round(_ox, 3), round(_oy, 3), li]
        stubs.append((x, y, round(_ox, 3), round(_oy, 3), li))
        via_pts.append((_ox, _oy, "endpoint-attach-offset"))
        attach_log.append({"endpoint": idx, "mode": "attach-via-offset",
                           "via_at": [round(_ox, 3), round(_oy, 3)],
                           "stub_mm": round(math.hypot(_ox - x,
                                                       _oy - y), 3)})
    else:
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
    for (_sx, _sy, _ex, _ey, _sli) in stubs:
        ok, bl = geomir.verify_polyline(
            [(_sx, _sy), (_ex, _ey)], width, _nc,
            _segs[_sli], _rects[_sli], rules=_rules)
        if not ok:
            _bad = f"stub@{[_sx, _sy]}: {bl}"
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
for (_sx, _sy, _ex, _ey, _sli) in stubs:
    t = pcbnew.PCB_TRACK(b)
    t.SetStart(pcbnew.VECTOR2I(int(_sx * MM), int(_sy * MM)))
    t.SetEnd(pcbnew.VECTOR2I(int(_ex * MM), int(_ey * MM)))
    t.SetWidth(int(width * MM))
    t.SetLayer(layer_id(_sli))
    t.SetNet(net)
    b.Add(t)
    items.append(t)
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
