# -*- coding: utf-8 -*-
"""Run inside KiCad's python: import SES with a watchdog thread that closes
any modal popup (the headless SES-import info dialog blocks the call)."""
import ctypes
import ctypes.wintypes as wt
import threading
import time

import pcbnew

BOARD = r"E:\DATA\vscode\ZCODE\fluxpcb\projects\hpm6e80-core\pcb\carrier\hpm6e80-carrier.kicad_pcb"
SES = r"E:\DATA\vscode\ZCODE\fluxpcb\projects\hpm6e80-core\pcb\carrier\carrier.ses"

user32 = ctypes.windll.user32
WM_CLOSE = 0x0010
WM_RETURN = 0x0D


def _close_popups():
    while True:
        time.sleep(0.4)
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            continue
        n = user32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, buf, n + 1)
        title = buf.value
        # close any KiCad message box that appears
        if title and ("KiCad" in title or "pcbnew" in title or "信息" in title
                      or "Specctra" in title or "警告" in title or "错误" in title):
            user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)


t = threading.Thread(target=_close_popups, daemon=True)
t.start()

print("loading board", flush=True)
b = pcbnew.LoadBoard(BOARD)
print("importing ses", flush=True)
ok = pcbnew.ImportSpecctraSES(b, SES)
print("import result:", ok, flush=True)
print("saving", flush=True)
b.Save(BOARD)
print("saved", flush=True)
