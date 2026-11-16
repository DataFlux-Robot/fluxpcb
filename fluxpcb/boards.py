"""Board driver: generates real KiCad artifacts for the hpm6e80-core project
(core + carrier schematics, carrier PCB) and runs real ERC/DRC evidence.
Called by the schematic/pcb stages when kicad-cli is available."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _kicad() -> str | None:
    return shutil.which("kicad-cli")


def run_kicad(*args, timeout=600):
    exe = _kicad()
    p = subprocess.run([exe, *args], capture_output=True, text=True, timeout=timeout)
    return p.returncode, (p.stdout + p.stderr).strip()


def build_schematics(project_dir: Path) -> dict:
    """Core + carrier schematics with real ERC evidence."""
    from fluxpcb.kicadgen.core_lib import core_schematic
    from fluxpcb.kicadgen.parts_lib import carrier_schematic
    from fluxpcb.kicadgen.schematic import write as sch_write

    ev = {"kicad_cli": bool(_kicad()), "boards": {}}
    jobs = (("core", core_schematic()), ("carrier", carrier_schematic()))
    for name, sch in jobs:
        d = project_dir / "schematic" / name
        sch_write(sch, d / f"hpm6e80-{name}.kicad_sch")
        if _kicad():
            sch_file = next(d.glob("*.kicad_sch"))
            code, msg = run_kicad("sch", "erc", "--exit-code-violations",
                                  "-o", str(d / "erc.rpt"), str(sch_file))
            ev["boards"][name] = {"erc_exit": code, "erc_msg": msg[:200]}
    (project_dir / "schematic" / "boards_evidence.json").write_text(
        json.dumps(ev, ensure_ascii=False, indent=1), encoding="utf-8")
    return ev


def build_carrier_pcb(project_dir: Path) -> dict:
    """Carrier PCB with real DRC evidence (uses build_carrier_pcb.main)."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "build_carrier_pcb", ROOT / "build_carrier_pcb.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    pcb_dir = project_dir / "pcb" / "carrier"
    pcb_dir.mkdir(parents=True, exist_ok=True)
    from fluxpcb.kicadgen.pcb import write as pcb_write
    board = mod.build_board()
    pcb_write(board, pcb_dir / "hpm6e80-carrier.kicad_pcb")
    # project file + lib tables
    import json as _json
    pro = pcb_dir / "hpm6e80-carrier.kicad_pro"
    sch_pro = project_dir / "schematic" / "carrier" / "hpm6e80-carrier.kicad_pro"
    if sch_pro.exists():
        pro.write_text(sch_pro.read_text(encoding="utf-8"), encoding="utf-8")
    fp_tbl = Path(r"C:\Users\zhang\AppData\Local\Programs\KiCad\10.0"
                  r"\share\kicad\template\fp-lib-table")
    if fp_tbl.exists():
        shutil.copy2(fp_tbl, pcb_dir / "fp-lib-table")
    ev = {}
    if _kicad():
        code, msg = run_kicad("pcb", "drc", "--exit-code-violations",
                              "-o", str(pcb_dir / "drc.rpt"),
                              str(pcb_dir / "hpm6e80-carrier.kicad_pcb"), timeout=900)
        ev = {"drc_exit": code, "drc_msg": msg[:300]}
    return ev
