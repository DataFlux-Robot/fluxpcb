# -*- coding: utf-8 -*-
"""Route remaining connections using KiCad's native pcbnew Python API.

Loads the board, adds tracks/vias programmatically, saves — no text injection.
This eliminates all format-mismatch risks from regex-based board manipulation.
"""
from __future__ import annotations

import re
import subprocess
import sys
import uuid as uuidlib
from pathlib import Path

KICAD = r"C:\Users\zhang\AppData\Local\Programs\KiCad\10.0\bin\kicad-cli.exe"
KIPY = r"C:\Users\zhang\AppData\Local\Programs\KiCad\10.0\bin\python.exe"


def kipy_run(code: str, timeout=600) -> tuple[int, str]:
    p = subprocess.run([KIPY, "-c", code], capture_output=True, text=True,
                       timeout=timeout)
    return p.returncode, (p.stdout + p.stderr)


def kipy_run_file(script: str, pcb: str, pairs: str, timeout=600) -> tuple[int, str]:
    p = subprocess.run([KIPY, script, pcb, pairs], capture_output=True, text=True,
                       timeout=timeout)
    return p.returncode, (p.stdout + p.stderr)


def route_bridges(pcb_path: Path, pairs_file: Path) -> str:
    """Return the command to run the embedded routing script."""
    embedded = Path(__file__).parent / "fluxpcb" / "kicadgen" / "route_embedded.py"
    return str(embedded)


def get_drc_pairs(rpt_path: Path):
    """Extract unconnected pairs from DRC report."""
    txt = rpt_path.read_text(encoding="utf-8", errors="replace")
    pairs = []
    for m in re.finditer(
            r"\[unconnected_items\][^\n]*\n[^\n]*\n\s*@\(([\d.]+) mm, ([\d.]+) mm\): ([^\n]+)\n"
            r"\s*@\(([\d.]+) mm, ([\d.]+) mm\): ([^\n]+)", txt):
        g = m.groups()
        d1, d2 = g[2].strip(), g[5].strip()
        n1 = re.search(r"\[(\S+)\]", d1)
        net = n1.group(1) if n1 else ""
        l1 = 1 if "bottom" in d1 else 0
        l2 = 1 if "bottom" in d2 else 0
        pairs.append(f"{net}|{g[0]}|{g[1]}|{l1}|{g[3]}|{g[4]}|{l2}")
    return pairs


def run_drc(pcb: Path, rpt: Path) -> tuple[int, str]:
    p = subprocess.run([KICAD, "pcb", "drc", "--exit-code-violations",
                        "--refill-zones", "--save-board", "-o", str(rpt), str(pcb)],
                       capture_output=True, text=True, timeout=900)
    txt = rpt.read_text(encoding="utf-8", errors="replace")
    unconn = txt.count("[unconnected_items]")
    shorts = txt.count("[shorting_items]")
    return p.returncode, f"unconn={unconn} shorts={shorts}"


def main(pcb_path: Path):
    rpt = pcb_path.parent / "drc.rpt"

    for it in range(1, 5):
        # Step 1: DRC to get current pairs
        code, msg = run_drc(pcb_path, rpt)
        print(f"iter {it}: {msg}")
        if "unconn=0" in msg and "shorts=0" in msg:
            print("✅ ALL CLEAN")
            return 0

        # Step 2: extract pairs
        pairs = get_drc_pairs(rpt)
        if not pairs:
            print("no pairs to route")
            break

        # Step 3: write pairs to temp file
        pairs_file = pcb_path.parent / "pairs.txt"
        pairs_file.write_text("\n".join(pairs), encoding="utf-8")

        # Step 4: run KiCad Python to add routes
        embedded_script = route_bridges(pcb_path, pairs_file)
        rc, out = kipy_run_file(embedded_script, str(pcb_path), str(pairs_file))
        print(f"  kipy: rc={rc} {out.strip()[:80]}")

        if "ROUTES_ADDED" not in out:
            print(f"  kipy failed: {out[:300]}")
            break

    return 1


if __name__ == "__main__":
    pcb = Path(sys.argv[1] if len(sys.argv) > 1 else
               "projects/esp32s31-dev/pcb/board/esp32s31-dev.kicad_pcb")
    sys.exit(main(pcb))
