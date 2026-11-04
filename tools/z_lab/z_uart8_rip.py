# -*- coding: utf-8 -*-
"""z_uart8_rip — UART8_RX 精准拆墙(多墙形态的定点 rip+FR).

重探判决: 4 堵点(GND@30.8,26 短0.45 / NRST@29.6,38.24 短0.005(疑过孔) /
3V3@24.4,26.8 短0.45 / VCAP@32.24,33 短0.344)。推线拒、收窄不够 ->
定点撕 4 堵点(走线+过孔都撕) -> route_session 重布 -> 重探 -> 收割。
与 SDMMC 失败案的区别: 那是无走廊多重锁死; 此处走廊存在(候选仅差
0.005-0.45), 拆墙即可能通行。
用法: z_uart8_rip.py [BOARD_DIR=FC_L13]
"""
import json
import subprocess
import sys
import time
from pathlib import Path

LAB = Path("E:/DATA/vscode/ZCODE/z_lab")
REPO = Path("E:/DATA/vscode/ZCODE/flux-neurotransmitter")
sys.path.insert(0, str(REPO))
KIPY = "C:/Users/zhang/AppData/Local/Programs/KiCad/10.0/bin/python.exe"
KG = REPO / "fluxpcb" / "kicadgen"
from fluxpcb.routing import find_kicad          # noqa: E402
from fluxpcb.kernel import state as kstate      # noqa: E402

BOARD = Path(sys.argv[1]) if len(sys.argv) > 1 else LAB / "z_v/FC_L13"
bd = BOARD / "pcb" / "board"
pcb = sorted(bd.glob("*.kicad_pcb"))[0]
kicad_cli, kipy = find_kicad()
rep = {"ts": round(time.time()), "steps": []}

q0 = kstate.query(kicad_cli, kipy, pcb, "opens", consolidate=True)
s0 = kstate.query(kicad_cli, kipy, pcb, "shorts", consolidate=True)
rep["opens_before"] = q0.value
rep["shorts_before"] = s0.value
print("[rip] 起点 opens=%d shorts=%d" % (q0.value, s0.value), flush=True)

# 1. 定点撕堵点(走线+过孔, 0.4mm 半径): UART8 四墙 + GND 对的
#    net68 端点堵(同病合并施工)
WALLS = [(30.8, 26.0), (29.6, 38.24), (24.4, 26.8), (32.24, 33.0),
         (19.6, 30.4)]
r = subprocess.run(
    [KIPY, "-c", """
import pcbnew, sys, json
b = pcbnew.LoadBoard(sys.argv[1])
MM = 1000000
WALLS = %s
ripped = []
for t in list(b.GetTracks()):
    cls = type(t).__name__
    if cls not in ('PCB_TRACK', 'PCB_VIA'):
        continue
    p = t.GetPosition() if cls == 'PCB_VIA' else None
    if cls == 'PCB_TRACK':
        s, e = t.GetStart(), t.GetEnd()
        mx, my = (s.x + e.x) / 2 / MM, (s.y + e.y) / 2 / MM
    else:
        mx, my = p.x / MM, p.y / MM
    for (wx, wy) in WALLS:
        if abs(mx - wx) < 0.4 and abs(my - wy) < 0.4:
            ripped.append([cls, t.GetNetname(), round(mx, 2), round(my, 2)])
            b.Remove(t)
            break
b.Save(sys.argv[1])
print(json.dumps({'ripped': ripped}))
""" % json.dumps(WALLS), str(pcb)],
    capture_output=True, text=True, encoding="utf-8", errors="replace",
    timeout=300)
tail = [l for l in (r.stdout or "").splitlines()
        if l.strip().startswith("{")]
rep["steps"].append({"step": "rip",
                     "out": tail[-1] if tail else (r.stderr or "")[-100:]})
print("[rip] 拆墙 <- %s" % (tail[-1] if tail else (r.stderr or "")[-100:]),
      flush=True)

# 2. route_session 重布(拆掉的网)
r = subprocess.run(
    [KIPY, str(KG / "route_session.py"), str(pcb), "", "12"],
    capture_output=True, text=True, encoding="utf-8", errors="replace",
    timeout=2400)
tail = [l for l in (r.stdout or "").splitlines()
        if l.strip().startswith("{")]
rep["steps"].append({"step": "reroute",
                     "out": tail[-1] if tail else "无输出"})
print("[rip] 重布 <- %s" % (tail[-1] if tail else "无输出"), flush=True)

# 3. 重探 UART8
r = subprocess.run(
    [KIPY, str(KG / "geomir.py"), str(pcb), "UART8_RX",
     str(LAB / "_uart8_reprobe3"), str(bd / "loop_pairs.tmp.txt")],
    capture_output=True, text=True, encoding="utf-8", errors="replace",
    timeout=1800)
n_paths = 0
for ln in (r.stdout or "").splitlines():
    ln = ln.strip()
    if ln.startswith("{") and '"rejects"' in ln:
        try:
            n_paths = len(json.loads(ln).get("paths") or [])
        except ValueError:
            pass
rep["steps"].append({"step": "reprobe", "paths": n_paths})
print("[rip] 重探 paths=%d" % n_paths, flush=True)

# 4. 有路即收割
if n_paths > 0:
    r = subprocess.run(
        [sys.executable, str(LAB / "z_harvest.py"), str(bd), "UART8_RX"],
        capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=2400)
    tail = [l for l in (r.stdout or "").splitlines()
            if l.strip().startswith("{")]
    rep["steps"].append({"step": "harvest",
                         "out": tail[-1] if tail else "无输出"})
    print("[rip] 收割 <- %s" % (tail[-1] if tail else "无输出"), flush=True)

q1 = kstate.query(kicad_cli, kipy, pcb, "opens", consolidate=True)
s1 = kstate.query(kicad_cli, kipy, pcb, "shorts", consolidate=True)
rep["opens_after"] = q1.value
rep["shorts_after"] = s1.value
rep["PASS"] = bool(q1.value < q0.value and s1.value <= s0.value)
(LAB / "z_uart8_rip_report.json").write_text(
    json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
print("[rip] 终态 opens=%d(起点 %d) shorts=%d — %s"
      % (q1.value, q0.value, s1.value,
         "PASS" if rep["PASS"] else "FAIL"), flush=True)
