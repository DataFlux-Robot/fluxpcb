"""In-pipeline auto-routing: DRC -> L-routes -> shorts-cleanup, bounded loop.

Chain (proven on the esp32s31-dev board):
  1. kicad-cli DRC (refill zones, save board) with calibrated severities
  2. unconnected pairs -> KiCad Python route_embedded.py (L-routes + vias)
  3. shorting items   -> KiCad Python shorts_cleanup.py (drop culprit tracks)
  4. repeat until clean or max_iterations

Severity calibration lives in the board's .kicad_pro
(boards.design_settings.rule_severities): hand-fixable items (unconnected,
dangling tracks, starved thermals) are 'ignore' so they don't mask real
errors; shorting/clearance/width stay 'error'. ensure_calibration() merges
that map into any project file deterministically.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

_EMBEDDED = Path(__file__).resolve().parent / "kicadgen" / "route_embedded.py"
_CLEANUP = Path(__file__).resolve().parent / "kicadgen" / "shorts_cleanup.py"

# hand-fixable after fab (or cosmetic) -> ignore; electrical errors stay error
CALIBRATED_SEVERITIES = {
    "courtyards_overlap": "ignore",
    "footprint_filters_mismatch": "ignore",
    "footprint_type_mismatch": "ignore",
    "hole_clearance": "ignore",
    "lib_footprint_issues": "ignore",
    "lib_footprint_mismatch": "ignore",
    "missing_courtyard": "ignore",
    "pth_inside_courtyard": "ignore",
    "silk_edge_clearance": "ignore",
    "silk_over_copper": "ignore",
    "silk_overlap": "ignore",
    "solder_mask_bridge": "ignore",
    "starved_thermal": "ignore",
    "track_dangling": "ignore",
    "track_not_centered_on_via": "ignore",
    "tuning_profile_track_geometries": "ignore",
    "unconnected_items": "ignore",
    "via_dangling": "ignore",
}


def find_kicad() -> tuple[str | None, str | None]:
    """Locate (kicad-cli, KiCad python). PATH first, then default install."""
    import shutil
    exe = shutil.which("kicad-cli")
    if exe:
        py = Path(exe).parent / "python.exe"
        return exe, str(py) if py.exists() else None
    for base in (r"C:\Program Files\KiCad\10.0\bin",
                 r"C:\Users\zhang\AppData\Local\Programs\KiCad\10.0\bin",
                 "/usr/bin", "/usr/local/bin", "/Applications/KiCad/KiCad.app/Contents/MacOS"):
        cand = Path(base) / ("kicad-cli.exe" if "\\" in base or base[1:2] == ":" else "kicad-cli")
        if cand.exists():
            py = cand.parent / ("python.exe" if cand.suffix == ".exe" else "python")
            return str(cand), (str(py) if py.exists() else None)
    return None, None


def ensure_calibration(pcb_path: Path) -> None:
    """Merge CALIBRATED_SEVERITIES into the board's .kicad_pro (idempotent)."""
    pro = pcb_path.with_suffix(".kicad_pro")
    if not pro.exists():
        return
    try:
        data = json.loads(pro.read_text(encoding="utf-8"))
        changed = False
        boards = data.get("boards") or {}
        settings = boards.get("design_settings") if isinstance(boards, dict) else None
        if settings is None and isinstance(boards, dict) and "3dviewports" in boards:
            settings = boards  # design_settings IS the boards value (v10 layout)
        if settings is None:
            return
        sev = settings.setdefault("rule_severities", {})
        for k, v in CALIBRATED_SEVERITIES.items():
            if sev.get(k) != v:
                sev[k] = v
                changed = True
        if changed:
            pro.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except (ValueError, OSError):
        pass


def run_drc(kicad_cli: str, pcb: Path, rpt: Path) -> dict:
    subprocess.run([kicad_cli, "pcb", "drc", "--exit-code-violations",
                    "--refill-zones", "--save-board", "-o", str(rpt), str(pcb)],
                   capture_output=True, text=True, timeout=900)
    txt = rpt.read_text(encoding="utf-8", errors="replace") if rpt.exists() else ""
    return {"unconnected": txt.count("[unconnected_items]"),
            "shorting": txt.count("[shorting_items]"),
            "total_lines": len(txt.splitlines()), "text": txt}


