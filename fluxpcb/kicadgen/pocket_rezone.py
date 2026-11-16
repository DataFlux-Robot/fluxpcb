# -*- coding: utf-8 -*-
"""pocket_carve_rezone — 铜配额重谈判(P1: 口袋墙的结构解).

口袋墙形态学判定: 走廊容量 < 铜密度, 局部算子(挪/撕/胀/挪件)全部
打不动 —— 本工具不凿墙, **重建街区**:
  1. 撤回: 撕除口袋窗(pair 走廊 ±WIN mm)内全部铜(含堵墙走线);
  2. 重划: 窗内全部相关网(目标网+切割信号)按逃逸优先序整体重布
     (route_session ONLY_NETS 白名单模式);
  3. 门禁: 调用方(_gated_step)裁决 objective/回滚。

Usage: pocket_rezone.py PCB NET [WIN_MM]
Prints: {"ripped": n, "routed": bool, "nets": [...], "pair": [...]}
"""
import json
import subprocess
import sys
from pathlib import Path

import pcbnew

MM = 1000000
pcb_path = Path(sys.argv[1])
net_name = sys.argv[2] if len(sys.argv) > 2 else ""
WIN = float(sys.argv[3]) if len(sys.argv) > 3 else 3.0

b = pcbnew.LoadBoard(str(pcb_path))

# 1. 找目标开路对(pairs 文件优先, 退回岛锚)
# ④ 单源: 解析走 geomir.pairs_by_net(格式 net|x|y|l|x|y|l, 端点在
# (1,2)/(4,5)) —— 手搓列切分已两次埋契约错位(球位键断裂同类)
import geomir
pairs_file = pcb_path.parent / "ratsnest_pairs.txt"
pair = None
region_nets = set()
if pairs_file.exists():
    for a, bpt in geomir.pairs_by_net(
            pairs_file.read_text(encoding="utf-8").splitlines()
    ).get(net_name, []):
        pair = (a, bpt)
        break
if pair is None:
    b.BuildConnectivity()
    islands = geomir.islands_of(
        b, b.FindNet(net_name).GetNetCode())
    if len(islands) >= 2:
        pair = (tuple(islands[0]["anchor"]),
                tuple(islands[1]["anchor"]))
if pair is None:
    print(json.dumps({"ripped": 0, "routed": False,
                      "error": "no open pair"}))
    sys.exit(0)

(ax, ay), (bx, by) = pair
x0, x1 = min(ax, bx) - WIN, max(ax, bx) + WIN
y0, y1 = min(ay, by) - WIN, max(ay, by) + WIN
x0, x1 = min(ax, bx) - WIN, max(ax, bx) + WIN
y0, y1 = min(ay, by) - WIN, max(ay, by) + WIN

# 2. 撤回-重划集合守恒(首跑教训: 撕 392 段只重布 8 网 -> 10 开
# 变 52 开, 被撕却不重布的网全是新伤口。集合必须对称:
# **只撕将要重布的网**, 且白名单不截断撕取面)
from routegrid import seg_seg_dist
MAX_REZONE = 12    # 重划网数上限(走廊配额: 超了说明窗口过大)

# 2a. 预扫: 走廊 ±WIN 带内铜按网归集(不动板)
cand = {}
for t in b.GetTracks():
    s, e = t.GetStart(), t.GetEnd()
    d = seg_seg_dist((ax, ay), (bx, by),
                     (s.x / MM, s.y / MM), (e.x / MM, e.y / MM))
    if d <= WIN:
        cand.setdefault(t.GetNetname(), []).append(t)

# 2b. 白名单: 目标网必入, 其余按占铜量降序(重谈判的"配额"语义:
# 走廊内占铜最多的网先让位)
others = sorted((n for n in cand if n and n != net_name),
                key=lambda n: -len(cand[n]))
targets = [net_name] + others[:MAX_REZONE - 1]
ripped = 0
for n in targets:
    for t in cand.get(n, []):
        b.Remove(t)
        ripped += 1
b.Save(str(pcb_path))

# 3. 重划: 白名单全量重布(不截断 —— 撕了就必须布回)
routed = False
route_report = ""
try:
    r = subprocess.run(
        [sys.executable, str(Path(__file__).parent / "route_session.py"),
         str(pcb_path), "", "12", ",".join(targets)],
        capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=1800)
    routed = r.returncode == 0
    # session 终行 JSON(closed/remaining)是验证视图, 一并报出
    for ln in reversed((r.stdout or "").splitlines()):
        if ln.strip().startswith("{"):
            route_report = ln.strip()
            break
except (OSError, subprocess.TimeoutExpired):
    routed = False

print(json.dumps({"ripped": ripped, "routed": routed,
                  "nets": targets, "pair": pair,
                  "route": route_report,
                  "window": [x0, y0, x1, y1]}))
