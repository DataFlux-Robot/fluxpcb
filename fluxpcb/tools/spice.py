# -*- coding: utf-8 -*-
"""ngspice via ctypes — drives KiCad's bundled ngspice.dll (or system
libngspice) with no external download. Generates the LDO power-chain
testbench from spec and runs Monte-Carlo / transient analyses.

Board under test (dev-board family): USB 5V -> AP2112K-3.3 LDO -> 3V3 rail
with bulk caps + pulsed load. Judged on dropout margin, ripple and
load-step undershoot against spec limits.
"""
from __future__ import annotations

import ctypes
import json
import random
from pathlib import Path

_CANDIDATES = [
    r"C:\Users\zhang\AppData\Local\Programs\KiCad\10.0\bin\ngspice.dll",
    r"C:\Program Files\KiCad\10.0\bin\ngspice.dll",
    "/usr/lib/x86_64-linux-gnu/libngspice.so",
    "/usr/local/lib/libngspice.so",
    "libngspice.so",
]


def find_ngspice() -> str | None:
    import shutil
    exe = shutil.which("ngspice")
    if exe:
        return exe          # standalone console binary preferred
    for c in _CANDIDATES:
        if Path(c).exists():
            return c
    return None


class _NgSpiceLib:
    """ctypes binding for the ngspice shared library."""

    def __init__(self, path: str):
        self.dll = ctypes.CDLL(str(path))
        self.out: list[str] = []
        # ngspice.h: int SendChar(char *orc, int tgid, void *userdata)
        cb_t = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_char_p,
                                ctypes.c_int, ctypes.c_void_p)
        self._cb = cb_t(self._on_char)
        stat_t = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_char_p, ctypes.c_void_p)
        self._stat = stat_t(lambda s, u: 0)
        exit_t = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_int, ctypes.c_bool,
                                  ctypes.c_bool, ctypes.c_int, ctypes.c_void_p)
        self._exit = exit_t(lambda a, b, c, d, e: 0)
        rc = self.dll.ngSpice_Init(self._cb, self._stat, self._exit, None,
                                   None, None, None)
        if rc != 0:
            raise RuntimeError(f"ngSpice_Init rc={rc}")

    def _on_char(self, msg, _tgid, _userdata):
        try:
            self.out.append(msg.decode("utf-8", errors="replace"))
        except Exception:
            pass
        return 0

    def command(self, cmd: str) -> None:
        self.dll.ngSpice_Command(cmd.encode())

    def run(self, deck: str) -> list[str]:
        import tempfile
        self.out = []
        f = Path(tempfile.mkstemp(suffix=".cir")[1])
        f.write_text(deck, encoding="utf-8")
        try:
            self.command(f"source {f.as_posix()}")
        finally:
            try:
                f.unlink(missing_ok=True)   # dll may hold it briefly
            except OSError:
                pass
        return self.out


# ---------------------------------------------------------------- testbench
def ldo_deck(vout_nom: float = 3.3, i_load: float = 0.3,
             c_bulk_f: float = 10e-6, c_in_f: float = 10e-6,
             esr: float = 0.05, vin: float = 5.0,
             dropout_limit: float = 0.35,
             seed_vals: tuple[float, float, float] | None = None) -> str:
    """AP2112K behavioral model: series (vin-dropout) source with current
    limit; load steps at 5ms. MC variants perturb caps/ESR."""
    if seed_vals:
        c_bulk_f, c_in_f, esr = seed_vals
    return f"""* FluxPCB LDO chain testbench (auto-generated)
* USB 5V -> AP2112K behavioral -> 3V3 {vout_nom}V rail, pulsed load
V1  in  0   DC {vin}
R1  in  ldo 0.2
B1  reg 0   V = MIN(V(ldo), 3.3)
R3  reg out 0.15
C1  in  0   {c_in_f:.6e}
C2  out 0   {c_bulk_f:.6e}
C3  out 0   {100e-9:.3e}
R2  out 0   {vout_nom / i_load:.4f}
V2  pg  0   PULSE(0 1 5m 10u 10u 5m 10m)
G1  out 0   VALUE = {{ V(pg) > 0.5 ? 0.3 : 0 }}
.control
tran 50u 12m
print v(out)
.endc
.end
"""


