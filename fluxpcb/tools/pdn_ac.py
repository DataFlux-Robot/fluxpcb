# -*- coding: utf-8 -*-
"""PDN impedance Z(f) via a real ngspice AC sweep (S3).

Pipeline (the openEMS+scikit-rf+ngspice recipe, with the EM-extraction leg
replaced by a lumped plane-cavity model until openEMS is available):
  VRM (R+L) -> plane pair (lumped L,C from stackup) -> decap network
  (C+ESR+ESL each) -> load port; .ac sweep 100kHz..1GHz, Z(f)=V/I at load.
Judged against the target-Z line Zt = V*tol%/Imax across the band.
Writes sim/pdn_ac.json.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

from fluxpcb.tools import spice as spice_mod

MU0 = 4 * math.pi * 1e-7
EPS0 = 8.854e-12


def _deck(caps: list[dict], plane_l_h: float, plane_c_f: float,
          vrm_r: float, vrm_l_nH: float) -> str:
    """Standard PDN-Z measurement: 1A AC current source injected at the load
    point, VRM as ideal source + R + L, plane as lumped L(+C), decaps as
    series R-L-C. Z(f) = v(out)."""
    cap_lines = []
    for i, c in enumerate(caps):
        n = i + 1
        cap_lines.append(
            f"Rc{n} out c{n}r {c['esr']:.4g}\n"
            f"Lc{n} c{n}r c{n}l {c['esl_nh']*1e-9:.4e}\n"
            f"C{n}  c{n}l 0   {c['c_f']*1e-6:.6e}")  # c_f is uF
    caps_txt = "\n".join(cap_lines)
    return f"""* FluxPCB PDN Z(f) sweep (auto-generated)
* 1A AC at the load point; Z(f) = v(out)
Vvrm vrm 0 DC 3.3 AC 0
Rvrm vrm vrm2 {vrm_r}
Lvrm vrm2 pln1 {vrm_l_nH*1e-9}
Lp   pln1 out {plane_l_h*1e-9}
Cp   pln1 0 {plane_c_f*1e-12}
I1   0 out AC 1
{caps_txt}
.control
ac dec 40 100k 1G
let z = mag(v(out))
print z
.endc
.end
"""


def run(project_dir: Path, spec: dict) -> dict:
    # decap plan from spec power (mirrors pdn_input defaults)
    caps = ([{"c_f": 100, "esr": 0.05, "esl_nh": 1.0}] * 4 +
            [{"c_f": 10, "esr": 0.08, "esl_nh": 0.8}] * 4 +
            [{"c_f": 1, "esr": 0.1, "esl_nh": 0.6}] * 4)
    power = spec.get("power") or {}
    v = 3.3
    imax = max(0.5, float(power.get("total_w", 1.5)) / v)
    z_target = v * 0.03 / imax            # 3% ripple budget

    # plane pair lumped: Lp = mu0*h*(L/W)/... plate inductance per square
    # ~ mu0*h = 2nH/square at 1.6mm; Cp = eps0*er*L*W/h
    stackf = project_dir / "stackup.yaml"
    er, h = 4.4, 1.6e-3
    import yaml
    if stackf.exists():
        st = yaml.safe_load(stackf.read_text(encoding="utf-8")) or {}
        er = float(st.get("er", er))
        h = 0.2e-3 if int(st.get("layers", 2)) > 2 else h
    plane_l_nH = MU0 * h * 1e9            # per square, dominant term
    plane_c_pF = EPS0 * er * (0.05 * 0.03) / h * 1e12   # 50x30mm approx

    lib = spice_mod.find_ngspice()
    if lib is None:
        out = {"status": "blocked", "error": "ngspice不可用"}
        (project_dir / "sim").mkdir(exist_ok=True)
        (project_dir / "sim" / "pdn_ac.json").write_text(
            json.dumps(out), encoding="utf-8")
        return out

    deck = _deck(caps, plane_l_nH, plane_c_pF, vrm_r=0.02, vrm_l_nH=20)
    if lib.lower().endswith(".exe"):
        out_lines = spice_mod._run_standalone(lib, deck)
    else:
        out_lines = spice_mod._NgSpiceLib(lib).run(deck)

    pts = []
    for ln in out_lines:
        low = ln.strip().lower()
        if low.startswith("stdout"):
            low = low[6:].strip()
        parts = low.replace(",", " ").split()
        try:
            if len(parts) >= 3:
                pts.append((float(parts[1]), float(parts[2])))   # idx f z
                continue
            if len(parts) == 2:
                pts.append((float(parts[0]), float(parts[1])))
        except ValueError:
            continue
    if not pts:
        out = {"status": "error", "error": "AC sweep produced no points"}
        (project_dir / "sim").mkdir(exist_ok=True)
        (project_dir / "sim" / "pdn_ac.json").write_text(
            json.dumps(out), encoding="utf-8")
        return out

    curve = [(f, abs(z)) for f, z in pts]
    zmax_f, zmax = max(curve, key=lambda p: p[1])
    # margin vs target across probe band 1..200MHz
    band = [(f, z) for f, z in curve if 1e6 <= f <= 200e6]
    zmax_band_f, zmax_band = max(band, key=lambda p: p[1]) if band else (0, 0)
    margin = (z_target - zmax_band) / z_target * 100
    out = {
        "status": "ok",
        "z_target_ohm": round(z_target, 4),
        "z_max_ohm": round(zmax_band, 4),
        "z_max_at_mhz": round(zmax_band_f / 1e6, 2),
        "worst_margin_pct": round(margin, 1),
        "n_decaps": len(caps), "probe_mhz": [1, 200],
        "pass": margin >= 0,
        "curve": [[round(f / 1e6, 2), round(z, 4)] for f, z in curve[::6]],
    }
    (project_dir / "sim").mkdir(exist_ok=True)
    (project_dir / "sim" / "pdn_ac.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    return out
