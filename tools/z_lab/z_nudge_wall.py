# -*- coding: utf-8 -*-
"""z_nudge_wall — 挪墙原语(复合算子的胚胎): 位移指定网的邻近线段.

战理(靶2b-2 动端点不动走廊的近亲): 多墙形态里墙常是活铜不能撕,
但可以整体平移几丝毫米让出走廊 —— 本案: GND 闭合铜把 UART4_TX
焊盘焊死在盒子里, 挪 0.25mm 开盒, via 就有位。

用法: z_nudge_wall.py BOARD_DIR NET X Y DX DY [R=1.0]
机制: 取 NET 在 (X,Y) 半径 R 内的所有线段/过孔, 平移 (DX,DY)。
     原样不动其他铜。挪后不做验证 —— 验证是调用方(复合序列)的事:
     kstate opens 对账 + DRC, 净收益才落板。

返回: {"moved": n, "items": [...]}
"""
import json
import subprocess
import sys
from pathlib import Path

KIPY = 'python'
board_dir = Path(sys.argv[1])
net = sys.argv[2]
X, Y = float(sys.argv[3]), float(sys.argv[4])
DX, DY = float(sys.argv[5]), float(sys.argv[6])
R = float(sys.argv[7]) if len(sys.argv) > 7 else 1.0
pcb = sorted(board_dir.glob('*.kicad_pcb'))[0]

r = subprocess.run(
    [KIPY, "-c", r'''
import pcbnew, sys, json
b = pcbnew.LoadBoard(sys.argv[1])
net, X, Y, DX, DY, R = (sys.argv[2], float(sys.argv[3]), float(sys.argv[4]),
                        float(sys.argv[5]), float(sys.argv[6]), float(sys.argv[7]))
MM = 1000000
moved = []
def near(px, py):
    return ((px - X) ** 2 + (py - Y) ** 2) ** 0.5 <= R
for t in list(b.GetTracks()):
    if t.GetNetname() != net:
        continue
    cls = type(t).__name__
    if cls == 'PCB_VIA':
        p = t.GetPosition()
        px, py = p.x / MM, p.y / MM
        if near(px, py):
            t.SetPosition(pcbnew.VECTOR2I(int((px + DX) * MM), int((py + DY) * MM)))
            moved.append(['VIA', round(px, 3), round(py, 3)])
    else:
        s, e = t.GetStart(), t.GetEnd()
        ax, ay, bx, by = s.x / MM, s.y / MM, e.x / MM, e.y / MM
        if near(ax, ay) and near(bx, by):
            t.SetStart(pcbnew.VECTOR2I(int((ax + DX) * MM), int((ay + DY) * MM)))
            t.SetEnd(pcbnew.VECTOR2I(int((bx + DX) * MM), int((by + DY) * MM)))
            moved.append(['TRK', round(ax, 3), round(ay, 3),
                          round(bx, 3), round(by, 3)])
        elif near(ax, ay) or near(bx, by):
            # 单端在圈内: 只挪该端(保持另端连通)
            if near(ax, ay):
                t.SetStart(pcbnew.VECTOR2I(int((ax + DX) * MM), int((ay + DY) * MM)))
            else:
                t.SetEnd(pcbnew.VECTOR2I(int((bx + DX) * MM), int((by + DY) * MM)))
            moved.append(['TRK-HALF', round(ax, 3), round(ay, 3),
                          round(bx, 3), round(by, 3)])
b.Save(sys.argv[1])
print(json.dumps({'moved': len(moved), 'items': moved}))
''', str(pcb), net, str(X), str(Y), str(DX), str(DY), str(R)],
    capture_output=True, text=True, encoding="utf-8", errors="replace",
    timeout=300)
tail = [l for l in (r.stdout or "").splitlines() if l.strip().startswith("{")]
print(tail[-1] if tail else json.dumps(
    {"moved": 0, "error": (r.stderr or "")[-120:]}))
