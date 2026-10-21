# -*- coding: utf-8 -*-
"""Parametric ESP32-S31 dev-board family generator (schematic + PCB).

Driven by spec constraints: board size, header rows/pins, buttons, LEDs,
prog header. Pin map/footprint data come from the verified esp32s31_dev
tables; placement is RULE-BASED (functional zones + 1.27mm grid + decap
beside its IC + connectors at edges) instead of hand-tuned coordinates.

The esp32s31-dev board (hand-tuned, released) remains the legacy instance;
this module is the generator for new family variants (e.g. esp32s31-mini).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from fluxpcb.kicadgen import esp32s31_dev as legacy
from fluxpcb.kicadgen.pcb import Board, Fp
from fluxpcb.kicadgen.parts_lib import P
from fluxpcb.kicadgen.schematic import Schematic, SymDef, SymInst

ROOT = Path(__file__).resolve().parent.parent.parent

# free GPIO order for header allocation (verified module pin table)
_FREE_IOS = ["IO2", "IO3", "IO4", "IO5", "IO6", "IO7", "IO8", "IO9",
             "IO10", "IO11", "IO12", "IO13", "IO14", "IO15", "IO16",
             "IO17", "IO33", "IO34", "IO35", "IO36", "IO37"]


@dataclass
class DevBoardParams:
    name: str = "esp32s31-devboard"
    w_mm: float = 52.0
    h_mm: float = 30.0
    header_rows: int = 2
    header_pins: int = 8          # per row
    buttons: int = 1
    leds: int = 1
    prog_header: bool = True

    @classmethod
    def from_spec(cls, spec: dict) -> "DevBoardParams":
        cons = spec.get("constraints") or {}
        w, h = cons.get("size_mm", [52.0, 30.0])
        hdr = cons.get("headers", {}) or {}
        return cls(
            name=spec.get("project", "esp32s31-devboard"),
            w_mm=float(w), h_mm=float(h),
            header_rows=int(hdr.get("rows", 2)),
            header_pins=int(hdr.get("pins_per_row", 8)),
            buttons=int(cons.get("buttons", 1)),
            leds=int(cons.get("leds", 1)),
            prog_header=bool(cons.get("prog_header", True)),
        )

    def header_nets(self) -> list[list[str]]:
        """Header pin nets: rails first, then free GPIOs in order."""
        need = self.header_rows * self.header_pins
        ios = _FREE_IOS[: max(0, need - 6)]
        rows = []
        cursor = 0
        for r in range(self.header_rows):
            row = []
            if r == 0:
                row += ["5V", "3V3", "GND", "EN"]
            else:
                row += ["GND"]
            while len(row) < self.header_pins and cursor < len(ios):
                row.append(ios[cursor])
                cursor += 1
            rows.append(row[:self.header_pins])
        return rows


# ---------------------------------------------------------------- rule-based placement
def _snap(v: float, grid: float = 1.27) -> float:
    return round(round(v / grid) * grid, 2)


def placement(p: DevBoardParams) -> dict[str, tuple[float, float, float]]:
    """Deterministic functional-zone placement: module left-center, USB-C on
    the right edge, LDO between them with bulk caps beside it, buttons on the
    bottom edge, LEDs on the top edge, decap/pull-ups adjacent to their IC,
    headers in a center column. All snapped to 1.27mm."""
    w, h = p.w_mm, p.h_mm
    cx, cy = w / 2.0, h / 2.0
    vertical_module = h >= 28
    pl: dict[str, tuple[float, float, float]] = {}

    if vertical_module:                    # module rot 0, left side
        mx, mrot = _snap(0.23 * w), 0.0
        col_x = _snap(0.47 * w)            # header column right of module
    else:                                  # module rot 90, left side
        mx, mrot = _snap(0.24 * w), 90.0
        col_x = _snap(0.52 * w)

    pl["MOD1"] = (mx, _snap(cy), mrot)
    pl["J4"] = (_snap(w - 2.2), _snap(cy), 90.0)             # USB-C right edge
    # power chain between module and USB
    ux = _snap(0.68 * w)
    pl["U1"] = (ux, _snap(cy), 0.0)                          # LDO
    pl["C1"] = (_snap(ux - 2.54), _snap(cy - 3.81), 0.0)     # LDO in cap
    pl["C2"] = (_snap(ux - 2.54), _snap(cy + 3.81), 0.0)     # LDO out cap
    pl["C3"] = (_snap(ux + 2.54), _snap(cy - 3.81), 0.0)     # EN filter cap
    # CC pulldowns left of the USB-C pad zone (its pads start ~w-6.6)
    pl["R5"] = (_snap(w - 10.16), _snap(cy - 2.54), 0.0)
    pl["R6"] = (_snap(w - 10.16), _snap(cy + 2.54), 0.0)
    # pull-ups between the header column and the LDO zone (clear of the
    # module's castellated pad ring and the header PTH pads)
    corridor_x = _snap(col_x + 5.08)
    if vertical_module:
        pl["R2"] = (corridor_x, _snap(cy - 6.35), 0.0)
        pl["R3"] = (corridor_x, _snap(cy + 6.35), 0.0)
    else:
        pl["R2"] = (corridor_x, _snap(cy - 5.08), 0.0)
        pl["R3"] = (corridor_x, _snap(cy + 5.08), 0.0)
    # LEDs top edge + series resistors (R1, then R4 if a second LED)
    led_r_refs = ["R1"] + (["R4"] if p.leds > 1 else [])
    for i in range(p.leds):
        lx = _snap(0.62 * w + i * 5.08)
        pl[f"D{i+1}"] = (lx, _snap(3.81), 0.0)
        pl[led_r_refs[i]] = (_snap(lx + 2.54), _snap(3.81), 0.0)
    # buttons bottom edge
    for i in range(p.buttons):
        pl[f"SW{i+1}"] = (_snap(0.62 * w + 1.27 + i * 6.35), _snap(h - 5.08), 0.0)
    # header columns (rot 0: KiCad 1xN pin headers originate at pad 1 and
    # extend +y, so place pad 1 at the top of the span)
    span = (p.header_pins - 1) * 2.54
    y1 = _snap(cy - span / 2.0)
    for r in range(p.header_rows):
        pl[f"J{r+1}"] = (col_x, y1, 0.0)
        col_x = _snap(col_x + 2.54)
    if p.prog_header:
        # prog header along the bottom edge; rot 270 turns the +y pad run
        # into +x so pads stay inside the board outline
        pl["J3"] = (_snap(0.60 * w), _snap(h - 2.54), 270.0)
    del span
    return pl


# ---------------------------------------------------------------- netlists
def _mod_nets(p: DevBoardParams) -> dict[str, str]:
    nets = {}
    for name, num in legacy.MOD_PINS.items():
        if name == "GND":
            for n in (num if isinstance(num, list) else [num]):
                nets[str(n)] = "GND"
        elif name == "3V3":
            for n in num:
                nets[str(n)] = "3V3"
        elif name in ("EN", "USB_DP", "USB_DM", "TX0", "RX0"):
            nets[str(num)] = name
    header_ios = {n for row in p.header_nets() for n in row}
    for name, num in legacy.MOD_PINS.items():
        if name in header_ios and name.startswith("IO"):
            nets[str(num)] = name
    return nets


def _led_net(i: int) -> str:
    return "LED_PWR" if i == 0 else "LED_USER"


def schematic(p: DevBoardParams) -> Schematic:
    sch = Schematic(title=p.name, project=p.name)
    nets = _mod_nets(p)
    header_ios = {n for row in p.header_nets() for n in row}
    sch.add_def(legacy._module_symbol())
    sch.add_def(SymDef("flux:USB_C_16P", [P("A4", "VBUS"), P("A9", "VBUS"),
                                          P("A1", "GND"), P("A12", "GND"),
                                          P("B4", "VBUS"), P("B9", "VBUS"),
                                          P("B1", "GND"), P("B12", "GND"),
                                          P("A6", "D+"), P("A7", "D-"),
                                          P("B6", "D+"), P("B7", "D-"),
                                          P("A5", "CC1"), P("B5", "CC2"),
                                          P("S1", "SH")], ref_prefix="J"))
    sch.add_def(SymDef("flux:AP2112K", [
        P(1, "VIN"), P(2, "GND"), P(3, "EN"), P(5, "VOUT")], ref_prefix="U"))
    sch.add_def(SymDef("flux:LED", [P(1, "A"), P(2, "K")], ref_prefix="D"))
    sch.add_def(SymDef("flux:R_0603", [P(1, "A"), P(2, "B")], ref_prefix="R"))
    sch.add_def(SymDef("flux:C_0603", [P(1, "A"), P(2, "B")], ref_prefix="C"))
    sch.add_def(SymDef("flux:SW_PUSH", [P(1, "A"), P(2, "B")], ref_prefix="SW"))
    sch.add_def(SymDef("flux:HDR14", [P(i, f"P{i}") for i in range(1, 15)],
                       ref_prefix="J"))
    sch.add_def(SymDef("flux:HDR4", [P(i, f"P{i}") for i in range(1, 5)],
                       ref_prefix="J"))

    led_r_refs = ["R1"] + (["R4"] if p.leds > 1 else [])
    x0, y0 = 50.8, 127.0
    sch.place(SymInst("MOD1", "flux:ESP32-S31-WROOM-3U", "ESP32-S31-WROOM-3U",
                      x=30.48, y=127.0, pin_nets=nets))
    sch.place(SymInst("J4", "flux:USB_C_16P", "USB-C", x=x0, y=y0,
                      pin_nets={"A4": "5V", "A9": "5V", "B4": "5V", "B9": "5V",
                                "A1": "GND", "A12": "GND", "B1": "GND", "B12": "GND",
                                "A6": "USB_DP", "B6": "USB_DP",
                                "A7": "USB_DM", "B7": "USB_DM",
                                "A5": "CC1", "B5": "CC2", "S1": "GND"}))
    sch.place(SymInst("U1", "flux:AP2112K", "AP2112K-3.3", x=68.58, y=y0,
                      pin_nets={"1": "5V", "2": "GND", "3": "5V", "5": "3V3"}))
    # buttons: SW1 -> EN (reset); SW2 -> IO0 (boot) when present
    btn_nets = [{"1": "EN", "2": "GND"}, {"1": "IO0", "2": "GND"}]
    for i in range(p.buttons):
        sch.place(SymInst(f"SW{i+1}", "flux:SW_PUSH", "RESET" if i == 0 else "BOOT",
                          x=x0 + i * 7.62, y=101.6, pin_nets=btn_nets[min(i, 1)]))
    # pull-ups
    sch.place(SymInst("R2", "flux:R_0603", "10k", x=66.04, y=101.6,
                      pin_nets={"1": "3V3", "2": "EN"}))
    if p.buttons > 1:
        sch.place(SymInst("R3", "flux:R_0603", "10k", x=73.66, y=101.6,
                          pin_nets={"1": "3V3", "2": "IO0"}))
    # CC pulldowns
    sch.place(SymInst("R5", "flux:R_0603", "5.1k", x=50.8, y=73.66,
                      pin_nets={"1": "CC1", "2": "GND"}))
    sch.place(SymInst("R6", "flux:R_0603", "5.1k", x=55.88, y=73.66,
                      pin_nets={"1": "CC2", "2": "GND"}))
    # LEDs + series resistors
    led_sources = ["3V3", "IO9"][:p.leds]
    for i, src in enumerate(led_sources):
        net = _led_net(i)
        sch.place(SymInst(f"D{i+1}", "flux:LED", "RED" if i == 0 else "BLUE",
                          x=62.23 + i * 10.16, y=73.66,
                          pin_nets={"1": src, "2": net}))
        sch.place(SymInst(led_r_refs[i], "flux:R_0603", "1k",
                          x=67.31 + i * 10.16, y=73.66,
                          pin_nets={"1": net, "2": "GND"}))
    # caps: LDO in/out + EN filter
    sch.place(SymInst("C1", "flux:C_0603", "10uF", x=82.55, y=73.66,
                      pin_nets={"1": "5V", "2": "GND"}))
    sch.place(SymInst("C2", "flux:C_0603", "10uF", x=87.63, y=73.66,
                      pin_nets={"1": "3V3", "2": "GND"}))
    sch.place(SymInst("C3", "flux:C_0603", "100nF", x=92.71, y=73.66,
                      pin_nets={"1": "EN", "2": "GND"}))
    # headers
    for r, row in enumerate(p.header_nets()):
        pins = {str(i + 1): n for i, n in enumerate(row)}
        pins.update({str(len(row) + i + 1): "GND" for i in range(14 - len(row))})
        sch.place(SymInst(f"J{r+1}", "flux:HDR14", f"HDR-{r+1}",
                          x=104.14 + r * 10.16, y=127.0, pin_nets=pins))
    if p.prog_header:
        sch.place(SymInst("J3", "flux:HDR4", "HDR-PROG", x=50.8, y=154.94,
                          pin_nets={"1": "3V3", "2": "GND", "3": "TX0", "4": "RX0"}))
    sch.rails = ["5V", "GND"]
    del header_ios, x0, y0
    return sch


# ---------------------------------------------------------------- board
def board(p: DevBoardParams) -> Board:
    header_ios = {n for row in p.header_nets() for n in row}
    led_r_refs = ["R1"] + (["R4"] if p.leds > 1 else [])
    all_nets = ["5V", "3V3", "GND", "EN", "USB_DP", "USB_DM", "TX0", "RX0",
                "CC1", "CC2", "LED_PWR", "LED_USER", "IO0"] + sorted(header_ios)
    b = Board(p.name, p.w_mm, p.h_mm, sorted(set(all_nets)))
    esp_lib = ROOT / "libs"
    pl = placement(p)

    mod_nets = _mod_nets(p)
    for name, num in legacy.MOD_PINS.items():
        if name == "IO0":
            mod_nets[str(num)] = "IO0"
    b.place(legacy.EspFp(esp_lib, "Espressif", "ESP32-S31-WROOM-3", "MOD1",
                         "ESP32-S31-WROOM-3U", *pl["MOD1"], mod_nets))
    x, y, r = pl["J4"]
    b.place(Fp("Connector_USB", "USB_C_Receptacle_HRO_TYPE-C-31-M-12",
               "J4", "USB-C", x, y, r,
               pad_nets={"A4": "5V", "A9": "5V", "B4": "5V", "B9": "5V",
                         "A1": "GND", "A12": "GND", "B1": "GND", "B12": "GND",
                         "A6": "USB_DP", "B6": "USB_DP",
                         "A7": "USB_DM", "B7": "USB_DM",
                         "A5": "CC1", "B5": "CC2", "SH": "GND"}))
    x, y, r = pl["U1"]
    b.place(Fp("Package_TO_SOT_Smd", "SOT-23-5", "U1", "AP2112K-3.3", x, y, r,
               pad_nets={"1": "5V", "2": "GND", "3": "5V", "5": "3V3"}))
    btn_nets = [{"1": "EN", "2": "GND"}, {"1": "IO0", "2": "GND"}]
    for i in range(p.buttons):
        x, y, r = pl[f"SW{i+1}"]
        b.place(Fp("Button_Switch_SMD", "SW_Push_1P1T_NO_CK_KMR2",
                   f"SW{i+1}", "RESET" if i == 0 else "BOOT", x, y, r,
                   pad_nets=btn_nets[min(i, 1)]))
    led_sources = ["3V3", "IO9"][:p.leds]
    for i, src in enumerate(led_sources):
        net = _led_net(i)
        x, y, r = pl[f"D{i+1}"]
        b.place(Fp("LED_SMD", "LED_0603_1608Metric", f"D{i+1}",
                   "RED" if i == 0 else "BLUE", x, y, r,
                   pad_nets={"1": src, "2": net}))
        x, y, r = pl[led_r_refs[i]]
        b.place(Fp("Resistor_SMD", "R_0603_1608Metric", led_r_refs[i], "1k",
                   x, y, r, pad_nets={"1": net, "2": "GND"}))
    passives = [
        ("R2", "R_0603_1608Metric", "Resistor_SMD", "10k", {"1": "3V3", "2": "EN"}),
        ("R5", "R_0603_1608Metric", "Resistor_SMD", "5.1k", {"1": "CC1", "2": "GND"}),
        ("R6", "R_0603_1608Metric", "Resistor_SMD", "5.1k", {"1": "CC2", "2": "GND"}),
        ("C1", "C_0805_2012Metric", "Capacitor_SMD", "10uF", {"1": "5V", "2": "GND"}),
        ("C2", "C_0805_2012Metric", "Capacitor_SMD", "10uF", {"1": "3V3", "2": "GND"}),
        ("C3", "C_0603_1608Metric", "Capacitor_SMD", "100nF", {"1": "EN", "2": "GND"}),
    ]
    if p.buttons > 1:
        passives.append(("R3", "R_0603_1608Metric", "Resistor_SMD", "10k",
                         {"1": "3V3", "2": "IO0"}))
    for ref, fp, lib, val, nets in passives:
        if ref not in pl:
            continue
        x, y, r = pl[ref]
        b.place(Fp(lib, fp, ref, val, x, y, r, pad_nets=nets))
    hdr_fp = {8: "PinHeader_1x08_P2.54mm_Vertical",
              10: "PinHeader_1x10_P2.54mm_Vertical",
              14: "PinHeader_1x14_P2.54mm_Vertical"}
    fp_name = hdr_fp.get(p.header_pins, "PinHeader_1x08_P2.54mm_Vertical")
    for r_i, row in enumerate(p.header_nets()):
        ref = f"J{r_i + 1}"
        x, y, r = pl[ref]
        nets = {str(i + 1): n for i, n in enumerate(row)}
        b.place(Fp("Connector_PinHeader_2.54mm", fp_name, ref,
                   f"HDR-{r_i + 1}", x, y, r, pad_nets=nets))
    if p.prog_header:
        x, y, r = pl["J3"]
        b.place(Fp("Connector_PinHeader_2.54mm", "PinHeader_1x04_P2.54mm_Vertical",
                   "J3", "HDR-PROG", x, y, r,
                   pad_nets={"1": "3V3", "2": "GND", "3": "TX0", "4": "RX0"}))
    b.ground_zones()
    return b


def write_all(p: DevBoardParams, project_dir: Path) -> dict:
    from fluxpcb.kicadgen.schematic import write as sch_write
    from fluxpcb.kicadgen.pcb import write as pcb_write
    sch_dir = project_dir / "schematic" / "board"
    sch_write(schematic(p), sch_dir / f"{p.name}.kicad_sch")
    pcb_dir = project_dir / "pcb" / "board"
    pcb_write(board(p), pcb_dir / f"{p.name}.kicad_pcb")
    pl = placement(p)
    import json
    (pcb_dir / "placement.json").write_text(
        json.dumps({k: list(v) for k, v in pl.items()}, ensure_ascii=False, indent=1),
        encoding="utf-8")
    return {"schematic": str(sch_dir / f"{p.name}.kicad_sch"),
            "pcb": str(pcb_dir / f"{p.name}.kicad_pcb"),
            "placement": {k: list(v) for k, v in pl.items()}}
