# -*- coding: utf-8 -*-
"""z_keepalive — watchdog 自托管(自举守护): 谁来守护守护者.

注册为计划任务(每小时+登录时), 检查 z_watchdog.py 是否存活,
不在则后台拉起 —— 机器重启/进程清场后无人值守自动续上.
幂等: 已在跑则什么都不做. 用法: z_keepalive.py
"""
import os
import subprocess
import sys
import time
from pathlib import Path

LAB = Path("E:/DATA/vscode/ZCODE/z_lab")
FLAG = LAB / "_keepalive.log"


def log(msg):
    line = "%s %s\n" % (time.strftime("%m-%d %H:%M:%S"), msg)
    try:
        with open(FLAG, "a", encoding="utf-8") as f:
            f.write(line)
    except OSError:
        pass


def watchdog_alive():
    """Windows: PowerShell CIM 查命令行含 z_watchdog.py 的 python 进程.
    (wmic 已在 Win11 移除 —— 第一版用 wmic 探测必假阴性, 会重复拉起)"""
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_Process -Filter \"name='python.exe'\""
             " | ForEach-Object { $_.CommandLine }"],
            capture_output=True, text=True, timeout=60)
        return "z_watchdog.py" in (r.stdout or "")
    except Exception:
        return False          # 查不到=宁可重复拉(幂等由 flag 承担)


def main():
    if watchdog_alive():
        log("alive")
        return 0
    log("dead -> 拉起")
    f = open(LAB / "_watchdog_keepalive.log", "a", encoding="utf-8")
    subprocess.Popen([sys.executable, "-u", str(LAB / "z_watchdog.py")],
                     stdout=f, stderr=f, cwd=str(LAB),
                     creationflags=0x00000008 | 0x00000200)  # detached
    return 0


if __name__ == "__main__":
    sys.exit(main())
