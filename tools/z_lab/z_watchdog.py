# -*- coding: utf-8 -*-
"""z_watchdog — 流派五: 无人值守韧性层(训练环看门狗).

用法: z_watchdog.py   (一条后台长跑; 全部完成自停)
每 5 分钟: 对监护清单逐项 —— 完成则跳过, 进程死了则断点续跑重启。
断点续跑由各驱动内建(轮级/板级缓存), 重启无损。
环境性清场(机器重启/进程终止)后本进程也会死 —— 重开一条即可,
它会从"完成态检查"接着走。
"""
import json
import subprocess
import sys
import time
from pathlib import Path

LAB = Path("E:/DATA/vscode/ZCODE/z_lab")
KIPY = "python"          # z_lab 驱动用系统 python(kernel 层自带路径)
POLL_S = 300

JOBS = [
    {
        "name": "rsi",
        "cmd": [KIPY, "-u", str(LAB / "z_rsi_round.py"),
                str(LAB / "z_v/FC_L13"), "3", "8"],
        "log": LAB / "_rsi_run.log",
        "done": lambda: _curve_done(),
    },
    {
        "name": "arena",
        "cmd": [KIPY, "-u", str(LAB / "z_arena.py"),
                str(LAB / "cand_mech-harvest.json"), "4", "--natural"],
        "log": LAB / "_arena_run.log",
        "done": lambda: _arena_done(),
    },
    {
        "name": "evo",
        "cmd": [KIPY, "-u", str(LAB / "z_evo.py"), "1",
                str(LAB / "z_v/FC_L13")],
        "log": LAB / "_evo_run.log",
        "done": lambda: (LAB / "evo_gen_1.json").exists(),
    },
    {
        "name": "clear",
        "cmd": [KIPY, "-u", str(LAB / "z_agent_loop.py")],
        "log": LAB / "_agent_loop.log",
        "done": lambda: _clear_done(),
    },
]


def _clear_done():
    """层1 收工=清零/仅剩GAP/stall/max —— stall 停机是层2的事, 不重启."""
    try:
        d = json.loads((LAB / "z_agent_loop_report.json").read_text(
            encoding="utf-8"))
        return bool(d.get("only_hard_left")
                    or d.get("done") in ("cleared", "only_gaps_left",
                                         "stall", "max_rounds"))
    except (OSError, ValueError):
        return False


def _curve_done():
    try:
        d = json.loads((LAB / "z_rsi_curve.json").read_text(
            encoding="utf-8"))
        rs = d.get("rounds") or []
        return (d.get("verdict") is not None
                or sum(1 for r in rs if (r.get("test") or {}).get(
                    "steps") is not None) >= 3)
    except (OSError, ValueError):
        return False


def _arena_done():
    try:
        d = json.loads((LAB / "arena_report.json").read_text(
            encoding="utf-8"))
        sv = (d.get("survival") or {}).get("mech-harvest") or {}
        return bool(sv.get("per_board")) and len(
            sv.get("per_board") or {}) >= 7
    except (OSError, ValueError):
        return False


handles = {}
print("[watchdog] 启动, 监护 %d 项" % len(JOBS), flush=True)
while True:
    alive = 0
    for job in JOBS:
        if job["done"]():
            continue
        alive += 1
        h = handles.get(job["name"])
        if h is not None and h.poll() is None:
            continue        # 还活着
        # 死了/没起 -> 断点重启(日志追加, 保留历史)
        f = open(job["log"], "a", encoding="utf-8")
        h = subprocess.Popen(job["cmd"], stdout=f, stderr=f)
        handles[job["name"]] = h
        print("[watchdog] %s 重启 pid=%s" % (job["name"], h.pid),
              flush=True)
    if alive == 0:
        print("[watchdog] 全部完成, 自停", flush=True)
        break
    time.sleep(POLL_S)
