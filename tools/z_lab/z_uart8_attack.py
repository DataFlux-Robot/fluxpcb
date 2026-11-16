# -*- coding: utf-8 -*-
"""z_uart8_attack — UART8_RX 近失手术(5μm 级差, nudge 精确射程).

探图裁决: NRST@(29.6,38.24) 短 0.005mm(近失) + GND@(30.8,26) 短 0.45。
用法: z_uart8_attack.py [BOARD_DIR=FC_L13]
步骤: gap_nudge NRST 双侧推 0.1mm -> 重探 UART8_RX(geomir) ->
      有路即 z_harvest 收割 -> kstate 记账。
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
rep["opens_before"] = q0.value
print("[uart8] 起点 opens=%d" % q0.value, flush=True)

# 1. NRST@(29.6,38.24) 双侧推 0.1mm(走廊垂直向两侧试)
BX, BY = 29.6, 38.24
for (dx, dy, tag) in ((0.97, -0.25, "右下"), (-0.97, 0.25, "左上")):
    r = subprocess.run(
        [KIPY, str(KG / "gap_nudge.py"), str(pcb),
         str(BX), str(BY), str(dx), str(dy), "0.1"],
        capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=300)
    tail = [l for l in (r.stdout or "").splitlines()
            if l.strip().startswith("{")]
    out = tail[-1] if tail else ("崩溃:" + (r.stderr or "")[-80:])
    rep["steps"].append({"step": "nudge-" + tag, "out": out})
    print("[uart8] nudge %s <- %s" % (tag, out), flush=True)
    try:
        if json.loads(tail[-1]).get("nudged"):
            break    # 一侧成功即够(5μm 缺口)
    except (ValueError, IndexError):
        pass

# 2. 重探: geomir 出图 UART8_RX(只读)
r = subprocess.run(
    [KIPY, str(KG / "geomir.py"), str(pcb), "UART8_RX",
     str(LAB / "_uart8_reprobe"), str(bd / "loop_pairs.tmp.txt")],
    capture_output=True, text=True, encoding="utf-8",
    errors="replace", timeout=1800)
n_paths = 0
for ln in (r.stdout or "").splitlines():
    ln = ln.strip()
    if ln.startswith("{") and '"rejects"' in ln:
        try:
            n_paths = len(json.loads(ln).get("paths") or [])
        except ValueError:
            pass
rep["steps"].append({"step": "reprobe", "paths": n_paths})
print("[uart8] 重探 paths=%d" % n_paths, flush=True)

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
    print("[uart8] 收割 <- %s" % (tail[-1] if tail else "无输出"), flush=True)

q1 = kstate.query(kicad_cli, kipy, pcb, "opens", consolidate=True)
rep["opens_after"] = q1.value
(LAB / "z_uart8_report.json").write_text(
    json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
print("[uart8] 终态 opens=%d(起点 %d)" % (q1.value, q0.value), flush=True)
