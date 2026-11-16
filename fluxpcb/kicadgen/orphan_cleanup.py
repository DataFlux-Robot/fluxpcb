# -*- coding: utf-8 -*-
"""orphan_cleanup — 孤铜垃圾清理(HarnessPro 诊断驱动: 14 微步啃不动
16 开的轨迹诊断结论 = 焊盘簇<->dangling stub 形态, 该删不该连).

判据(精确): 同网走线/过孔的连通簇里**没有任何焊盘** = 孤铜垃圾
(FR 重建/换球再生成的残渣; KiCad ratsnest 把它算成开路对,
 连接类算子对它注定无效 — 连接垃圾只会更乱)。

Usage: orphan_cleanup.py PCB NET1,NET2,...
Prints: {"removed": n, "nets": [...], "kept_pads": m}
"""
import json
import sys
from pathlib import Path

import pcbnew

pcb_path = Path(sys.argv[1])
net_names = [x for x in (sys.argv[2] if len(sys.argv) > 2 else "")
             .split(",") if x.strip()]

b = pcbnew.LoadBoard(str(pcb_path))
b.BuildConnectivity()
conn = b.GetConnectivity()

# 同网焊盘坐标集(判孤儿用)
def _pad_keys(nc):
    keys = set()
    for fp in b.GetFootprints():
        for pad in fp.Pads():
            if pad.GetNetCode() != nc:
                continue
            q = pad.GetPosition()
            keys.add((q.x, q.y))
    return keys


removed = 0
kept = 0
report = []
for net_name in net_names or [n.GetNetname()
                              for n in
                              b.GetNetInfo().NetsByNetcode().values()
                              if n and n.GetNetCode()]:
    net = b.FindNet(net_name)
    if net is None:
        continue
    nc = net.GetNetCode()
    pad_xy = _pad_keys(nc)
    if not pad_xy:
        continue
    orphans = []
    for t in list(b.GetTracks()):
        if t.GetNetCode() != nc:
            continue
        try:
            cluster = conn.GetConnectedItems(t)
        except (AttributeError, TypeError):
            continue    # ok-silent: 畸形对象逐个跳过
        has_pad = False
        for it in cluster:
            if type(it).__name__ == "PAD":
                q = it.GetPosition()
                if (q.x, q.y) in pad_xy:
                    has_pad = True
                    break
        if not has_pad:
            orphans.append(t)
    for t in orphans:
        b.Remove(t)
        removed += 1
    kept += len(pad_xy)
    if orphans:
        report.append({"net": net_name, "orphans": len(orphans)})

try:
    pcbnew.ZONE_FILLER(b).Fill(b.Zones())
except Exception as e:
    report.append({"fill_error": str(e)[:60]})
b.Save(str(pcb_path))
print(json.dumps({"removed": removed, "nets": [r["net"] for r in
                                               report][:12],
                  "kept_pads": kept, "report": report[:8]}))
