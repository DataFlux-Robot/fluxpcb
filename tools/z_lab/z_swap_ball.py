# -*- coding: utf-8 -*-
"""z_swap_ball — 换球手术(C2 路): net 从旧球迁到新球(板级).

战理(CEO 定案的正确粒度): 单网换球只动它自己的连接 ——
  旧球 pad 摘除网络(留空球), 新球 pad 挂上该网络.
  走线交给后续逃逸/收割(z_escape_surg / z_seg_harvest).
  设计一致性: 同步写 FC_PIN_OVERRIDE.json(fcfull 的 C4 契约通道,
  schematic/firmware 读它对齐 —— 板文件是加工事实源).

用法: z_swap_ball.py BOARD_DIR NET NEW_BALL
返回: {"swapped": true, "from": T1, "to": N5, ...}
"""
import json
import subprocess
import sys
from pathlib import Path

KIPY = 'python'
board_dir = Path(sys.argv[1])
net_name = sys.argv[2]
new_ball = sys.argv[3]
pcb = sorted(board_dir.glob('*.kicad_pcb'))[0]
REPO = Path('E:/DATA/vscode/ZCODE/flux-neurotransmitter')
PROJ = REPO / 'projects' / 'h743-fc'

r = subprocess.run(
    [KIPY, "-c", r'''
import pcbnew, sys, json
b = pcbnew.LoadBoard(sys.argv[1])
net_name, new_ball = sys.argv[2], sys.argv[3]
net = b.FindNet(net_name)
if net is None:
    print(json.dumps({"swapped": false, "why": "net not found"}))
    raise SystemExit(0)
old_ball, old_xy, new_xy = None, None, None
for fp in b.GetFootprints():
    if not fp.GetReference().startswith("U"):
        continue
    for pad in fp.Pads():
        num = pad.GetNumber()
        if num == new_ball:
            if pad.GetNetCode() != 0 and pad.GetNetname() != net_name:
                print(json.dumps({"swapped": false,
                                  "why": "new ball occupied: " + pad.GetNetname()}))
                raise SystemExit(0)
            pad.SetNet(net)
            p = pad.GetPosition()
            new_xy = [round(p.x / 1e6, 3), round(p.y / 1e6, 3)]
        if pad.GetNetname() == net_name and num != new_ball:
            old_ball = num
            p = pad.GetPosition()
            old_xy = [round(p.x / 1e6, 3), round(p.y / 1e6, 3)]
            pad.SetNetCode(0)      # 摘网留空球
b.Save(sys.argv[1])
print(json.dumps({"swapped": True, "from": old_ball, "to": new_ball,
                  "old_xy": old_xy, "new_xy": new_xy}))
''', str(pcb), net_name, new_ball],
    capture_output=True, text=True, encoding="utf-8", errors="replace",
    timeout=300)
tail = [l for l in (r.stdout or "").splitlines() if l.strip().startswith("{")]
res = json.loads(tail[-1]) if tail else {"swapped": False,
                                         "why": (r.stderr or "")[-120:]}
# 设计一致性: FC_PIN_OVERRIDE.json (fcfull C4 契约)
if res.get("swapped"):
    ov_path = PROJ / "FC_PIN_OVERRIDE.json"
    ov = {}
    if ov_path.exists():
        try:
            ov = json.loads(ov_path.read_text(encoding="utf-8"))
        except ValueError:
            ov = {}
    ov[net_name] = new_ball
    ov_path.write_text(json.dumps(ov, ensure_ascii=False, indent=1),
                       encoding="utf-8")
    res["override"] = ov
print(json.dumps(res, ensure_ascii=False))