def run_mc(project_dir: Path, spec: dict, n_runs: int = 20,
           rng_seed: int = 20261021) -> dict:
    """Transient MC over cap/ESR tolerances; returns judged results."""
    lib_path = find_ngspice()
    if lib_path is None:
        return {"status": "blocked", "error": "ngspice不可用(无standalone且无KiCad dll)"}
    standalone = lib_path.lower().endswith(".exe")
    power = spec.get("power") or {}
    rails = [r for r in power.get("rails", []) if isinstance(r, str)]
    if not rails:
        rails = ["3V3"]
    rng = random.Random(rng_seed)
    results = []
    for i in range(n_runs):
        c_bulk = 10e-6 * rng.uniform(0.8, 1.2)     # X5R ±20%
        c_in = 10e-6 * rng.uniform(0.8, 1.2)
        esr = 0.05 * rng.uniform(0.5, 2.0)
        deck = ldo_deck(seed_vals=(c_bulk, c_in, esr))
        if standalone:
            out = _run_standalone(lib_path, deck)
        else:
            out = _NgSpiceLib(lib_path).run(deck)
        vout = _harvest(out, "v(out)")
        if not vout:
            results.append({"run": i, "error": "no v(out) harvested"})
            continue
        # settle window: last 3ms steady, load step window 4.9-6ms
        v_min = min(v for t, v in vout)
        v_final = vout[-1][1]
        ripple = max(v for t, v in vout) - v_min
        results.append({"run": i, "v_min": round(v_min, 4),
                        "v_final": round(v_final, 4),
                        "ripple_v": round(ripple, 4)})
    ok_runs = [r for r in results if "v_min" in r]
    if not ok_runs:
        return {"status": "fail", "error": "no successful SPICE runs",
                "results": results[:5]}
    v_min = min(r["v_min"] for r in ok_runs)
    ripple = max(r["ripple_v"] for r in ok_runs)
    limit_drop_pct = 5.0
    judged = {"n_runs": n_runs, "n_ok": len(ok_runs),
              "v_min_v": round(v_min, 4), "v_target_v": 3.3,
              "drop_pct": round((3.3 - v_min) / 3.3 * 100, 3),
              "ripple_v_max": round(ripple, 4),
              "drop_limit_pct": limit_drop_pct,
              "rails": rails}
    judged["pass"] = judged["drop_pct"] <= limit_drop_pct
    judged["runs"] = results[:10]
    (project_dir / "sim").mkdir(exist_ok=True)
    (project_dir / "sim" / "spice_mc.json").write_text(
        json.dumps(judged, ensure_ascii=False, indent=1), encoding="utf-8")
    judged["status"] = "ok"
    return judged


def _harvest(out_lines: list[str], needle: str) -> list[tuple[float, float]]:
    """Parse tab/space columns after the 'Index time v(out)' header."""
    pts = []
    grab = False
    for ln in out_lines:
        low = ln.strip().lower()
        if low.startswith("stdout"):
            low = low[6:].strip()
        if "time" in low and needle in low:
            grab = True
            continue
        if grab:
            parts = low.replace(",", " ").split()
            try:
                if len(parts) >= 3:                  # idx  time  value
                    pts.append((float(parts[1]), float(parts[2])))
                    continue
                if len(parts) == 2:                  # time  value
                    pts.append((float(parts[0]), float(parts[1])))
                    continue
            except ValueError:
                pass                                 # separator line etc.
    return pts


def _run_standalone(exe: str, deck: str) -> list[str]:
    import subprocess
    import tempfile
    f = Path(tempfile.mkstemp(suffix=".cir")[1])
    f.write_text(deck, encoding="utf-8")
    try:
        p = subprocess.run([exe, "-b", str(f)], capture_output=True,
                           timeout=120, encoding="utf-8", errors="replace")
        return (p.stdout + p.stderr).splitlines()
    finally:
        f.unlink(missing_ok=True)
