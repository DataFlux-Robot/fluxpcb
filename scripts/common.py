"""Shared paths and the KiCad helper call."""
import json
import os
import subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORK = os.environ.get("BENCH_WORK", os.path.join(ROOT, "work"))
BOARDS_DIR = os.path.join(WORK, "boards")
KICAD_PYTHON = os.environ.get("KICAD_PYTHON", "python3")
KICAD_CLI = os.environ.get("KICAD_CLI", "kicad-cli")
HELPER = os.path.join(ROOT, "scripts", "kicad_board.py")


def manifest():
    """The boards of the set BOARD_SET names: the eight benchmark boards by default
    (boards/manifest.json), or boards/manifest-<set>.json (zap25: the 25 public boards
    of Zap!'s comparison)."""
    s = os.environ.get("BOARD_SET", "")
    with open(os.path.join(ROOT, "boards", f"manifest-{s}.json" if s else "manifest.json")) as f:
        return json.load(f)["boards"]


def kicad(*args):
    """Run a kicad_board.py command under KiCad's python and return its JSON result."""
    out = subprocess.run([KICAD_PYTHON, HELPER, *[str(a) for a in args]], capture_output=True, text=True)
    for ln in out.stdout.splitlines():
        i = ln.find("__RESULT__")
        if i >= 0:
            return json.loads(ln[i + len("__RESULT__"):])
    raise RuntimeError(f"kicad {args[0]}: no result; stderr tail:\n" + "\n".join(out.stderr.splitlines()[-5:]))


def board_dir(name):
    return os.path.join(BOARDS_DIR, name)


def completion(unconnected, ratsnest):
    """100 * (1 - unconnected / ratsnest), both counted by KiCad."""
    if unconnected is None:
        return None
    return 100.0 if ratsnest <= 0 else round(100.0 * (1 - unconnected / ratsnest), 2)


def drc(name, pcb):
    """kicad-cli DRC, errors only, zones refilled, judged by the board's benchmark project
    (KiCad pairs a board with the project of the same base name, so it is copied beside)."""
    place_project(name, pcb)
    report = pcb[: -len(".kicad_pcb")] + ".drc.json"
    subprocess.run([KICAD_CLI, "pcb", "drc", "--format", "json", "--severity-error", "--refill-zones", "-o", report, pcb],
                   capture_output=True, text=True, timeout=1800)
    if not os.path.exists(report):
        return None
    with open(report) as f:
        rep = json.load(f)
    by = {}
    for v in rep.get("violations", []):
        if v.get("severity") == "error" and not v.get("excluded"):
            by[v.get("type", "?")] = by.get(v.get("type", "?"), 0) + 1
    return {"errors": sum(by.values()), "errorsByType": by, "unconnectedItems": len(rep.get("unconnected_items", []))}


def place_project(name, pcb):
    """Copy the board's benchmark project file beside a board so KiCad reads the same rules."""
    import shutil
    pro = os.path.join(board_dir(name), "circuit.kicad_pro")
    shutil.copy(pro, pcb[: -len(".kicad_pcb")] + ".kicad_pro")
    dru = os.path.join(board_dir(name), "circuit.kicad_dru")
    if os.path.exists(dru):
        shutil.copy(dru, pcb[: -len(".kicad_pcb")] + ".kicad_dru")
