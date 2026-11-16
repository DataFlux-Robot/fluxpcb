# -*- coding: utf-8 -*-
"""zone_bridge — 平面铜桥(C1: zone 切割类残差的结构解, PENDING_IMPL
候选 'zone_bridge' 的实现).

FC_L13 诊断: GND/电源碎片被信号线切开(12 对), 连接/挪/撕/缝合四类
算子 53 微步全灭 —— 缺的是"跨切缝桥接"这一类动作:
  对每个开路对: geomir hop-repair 规划(跨层绕切缝: 下潜到内层从
  切割信号下方穿过再上浮) -> 直连落板 -> 两端 stitch 钉固。
几何校验全铜层(T1b 语义), 每对独立记账, 零残留失败(失败留 reject)。

Usage: zone_bridge.py PCB NET1,NET2,...
Prints: {"bridged": n, "skipped": m, "pairs": [...], "net": ...}
"""
import json
import sys
from pathlib import Path

import pcbnew

MM = 1000000
sys.path.insert(0, str(Path(__file__).resolve().parent))
import geomir                                        # noqa: E402
import routegrid as rg                               # noqa: E402

pcb_path = Path(sys.argv[1])
net_names = [x for x in (sys.argv[2] if len(sys.argv) > 2 else "")
             .split(",") if x.strip()]

b = pcbnew.LoadBoard(str(pcb_path))
b.BuildConnectivity()
lids = rg.all_copper_ids(b)     # 验证视图全铜层(via_ok 契约)
segs, rects = geomir.extract_objects(b, lids)
planes = geomir.extract_planes(b, lids)

try:
    rp = pcb_path.parent / "ratsnest_pairs.txt"
    pair_lines = rp.read_text(encoding="utf-8").splitlines() \
        if rp.exists() else []
except OSError:
    pair_lines = []
pairs_by_net = geomir.pairs_by_net(pair_lines)


def layer_id(k):
    if k == 0:
        return pcbnew.F_Cu
    if k == 1:
        return pcbnew.B_Cu
    lid = getattr(pcbnew, "In%d_Cu" % (k - 1), None)
    return lid if lid is not None else pcbnew.F_Cu


bridged = skipped = 0
report = []
for net_name in net_names:
    net = b.FindNet(net_name)
    if net is None:
        continue
    nc = net.GetNetCode()
    pairs = pairs_by_net.get(net_name) or []
    if not pairs:
        # 无 pairs 文件时退回岛间桥接
        islands = geomir.islands_of(b, nc)
        pairs = [(tuple(islands[0]["anchor"]),
                  tuple(islands[i]["anchor"]))
                 for i in range(1, len(islands))]
    for (pa, pb) in pairs:
        ok_paths, rejects = geomir.plan_paths(
            pa, pb, 0.15, nc, segs, rects, limit=2,
            planes_by_layer=planes)
        if not ok_paths:
            skipped += 1
            report.append({"net": net_name, "pair": [pa, pb],
                           "reject": (rejects[0].get("blocked_by")
                                      or {}).get("net_name")
                                      if rejects else None})
            continue
        p0 = ok_paths[0]
        items = 0
        prev = None
        for (x, y, li) in p0["pts"]:
            if prev is not None:
                px, py, pli = prev
                if pli != li:
                    v = pcbnew.PCB_VIA(b)
                    v.SetPosition(pcbnew.VECTOR2I(int(px * MM),
                                                  int(py * MM)))
                    v.SetWidth(int(0.5 * MM))
                    v.SetDrill(int(0.25 * MM))
                    v.SetNet(net)
                    b.Add(v)
                    items += 1
                if abs(px - x) > 0.01 or abs(py - y) > 0.01:
                    t = pcbnew.PCB_TRACK(b)
                    t.SetStart(pcbnew.VECTOR2I(int(px * MM),
                                               int(py * MM)))
                    t.SetEnd(pcbnew.VECTOR2I(int(x * MM),
                                             int(y * MM)))
                    t.SetWidth(int(p0.get("width", 0.15) * MM))
                    t.SetLayer(layer_id(li))
                    t.SetNet(net)
                    b.Add(t)
                    items += 1
            prev = (x, y, li)
        if items:
            bridged += 1
            report.append({"net": net_name, "pair": [pa, pb],
                           "mode": p0.get("mode"),
                           "len": p0.get("length_mm")})

fill_err = None
try:
    pcbnew.ZONE_FILLER(b).Fill(b.Zones())
except Exception as e:
    fill_err = str(e)[:80]
b.Save(str(pcb_path))
print(json.dumps({"bridged": bridged, "skipped": skipped,
                  "nets": net_names, "report": report[:8],
                  "fill_error": fill_err}))
