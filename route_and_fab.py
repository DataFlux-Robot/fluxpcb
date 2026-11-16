# -*- coding: utf-8 -*-
"""Route the carrier board with Freerouting and export fab package.

pcbnew(KiCad python) -> DSN -> freerouting.jar -> SES -> pcbnew import -> DRC -> gerbers.
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
KIPY = r"C:\Users\zhang\AppData\Local\Programs\KiCad\10.0\bin\python.exe"
KICAD_CLI = r"C:\Users\zhang\AppData\Local\Programs\KiCad\10.0\bin\kicad-cli.exe"
JAVA = str(ROOT / "tools_bin" / "jdk25" / "bin" / "java.exe")
FR_JAR = str(ROOT / "tools_bin" / "freerouting.jar")


def pcbnew_run(code: str) -> int:
    p = subprocess.run([KIPY, "-c", code], capture_output=True, text=True,
                       timeout=1800)
    if p.returncode != 0:
        print(p.stdout[-1500:], p.stderr[-1500:])
    return p.returncode


def main(pcb_path: Path):
    pcb_path = pcb_path.resolve()
    work = pcb_path.parent
    dsn = work / "carrier.dsn"
    ses = work / "carrier.ses"

    print("[1/5] export DSN")
    rc = pcbnew_run(
        f"import pcbnew; b=pcbnew.LoadBoard(r'{pcb_path}'); "
        f"ok=pcbnew.ExportSpecctraDSN(b, r'{dsn}'); print('dsn:', ok)")
    if rc != 0:
        sys.exit("DSN export failed")
    print(dsn, dsn.stat().st_size, "bytes")

    print("[2/5] freerouting autoroute")
    p = subprocess.run([JAVA, "-jar", FR_JAR, "-de", str(dsn), "-do", str(ses),
                        "-mp", "40", "-l", "en"],
                       capture_output=True, text=True, timeout=7200, cwd=str(work))
    print((p.stdout + p.stderr)[-1200:])
    if not ses.exists():
        sys.exit("routing produced no SES file")

    print("[3/5] import SES")
    rc = pcbnew_run(
        f"import pcbnew; b=pcbnew.LoadBoard(r'{pcb_path}'); "
        f"ok=pcbnew.ImportSpecctraSES(b, r'{ses}'); b.Save(r'{pcb_path}'); print('ses:', ok)")
    if rc != 0:
        sys.exit("SES import failed")

    print("[4/5] DRC")
    p = subprocess.run([KICAD_CLI, "pcb", "drc", "--exit-code-violations",
                        "-o", str(work / "drc_post_route.rpt"), str(pcb_path)],
                       capture_output=True, text=True, timeout=1800)
    print((p.stdout + p.stderr)[:300])
    print("drc exit:", p.returncode)

    print("[5/5] fab package: gerbers + drill + positions")
    gerber_dir = work / "fab"
    gerber_dir.mkdir(exist_ok=True)
    for args in (
        ["pcb", "export", "gerbers", "-o", str(gerber_dir / "%f-%i.%x"),
         "--layers", "F.Cu,In1.Cu,In2.Cu,B.Cu,F.SilkS,B.SilkS,F.Mask,B.Mask,"
                     "Edge.Cuts,F.Paste,B.Paste,F.Fab,B.Fab", str(pcb_path)],
        ["pcb", "export", "drill", "-o", str(gerber_dir), str(pcb_path)],
        ["pcb", "export", "pos", "-o", str(gerber_dir / "positions.csv"),
         "--format", "csv", str(pcb_path)],
    ):
        p = subprocess.run([KICAD_CLI, *args], capture_output=True, text=True,
                           timeout=900)
        print(args[1], args[2], "exit", p.returncode, (p.stdout + p.stderr)[:120])
    print("DONE — fab package at", gerber_dir)


if __name__ == "__main__":
    main(Path(sys.argv[1] if len(sys.argv) > 1
              else ROOT / "projects" / "hpm6e80-core" / "pcb" / "carrier"
              / "hpm6e80-carrier.kicad_pcb"))