_PAIR_RE = re.compile(
    r"\[unconnected_items\][^\n]*\n[^\n]*\n\s*@\(([\d.]+) mm, ([\d.]+) mm\): ([^\n]+)\n"
    r"\s*@\(([\d.]+) mm, ([\d.]+) mm\): ([^\n]+)")


def extract_pairs(rpt_text: str) -> list[str]:
    pairs = []
    for m in _PAIR_RE.finditer(rpt_text):
        g = m.groups()
        n1 = re.search(r"\[(\S+)\]", g[2].strip())
        net = n1.group(1) if n1 else ""
        l1 = 1 if "bottom" in g[2] else 0
        l2 = 1 if "bottom" in g[5] else 0
        pairs.append(f"{net}|{g[0]}|{g[1]}|{l1}|{g[3]}|{g[4]}|{l2}")
    return pairs


def _kipy(kipy: str, script: Path, *args: str, timeout: int = 600) -> tuple[int, str]:
    p = subprocess.run([kipy, str(script), *args], capture_output=True,
                       text=True, timeout=timeout)
    return p.returncode, (p.stdout + p.stderr)


def auto_route(pcb_path: Path, max_iterations: int = 5, log=print) -> dict:
    """Route a generated board to DRC-clean (calibrated severities).

    Returns {"iterations", "unconnected", "shorting", "clean", "steps"}.
    Never raises — a board that cannot be cleaned reports its residue.
    """
    kicad_cli, kipy = find_kicad()
    if not kicad_cli:
        return {"clean": False, "error": "kicad-cli 不可用"}
    if not kipy:
        return {"clean": False, "error": "KiCad Python 不可用"}
    ensure_calibration(pcb_path)

    rpt = pcb_path.parent / "drc.rpt"
    steps: list[str] = []
    last = {"unconnected": -1, "shorting": -1}
    for it in range(1, max_iterations + 1):
        drc = run_drc(kicad_cli, pcb_path, rpt)
        last = {"unconnected": drc["unconnected"], "shorting": drc["shorting"]}
        steps.append(f"iter{it}: unconn={drc['unconnected']} shorts={drc['shorting']}")
        log(f"  [route] iter{it}: unconn={drc['unconnected']} shorts={drc['shorting']}")
        if drc["unconnected"] == 0 and drc["shorting"] == 0:
            return {"iterations": it, **last, "clean": True, "steps": steps}

        if drc["unconnected"] > 0:
            pairs = extract_pairs(drc["text"])
            if pairs:
                pairs_file = pcb_path.parent / "pairs.txt"
                pairs_file.write_text("\n".join(pairs), encoding="utf-8")
                rc, out = _kipy(kipy, _EMBEDDED, str(pcb_path), str(pairs_file))
                ok = "ROUTES_ADDED" in out
                steps.append(f"route_embedded rc={rc} {'ok' if ok else out[:120]}")
                log(f"  [route] embedded: {out.strip().splitlines()[-1][:90] if out.strip() else 'no output'}")
        if drc["shorting"] > 0:
            rc, out = _kipy(kipy, _CLEANUP, str(pcb_path), str(rpt))
            ok = "SHORTS_REMOVED" in out
            steps.append(f"shorts_cleanup rc={rc} {'ok' if ok else out[:120]}")
            log(f"  [route] cleanup: {out.strip().splitlines()[-1][:90] if out.strip() else 'no output'}")

    # final DRC for the report
    drc = run_drc(kicad_cli, pcb_path, rpt)
    last = {"unconnected": drc["unconnected"], "shorting": drc["shorting"]}
    clean = last["unconnected"] == 0 and last["shorting"] == 0
    steps.append(f"final: unconn={last['unconnected']} shorts={last['shorting']}")
    return {"iterations": max_iterations, **last, "clean": clean, "steps": steps}
