# -*- coding: utf-8 -*-
"""z_uart8_shrink — UART8_RX 近失手术 v2: 收窄 NRST 堵线宽度.

推线被连通契约拒(anchor 脱离) -> 换刀: 宽度收窄是连通保持变换
(半宽差直接换间距): 0.2->0.15 得 0.025mm = 5μm 缺口的 5 倍余量。
用法: z_uart8_shrink.py [BOARD_DIR=FC_L13]
"""
import json
import subprocess
import sys
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
rep = {"ts": round(__import__("time").time()), "steps": []}

q0 = kstate.query(kicad_cli, kipy, pcb, "opens", consolidate=True)
rep["opens_before"] = q0.value
print("[shrink] 起点 opens=%d" % q0.value, flush=True)

# 1. 收窄 NRST@(29.6,38.24) 周边走线一级(0.2->0.15, 连通保持)
r = subprocess.run(
    [KIPY, "-c", """
import pcbnew, sys
b = pcbnew.LoadBoard(sys.argv[1])
MM = 1000000
BX, BY = 29.6, 38.24
n = 0
for t in b.GetTracks():
    if type(t).__name__ != 'PCB_TRACK':
        continue
    s, e = t.GetStart(), t.GetEnd()
    mx, my = (s.x + e.x) / 2 / MM, (s.y + e.y) / 2 / MM
    if abs(mx - BX) < 1.2 and abs(my - BY) < 1.2:
        w = t.GetWidth() / MM
        if w > 0.15:
            t.SetWidth(int(0.15 * MM))
            n += 1
b.Save(sys.argv[1])
print('{\"shrunk\": %d}' % n)
""", str(pcb)],
    capture_output=True, text=True, encoding="utf-8", errors="replace",
    timeout=300)
tail = [l for l in (r.stdout or "").splitlines()
        if l.strip().startswith("{")]
rep["steps"].append({"step": "shrink",
                     "out": tail[-1] if tail else (r.stderr or "")[-80:]})
print("[shrink] 收窄 <- %s" % (tail[-1] if tail else r.stderr[-80:]),
      flush=True)

# 2. 重探 UART8_RX
r = subprocess.run(
    [KIPY, str(KG / "geomir.py"), str(pcb), "UART8_RX",
     str(LAB / "_uart8_reprobe"), str(bd / "loop_pairs.tmp.txt")],
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
print("[shrink] 重探 paths=%d" % n_paths, flush=True)

# 3. 有路即收割
if n_paths > 0:
    r = subprocess.run(
        [sys.executable, str(LAB / "z_harvest.py"), str(bd), "UART8_RX"],
        capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=2400)
    tail = [l for l in (r.stdout or "").splitlines()
            if l.strip().startswith("{")]
    rep["steps"].append({"step": "harvest",
                         "out": tail[-1] if tail else "无输出"})
    print("[shrink] 收割 <- %s" % (tail[-1] if tail else "无输出"), flush=True)

q1 = kstate.query(kicad_cli, kipy, pcb, "opens", consolidate=True)
rep["opens_after"] = q1.value
(LAB / "z_uart8_report.json").write_text(
    json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
print("[shrink] 终态 opens=%d(起点 %d)" % (q1.value, q0.value), flush=True)
