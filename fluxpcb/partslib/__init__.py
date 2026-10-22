"""Local parts catalog — deterministic, offline-first component selection.

The catalog (catalog.json) is a checked-in snapshot of verified parts
(LCSC numbers, footprints, stock/price at verification time). The
bom_lcsc_verified gate re-checks online when the network is available
(cache-degrading gracefully); offline runs use the snapshot as-is.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from fluxpcb.tools.stdvalues import fmt_cap, fmt_res, snap

CATALOG_PATH = Path(__file__).resolve().parent / "catalog.json"


@dataclass
class Part:
    role: str                 # family role: module / ldo / usb_c / button / led / res / cap / header
    mpn: str
    value: str = ""
    package: str = ""         # physical package: 0603 / SOT-23-5 / ...
    footprint: str = ""       # kicad lib:footprint
    lcsc: str = ""            # LCSC/JLC part number (Cxxxxx)
    datasheet: str = ""
    description: str = ""
    stock: int = 0
    price: str = ""           # unit price at snapshot time (CNY)
    basic: bool = False       # JLC "Basic" (economical assembly) part
    alternatives: list[str] = field(default_factory=list)
    verified: bool = False    # lcsc verified against JLC catalog


def load_catalog() -> list[dict]:
    return json.loads(CATALOG_PATH.read_text(encoding="utf-8"))


_PART_FIELDS = frozenset(Part.__dataclass_fields__)


def _to_part(row: dict) -> Part:
    return Part(**{k: v for k, v in row.items() if k in _PART_FIELDS})


def select(role: str, value: str | None = None) -> Part:
    """Deterministic part selection: exact role(+value) match, first hit.

    Passive values are snapped to E96 before matching so computational
    values (e.g. 4.97k) resolve to a stocked 4.99k.
    """
    rows = [r for r in load_catalog() if r["role"] == role]
    if not rows:
        raise KeyError(f"partslib: no parts for role '{role}'")
    if value is None:
        return _to_part(rows[0])
    want = _norm_value(value, role)
    for r in rows:                       # exact (normalized) value first
        if _norm_value(r.get("value", ""), role) == want:
            return _to_part(r)
    raise KeyError(f"partslib: role '{role}' has no value '{value}' "
                   f"(have: {sorted({r.get('value','') for r in rows})})")


def _norm_value(v: str, role: str) -> str:
    v = str(v).strip()
    if role == "res":
        return fmt_res(_parse_res(v))
    if role == "cap":
        return fmt_cap(_parse_cap(v))
    return v.lower()


def _parse_res(v: str) -> float:
    s = v.strip().lower().replace("ω", "").replace("r", "").replace("ohm", "")
    mult = 1.0
    for suf, m in (("k", 1e3), ("m", 1e6), ("g", 1e9)):
        if s.endswith(suf):
            mult, s = m, s[:-1]
            break
    return float(s or 0) * mult


def _parse_cap(v: str) -> float:
    s = v.strip().lower()
    mult = 1e-12
    for suf, m in (("uf", 1e-6), ("nf", 1e-9), ("pf", 1e-12)):
        if s.endswith(suf):
            mult, s = m, s[:-len(suf)]
            break
    return float(s or 0) * mult


# ---------------------------------------------------------------- family templates
def devboard_roles(spec: dict) -> list[dict]:
    """Required parts for the ESP32-S31 dev-board family, derived from spec.

    Refs mirror the board generator (kicadgen/esp32s31_dev.py) one-to-one so
    BOM, schematic and PCB stay consistent. Values follow E96 via select().
    """
    cons = spec.get("constraints") or {}
    buttons = max(1, int(cons.get("buttons", 2)))
    leds = max(1, int(cons.get("leds", 2)))
    prog = bool(cons.get("prog_header", True))
    rows = int((cons.get("headers") or {}).get("rows", 2))
    header_role = {8: "header_1x08", 10: "header_1x10", 14: "header_1x14"}         .get(int((cons.get("headers") or {}).get("pins_per_row", 14)), "header_1x14")
    roles: list[dict] = [
        {"role": "module", "refs": ["MOD1"]},
        {"role": "usb_c", "refs": ["J4"]},
        {"role": "ldo", "refs": ["U1"]},
        {"role": "button", "refs": [f"SW{i+1}" for i in range(buttons)]},
        {"role": "led", "value": "RED", "refs": ["D1"]},
    ]
    if leds > 1:
        roles.append({"role": "led", "value": "BLUE", "refs": ["D2"]})
    roles += [
        # EN pull-up always; IO0 pull-up only with a boot button
        {"role": "res", "value": "10k",
         "refs": ["R2"] + (["R3"] if buttons > 1 else [])},
        {"role": "res", "value": "1k",
         "refs": ["R1"] + (["R4"] if leds > 1 else [])},           # LED series
        {"role": "res", "value": "5.1k", "refs": ["R5", "R6"]},    # USB-C CC
        {"role": "cap", "value": "10uF", "refs": ["C1", "C2"]},    # LDO in/out
        {"role": "cap", "value": "100nF", "refs": ["C3"]},         # EN filter
        {"role": header_role, "refs": [f"J{i+1}" for i in range(rows)]},
    ]
    if prog:
        roles.append({"role": "header_1x04", "refs": ["J3"]})
    return roles


def px4_roles(spec: dict) -> list[dict]:
    """PX4 FMU family BOM (all public-document parts). mcu=h743 swaps in
    the H743 + VCAP caps (no SMPS inductor); default stays N6."""
    roles = [
        {"role": "imu", "refs": ["U2", "U3"]},
        {"role": "baro", "refs": ["U4", "U5"]},
        {"role": "mag", "refs": ["U6"]},
        {"role": "ldo", "refs": ["U7"]},
        {"role": "can_xcvr", "refs": ["U8"]},
        {"role": "ferrite", "refs": ["FB1"]},
        {"role": "xtal_24m", "refs": ["Y1"]},
        {"role": "xtal_32k", "refs": ["Y2"]},
        {"role": "hdr_1x06", "refs": ["J1", "J2"]},
        {"role": "hdr_1x04", "refs": ["J3"]},
        {"role": "usb_c", "refs": ["J4"]},
        {"role": "hdr_2x05", "refs": ["J5"]},
        {"role": "hdr_2x07", "refs": ["J6"]},
        {"role": "hdr_1x03", "refs": ["J7"]},
        {"role": "led", "value": "BLUE", "refs": ["D1"]},
        {"role": "res", "value": "1k", "refs": ["R1"]},
    ]
    if (spec.get("constraints") or {}).get("mcu") == "h743":
        return [{"role": "mcu_h743", "refs": ["U1"]},
                {"role": "cap", "value": "2.2uF", "refs": ["CV1", "CV2"]}] + roles
    return [{"role": "mcu_n6", "refs": ["U1"]},
            {"role": "ind_vddcore", "refs": ["L1"]}] + roles


def fc_roles(spec: dict) -> list[dict]:
    """Pixhawk-class full FC BOM (h743-fc): dual-trait IMUs (BMI088 +
    ICM-45686), dual CAN, microSD, 8 UARTs + 3-row PWM header."""
    return [
        {"role": "mcu_h743", "refs": ["U1"]},
        {"role": "imu_bmi088", "refs": ["U2"]},
        {"role": "imu_icm45686", "refs": ["U3"]},
        {"role": "baro", "refs": ["U4", "U5"]},
        {"role": "mag", "refs": ["U6"]},
        {"role": "ldo", "refs": ["U7"]},
        {"role": "can_xcvr", "refs": ["U8", "U9"]},
        {"role": "cap", "value": "2.2uF", "refs": ["CV1", "CV2"]},
        {"role": "ferrite", "refs": ["FB1"]},
        {"role": "xtal_24m", "refs": ["Y1"]},
        {"role": "xtal_32k", "refs": ["Y2"]},
        {"role": "hdr_1x06", "refs": ["J1", "J2", "J3"]},
        {"role": "hdr_1x04", "refs": ["J4", "J5", "J6", "J7", "J8",
                                      "J12", "J13", "J14"]},
        {"role": "hdr_2x05", "refs": ["J9"]},
        {"role": "usb_c", "refs": ["J10"]},
        {"role": "hdr_3x08", "refs": ["J11"]},
        {"role": "hdr_1x05", "refs": ["J15"]},
        {"role": "hdr_1x03", "refs": ["J16"]},
        {"role": "microsd", "refs": ["J17"]},
        {"role": "led", "value": "BLUE", "refs": ["D1"]},
        {"role": "res", "value": "1k", "refs": ["R1"]},
    ]
