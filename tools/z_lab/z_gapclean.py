# -*- coding: utf-8 -*-
"""z_gapclean — 间距真残差批量清线(阶段6/收尾3).

用法: z_gapclean.py BOARD_DIR
步骤:
  0. 摘净 .kicad_dru 的 flux_* 规则 -> fab_close 重建单源基线
     (反例账: 跨轮重复规则叠出 required 漂移 0.1298/0.1457);
  1. 对 genuine(非无名, actual < 政策地板)逐条 gap_nudge:
     方向 = 两违例对象连线方向(±), dist 0.1/0.2 各试,
     gap_nudge 自带连通保全契约(P6 教训);
  2. 批量后 fab_close 终测 -> before/after genuine 对账。
产出: z_gapclean_report.json + stdout 摘要。
"""
import json
import re
import subprocess
import sys
from pathlib import Path

KIPY = "C:/Users/zhang/AppData/Local/Programs/KiCad/10.0/bin/python.exe"
KCLI = "C:/Users/zhang/AppData/Local/Programs/KiCad/10.0/bin/kicad-cli.EXE"
KG = Path("E:/DATA/vscode/ZCODE/flux-neurotransmitter/fluxpcb/kicadgen")

board_dir = Path(sys.argv[1])
pcb = sorted(board_dir.glob("*.kicad_pcb"))[0]
dru = pcb.with_suffix(".kicad_dru")


def run_fab():
    r = subprocess.run(
        [KIPY, str(KG / "fab_waiver.py"), str(pcb), KCLI],
        capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=1200)
    txt = (r.stdout or "")
    i = txt.find("{")
    return json.loads(txt[i:]) if i >= 0 else {}


# 0. 摘净 flux_* 规则(单源重建)
if dru.exists():
    txt = dru.read_text(encoding="utf-8")
    parts = txt.split("(rule ")
    keep = [parts[0]]
    stripped = 0
    for p in parts[1:]:
        blk = "(rule " + p
        name = blk.split('"')[1] if '"' in blk else ""
        if name.startswith(("flux_waiver", "flux_policy")):
            stripped += 1
            continue
        keep.append(blk)
    dru.write_text("".join(keep), encoding="utf-8")
    print("摘除旧 flux 规则 %d 条" % stripped)
d0 = run_fab()
before = d0.get("genuine")
print("基线: genuine=%s probe=%s" % (before, d0.get("probe")))

# 1. 批量 gap_nudge(方向 = 违例对象连线 ±, dist 0.1/0.2)
led = json.loads((board_dir / "fab_waivers.json").read_text(
    encoding="utf-8"))
genuine = [x for x in (led.get("genuine") or [])
           if x.get("verdict") != "unnamed-net"
           and (x.get("actual_mm") or 1) < 0.12]
raw = json.loads((board_dir / (pcb.stem + ".fabprobe.json"))
                 .read_text(encoding="utf-8")) \
    if (board_dir / (pcb.stem + ".fabprobe.json")).exists() else {}


def _pair_pos(v):
    items = v.get("items") or []
    if len(items) < 2:
        return None
    p1 = items[0].get("pos") or {}
    p2 = items[1].get("pos") or {}
    return (p1.get("x"), p1.get("y"), p2.get("x"), p2.get("y"))


pos_by_pair = {}
for v in (raw.get("violations") or []):
    if "clearance" not in (v.get("type") or ""):
        continue
    desc = [it.get("description", "") for it in (v.get("items") or [])]
    key = tuple(sorted(re.findall(r"\[([^\]]+)\]", " ".join(desc))))[:2]
    pp = _pair_pos(v)
    if key and pp and key not in pos_by_pair:
        pos_by_pair[key] = pp

report = {"before": before, "nudges": []}
for it in genuine:
    nets = tuple(sorted(it.get("nets") or []))
    pp = pos_by_pair.get(nets)
    if not pp or None in pp:
        continue
    x1, y1, x2, y2 = pp
    import math
    L = math.hypot(x2 - x1, y2 - y1) or 1.0
    ux, uy = (x1 - x2) / L, (y1 - y2) / L   # 互推方向
    done = False
    for (dx, dy) in ((ux, uy), (-ux, -uy)):
        for dist in (0.1, 0.2):
            r = subprocess.run(
                [KIPY, str(KG / "gap_nudge.py"), str(pcb),
                 str(x1), str(y1), str(dx), str(dy), str(dist)],
                capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=300)
            out = (r.stdout or "").strip().splitlines()
            try:
                res = json.loads(out[-1]) if out else {}
            except ValueError:
                res = {}
            report["nudges"].append({
                "nets": list(nets), "at": [x1, y1], "dist": dist,
                "dir": [round(dx, 2), round(dy, 2)],
                "nudged": res.get("nudged"),
                "kept_connected": res.get("kept_connected")})
            if (res.get("nudged") or 0) > 0:
                done = True
                break
        if done:
            break

# 2. 终测对账
d1 = run_fab()
report["after"] = d1.get("genuine")
report["probe_after"] = d1.get("probe")
(board_dir / "z_gapclean_report.json").write_text(
    json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
print("终测: genuine %s -> %s | probe=%s | 报告已落盘"
      % (before, d1.get("genuine"), d1.get("probe")))
