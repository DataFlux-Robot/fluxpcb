# -*- coding: utf-8 -*-
"""clearance_surgery — C1 逐违例点手术(55 间距的布局修复).

fab_waivers.json genuine 清单逐条手术: 违例对里可收窄的走线按
宽度级联 0.2->0.15->0.127->0.11 收窄(半宽差直接变间距增益), 收到
预测增益 >= shortfall 为止。地板纪律:
  * 信号网 0.11mm(JLC 6 层最小 0.09 + 裕量);
  * 电源网(GND/3V3/5V/VDD*/VBAT) 0.15mm —— 电流容量不换间距;
  * 过孔/焊盘不动(无宽度杠杆) -> needs_nudge 诚实上报, 走 gap_nudge
    或换球链, 不硬凑。
手术后由 fab_close 复测裁决(本工具只保证每步几何合法语义)。

Usage: clearance_surgery.py PCB FAB_WAIVERS_JSON
Prints: {"narrowed": n, "items": [...], "needs_nudge": [...]}
"""
import json
import sys
from pathlib import Path

import pcbnew

MM = 1000000
RUNGS = (0.2, 0.15, 0.127, 0.11)          # 信号网级联
POWER_FLOOR = 0.15                        # 电源网地板
POWER_NETS = {"GND", "3V3", "5V", "VDDA", "VDD", "VBAT", "VBUS",
              "3V3A", "1V8", "1V2", "VCC", "VDDA_A"}

pcb_path = Path(sys.argv[1])
fw_path = Path(sys.argv[2])
b = pcbnew.LoadBoard(str(pcb_path))
led = json.loads(fw_path.read_text(encoding="utf-8"))
genuine = led.get("genuine") or []

# uuid -> (kind, obj)
byid = {}
for t in b.GetTracks():
    byid[t.m_Uuid.AsString()] = ("track" if t.GetClass() == "PCB_TRACK"
                                 else "via", t)
for fp in b.GetFootprints():
    for pad in fp.Pads():
        byid[pad.m_Uuid.AsString()] = ("pad", pad)


def next_rung(w, floor):
    for r in RUNGS:
        if r < w and r >= floor:
            return r
    return None


items_out, needs_nudge = [], []
narrowed = 0
for it in genuine:
    shortfall = float(it.get("shortfall_mm") or 0)
    if shortfall <= 0:
        continue
    uuids = it.get("uuids") or []
    ents = [byid.get(u) for u in uuids]
    ents = [e for e in ents if e]
    levers = []       # 可收窄对象
    hard = False      # 存在无杠杆且必须动的
    for kind, obj in ents:
        if kind != "track":
            hard = True
            continue
        w = obj.GetWidth() / MM
        net = obj.GetNetname()
        floor = POWER_FLOOR if any(
            net.upper().startswith(p) for p in POWER_NETS) \
            or net.upper() in POWER_NETS else RUNGS[-1]
        levers.append((obj, w, floor))
    if not levers:
        needs_nudge.append({
            "nets": it.get("nets"), "actual": it.get("actual_mm"),
            "pos": it.get("pos_mm"), "verdict": it.get("verdict"),
            "why": "no width lever (via/pad pair) — gap_nudge/swap"})
        continue
    # 级联收窄到预测增益 >= shortfall
    gain = 0.0
    steps = []
    while gain < shortfall - 1e-9:
        moved = False
        for i in range(len(levers)):
            obj, w, floor = levers[i]
            nr = next_rung(w, floor)
            if nr is None:
                continue
            gain += (w - nr) / 2
            steps.append("%.3f->%.3f" % (w, nr))
            obj.SetWidth(int(nr * MM))
            levers[i] = (obj, nr, floor)     # 级联继续用
            moved = True
            if gain >= shortfall - 1e-9:
                break
        if not moved:
            break
    if gain >= shortfall - 1e-9:
        narrowed += 1
        items_out.append({"nets": it.get("nets"),
                          "actual": it.get("actual_mm"),
                          "gain": round(gain, 4), "steps": steps,
                          "done": True})
    else:
        items_out.append({"nets": it.get("nets"),
                          "actual": it.get("actual_mm"),
                          "gain": round(gain, 4), "steps": steps,
                          "done": False,
                          "why": "width floor reached — residue %.4fmm"
                                 % (shortfall - gain)})

b.Save(str(pcb_path))
print(json.dumps({"narrowed": narrowed, "items": items_out,
                  "needs_nudge": needs_nudge}, ensure_ascii=False))
