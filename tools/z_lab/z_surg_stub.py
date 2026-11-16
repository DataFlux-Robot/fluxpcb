# -*- coding: utf-8 -*-
"""z_surg_stub — 死残段定点切除(无损): 拆墙败局遗留的孤段+孤孔.

战理: 拆死残段 ≠ 拆走廊 —— 死铜不承载连接, 切除即无损.
本案: GND 死对端点(19.6,30.4)被 UART4_TX(网码68)死残段
      (19.6,29.6)->(20.0,30.0)+孤孔(20.0,30.0) 封死 via-attach.
      两次半径撕(0.35/中点框)都漏: 段距端点 0.566mm > 半径.
      定点按冲突报告的网码+坐标精确切除, 不殃及邻网(NRST 0.512/I2C2 0.512 在刀口外).

用法: z_surg_stub.py BOARD_DIR
验收: opens 不升(死铜) + GND 死对解锁(随后 z_harvest GND 验证).
"""
import json
import subprocess
import sys
from pathlib import Path

LAB = Path("E:/DATA/vscode/ZCODE/z_lab")
KIPY = "python"
BOARD = Path(sys.argv[1])
pcb = sorted((BOARD / "pcb" / "board").glob("*.kicad_pcb"))[0]

r = subprocess.run(
    [KIPY, "-c", r"""
import pcbnew, sys, json
b = pcbnew.LoadBoard(sys.argv[1])
MM = 1000000
NETCODE = 68                    # UART4_TX(冲突报告给的网码)
SEG = ((19.6, 29.6), (20.0, 30.0))   # 死残段(精确坐标匹配)
VIA = (20.0, 30.0)              # 孤孔
TOL = 0.02
removed = []
for t in list(b.GetTracks()):
    if t.GetNetCode() != NETCODE:
        continue
    cls = type(t).__name__
    if cls == 'PCB_VIA':
        p = t.GetPosition()
        px, py = p.x / MM, p.y / MM
        if abs(px - VIA[0]) < TOL and abs(py - VIA[1]) < TOL:
            removed.append(['VIA', round(px, 3), round(py, 3)])
            b.Remove(t)
    elif cls == 'PCB_TRACK':
        s, e = t.GetStart(), t.GetEnd()
        a = (s.x / MM, s.y / MM)
        c = (e.x / MM, e.y / MM)
        fwd = (abs(a[0]-SEG[0][0]) < TOL and abs(a[1]-SEG[0][1]) < TOL
               and abs(c[0]-SEG[1][0]) < TOL and abs(c[1]-SEG[1][1]) < TOL)
        rev = (abs(c[0]-SEG[0][0]) < TOL and abs(c[1]-SEG[0][1]) < TOL
               and abs(a[0]-SEG[1][0]) < TOL and abs(a[1]-SEG[1][1]) < TOL)
        if fwd or rev:
            removed.append(['TRACK', round(a[0],3), round(a[1],3),
                            round(c[0],3), round(c[1],3)])
            b.Remove(t)
b.Save(sys.argv[1])
print(json.dumps({'removed': removed}))
""", str(pcb)],
    capture_output=True, text=True, encoding="utf-8", errors="replace",
    timeout=300)
tail = [l for l in (r.stdout or "").splitlines() if l.strip().startswith("{")]
print("[surg] 切除 <- %s" % (tail[-1] if tail else (r.stderr or "")[-200:]),
      flush=True)
rep = {"board": str(BOARD)}
if tail:
    rep["surg"] = json.loads(tail[-1])
(LAB / "z_surg_stub_report.json").write_text(
    json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
