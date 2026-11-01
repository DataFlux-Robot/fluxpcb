# -*- coding: utf-8 -*-
"""power_stitch — 定向平面缝合(② power-fragment 的结构解).

route_session 里的 power_stitch 是 session 内的全量贪心步(不可定向、
动作层调用=重跑整个 session); 本工具按网定向: 对目标网**没有过孔的
连通岛**逐焊盘尝试 via-in-pad / 偏置过孔把碎片钉到内层平面。

语义边界(诚实声明): 本工具只保证每步几何合法(via_ok 全层校验) +
把碎片引向平面; "是否真连上"由闭环的 Txn/严格门禁终审判定(delta)。
2 层板无第二平面时缝合无能为力 — 那是平面感知代价(②的另一半)的职责。

Usage: power_stitch.py PCB NET1,NET2,...
Prints: {"stitched", "skipped", "islands_without_via", "nets"}
"""
import json
import sys
from pathlib import Path

import pcbnew

MM = 1000000
# 候选扩展(FC_L13 诊断: skip=10 的焊盘被 0.25 过孔+稀疏偏移挡死):
# 更小过孔(JLC 最小 0.3/0.15)+更密偏移环
OFFSETS = [(0.0, 0.0), (0.3, 0.0), (-0.3, 0.0), (0.0, 0.3), (0.0, -0.3),
           (0.6, 0.0), (-0.6, 0.0), (0.0, 0.6), (0.0, -0.6),
           (0.9, 0.0), (-0.9, 0.0), (0.0, 0.9), (0.0, -0.9)]
STUB_W = 0.2
VIA_SIZES = [(0.25, 0.125), (0.2, 0.1), (0.15, 0.075)]  # 大->小试

sys.path.insert(0, str(Path(__file__).resolve().parent))
import geomir                                        # noqa: E402
import routegrid as rg                               # noqa: E402

pcb_path = Path(sys.argv[1])
net_names = [x for x in (sys.argv[2] if len(sys.argv) > 2 else "")
             .split(",") if x.strip()]
b = pcbnew.LoadBoard(str(pcb_path))
b.BuildConnectivity()
conn = b.GetConnectivity()
# 验证视图必须全铜层(via_ok 全层契约) — 布线层策略另论
lids = rg.all_copper_ids(b)
segs, rects = geomir.extract_objects(b, lids)
li_f = lids.index(pcbnew.F_Cu) if pcbnew.F_Cu in lids else 0


def _cluster_has_via(pad, nc):
    try:
        for it in conn.GetConnectedItems(pad):
            if type(it).__name__ == "PCB_VIA" and \
                    it.GetNetCode() == nc:
                return True
    except (AttributeError, TypeError):
        pass    # ok-silent: 畸形对象逐个跳过(单对象不拖垮提取)
    return False


stitched = skipped = no_via_islands = 0
report = []
for net_name in net_names:
    net = b.FindNet(net_name)
    if net is None:
        report.append({"net": net_name, "error": "net missing"})
        continue
    nc = net.GetNetCode()
    islands = geomir.islands_of(b, nc)
    n_isl_before = len(islands)
    did = 0
    for isl in islands:
        # 岛内取真实焊盘(按 pads 坐标回查)
        for (px, py) in isl["pads"][:2]:
            pad = None
            for fp in b.GetFootprints():
                for p in fp.Pads():
                    if p.GetNetCode() != nc:
                        continue
                    q = p.GetPosition()
                    if abs(q.x / MM - px) < 0.01 and \
                            abs(q.y / MM - py) < 0.01:
                        pad = p
                        break
                if pad is not None:
                    break
            if pad is None or _cluster_has_via(pad, nc):
                continue
            placed_here = False
            for (dx, dy) in OFFSETS:
                vx, vy = px + dx, py + dy
                for (v_r, v_d) in VIA_SIZES:
                    ok_v, _bl = geomir.via_ok(vx, vy, nc, segs, rects,
                                              via_r=v_r)
                    if not ok_v:
                        continue
                    if abs(dx) > 1e-6 or abs(dy) > 1e-6:
                        ok_s, _bl2 = geomir.verify_polyline(
                            [(px, py), (vx, vy)], STUB_W, nc,
                            segs[li_f], rects[li_f])
                        if not ok_s:
                            continue
                        t = pcbnew.PCB_TRACK(b)
                        t.SetStart(pcbnew.VECTOR2I(int(px * MM),
                                                   int(py * MM)))
                        t.SetEnd(pcbnew.VECTOR2I(int(vx * MM),
                                                 int(vy * MM)))
                        t.SetWidth(int(STUB_W * MM))
                        t.SetLayer(pcbnew.F_Cu)
                        t.SetNet(net)
                        b.Add(t)
                    v = pcbnew.PCB_VIA(b)
                    v.SetPosition(pcbnew.VECTOR2I(int(vx * MM),
                                                  int(vy * MM)))
                    v.SetWidth(int(v_r * 2 * MM))
                    v.SetDrill(int(v_d * 2 * MM))
                    v.SetNet(net)
                    b.Add(v)
                    stitched += 1
                    did += 1
                    placed_here = True
                    break
                if placed_here:
                    break
            if not placed_here:
                skipped += 1
            break           # 每岛最多钉一个焊盘(碎片引向平面即够)
    if n_isl_before > 1 and did == 0:
        no_via_islands += 1

fill_err = None
try:
    pcbnew.ZONE_FILLER(b).Fill(b.Zones())
except Exception as e:
    fill_err = str(e)[:80]
b.Save(str(pcb_path))
print(json.dumps({"stitched": stitched, "skipped": skipped,
                  "islands_without_via": no_via_islands,
                  "nets": net_names, "report": report[:6],
                  "fill_error": fill_err}))
