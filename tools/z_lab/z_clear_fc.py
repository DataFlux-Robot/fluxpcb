# -*- coding: utf-8 -*-
"""z_clear_fc — 飞控清线战役 Track A(交付主轴): 7 条旧弹药逐对收割.

用法: z_clear_fc.py [BOARD_DIR=FC_L13]
步骤(每循环): 实时 query 定名单(z_harvest 内建单源, 天然防 stale)
  -> 3V3/GND 逐对收割(闭合证书入 certs.jsonl) -> kstate 记 opens 轨迹
停止: 旧弹药网全退出 live 名单, 或 12 循环上限(诚实列余量)。
产出: z_lab/z_clear_fc_report.json(10->0 曲线即交付进度)。
"""
import json
import subprocess
import sys
import time
from pathlib import Path

LAB = Path("E:/DATA/vscode/ZCODE/z_lab")
REPO = Path("E:/DATA/vscode/ZCODE/flux-neurotransmitter")
sys.path.insert(0, str(REPO))
from fluxpcb.routing import find_kicad          # noqa: E402
from fluxpcb.kernel import state as kstate      # noqa: E402

BOARD = Path(sys.argv[1]) if len(sys.argv) > 1 else LAB / "z_v/FC_L13"
pcb = sorted((BOARD / "pcb" / "board").glob("*.kicad_pcb"))[0]
kicad_cli, kipy = find_kicad()
OLD_AMMO = ("3V3", "GND")     # val/test split, 与训练窗无冲突

report = {"board": str(BOARD), "trajectory": [], "ts": round(time.time())}


def _opens():
    q = kstate.query(kicad_cli, kipy, pcb, "opens", consolidate=True)
    return q.value, set((q.detail or {}).get("pairs") or [])


o0, pairs0 = _opens()
if o0 < 0:
    # query 失败=未知, 绝不开工(假'清光'教训: 崩溃的空名单被误判完成)
    print("[clear] 起点测量失败(opens=-1), 中止 — 重试或修仪器", flush=True)
    sys.exit(1)
report["opens_start"] = o0
print("[clear] 起点 opens=%d" % o0, flush=True)

for it in range(20):
    o, pairs = _opens()
    if o < 0:
        print("[clear] iter%d 测量失败, 跳过本轮(不计清光)" % it, flush=True)
        continue
    nets = {p.split("|")[0] for p in pairs}
    # 靶序: 旧弹药优先, 其余 live 网(含 rip 碎片 NRST/UART4_TX)殿后
    # —— 纯收割线零附带损伤(rpc 战训: rip 留碎片, 收割不留)
    live_ammo = [n for n in OLD_AMMO if n in nets] + \
        [n for n in sorted(nets) if n not in OLD_AMMO]
    report["trajectory"].append({"iter": it, "opens": o,
                                 "nets": sorted(nets)})
    if not live_ammo:
        print("[clear] 全部清光, opens=%d" % o, flush=True)
        break
    net = live_ammo[0]
    r = subprocess.run(
        [sys.executable, str(LAB / "z_harvest.py"),
         str(BOARD / "pcb" / "board"), net],
        capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=2400)
    tail = [l for l in (r.stdout or "").splitlines()
            if l.strip().startswith("{")]
    if not tail:
        # 响亮: 空输出=崩溃, stderr 必须入账(静默吞错教训)
        print("[clear] %s <- 崩溃: %s" % (net, (r.stderr or "")[-120:]),
              flush=True)
    else:
        print("[clear] %s <- %s" % (net, tail[-1]), flush=True)

o1, pairs1 = _opens()
report["opens_end"] = o1
report["closed_pairs"] = sorted(pairs0 - pairs1)
report["new_pairs"] = sorted(pairs1 - pairs0)
# 收工判据(看门狗用): 清零 或 仅剩硬对(无旧弹药/碎片可收)
_last_nets = {p.split("|")[0] for p in pairs1} if o1 >= 0 else set()
report["only_hard_left"] = bool(
    o1 == 0 or _last_nets <= {"SDMMC1_CMD", "USART6_RX", "UART8_RX"})
(LAB / "z_clear_fc_report.json").write_text(
    json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
print("[clear] 终态 opens=%d(起点 %d), 新增对=%d — 报告落盘"
      % (o1, o0, len(report["new_pairs"])), flush=True)
