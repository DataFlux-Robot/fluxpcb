# -*- coding: utf-8 -*-
"""ESP32-S31-WROOM-3U dev board — Pi Zero size (65x30 mm), single board.

Every pin number verified against the official Espressif datasheet
(ESP32-S31-WROOM-3/3U Datasheet, Table 3-1; local copy:
reference/ESP32-S31-WROOM-3.pdf). AP2112K-3.3 pinout from Diodes datasheet.
Footprints: Espressif official kicad-libraries (module) + KiCad official libs.
"""
from __future__ import annotations

from pathlib import Path

from fluxnt.kicadgen.pcb import Board, Fp
from fluxnt.kicadgen.parts_lib import P
from fluxnt.kicadgen.schematic import Pin, Schematic, SymDef, SymInst

ROOT = Path(__file__).resolve().parent.parent.parent

# ---- WROOM-3 pin map (datasheet Table 3-1, verified) ----
MOD_PINS = {
    "GND": [1, 2, 26, 47, 48],
    "3V3": [3, 4],
    "EN": 5,
    "IO2": 6, "IO3": 7, "IO0": 8, "IO1": 9, "IO4": 10, "IO5": 11,
    "IO6": 12, "IO7": 13, "IO8": 14, "IO9": 15, "IO10": 16, "IO11": 17,
    "IO12": 18, "IO13": 19, "IO14": 20, "IO15": 21, "IO16": 22,
    "IO17": 23, "IO18": 24, "IO19": 25,
    "SD_D0": 27, "SD_D1": 28, "SD_D2": 29, "SD_D3": 30,
    "SD_CLK": 31, "SD_CMD": 32,
    "USB_DP": 40, "USB_DM": 41,
    "IO33": 42, "IO34": 43, "IO35": 44, "IO36": 45, "IO37": 46,
    "IO38": 49, "IO39": 50, "IO40": 51, "IO42": 52, "IO43": 53,
    "IO44": 54, "IO45": 55, "IO46": 56, "IO47": 57, "IO48": 58,
    "IO49": 59, "IO50": 60, "IO51": 61, "IO52": 62, "IO53": 63,
    "IO54": 64, "IO55": 65, "IO56": 66, "IO57": 67,
    "TX0": 68, "RX0": 69, "IO60": 70, "IO61": 71,
}

J1_PINS = ["5V", "3V3", "GND", "EN", "IO2", "IO3", "IO4", "IO5",
           "IO6", "IO7", "IO8", "IO9", "IO35", "IO36"]
J2_PINS = ["GND", "IO10", "IO11", "IO12", "IO13", "IO14", "IO15", "IO16",
           "IO17", "IO18", "IO19", "IO33", "IO34", "IO37"]

NETS = sorted(set(
    ["5V", "3V3", "GND", "USB_DP", "USB_DM", "EN", "TX0", "RX0", "LED_USER",
     "CC1", "CC2"] + J1_PINS + J2_PINS))


def _module_symbol() -> SymDef:
    """Module symbol carrying exactly the pins this board uses."""
    pins = []
    used = set(J1_PINS + J2_PINS) | {"EN", "USB_DP", "USB_DM", "TX0", "RX0"}
    for name, num in MOD_PINS.items():
        if name == "GND":
            etype = "power_in"
        elif name == "3V3":
            etype = "power_in"
        elif name not in used:
            continue
        else:
            etype = "passive"
        if isinstance(num, list):
            for n in num:
                pins.append(Pin(str(n), name, etype))
        else:
            pins.append(Pin(str(num), name, etype))
    return SymDef("flux:ESP32-S31-WROOM-3U", pins, ref_prefix="MOD")


def schematic() -> Schematic:
    sch = Schematic(title="esp32s31-dev", project="esp32s31-dev")
    sch.add_def(_module_symbol())
    sch.add_def(SymDef("flux:USB_C_16P", [
        P("A4", "VBUS", "power_in"), P("A9", "VBUS2", "power_in"),
        P("B4", "VBUS3", "power_in"), P("B9", "VBUS4", "power_in"),
        P("A1", "GND1", "power_in"), P("A12", "GND2", "power_in"),
        P("B1", "GND3", "power_in"), P("B12", "GND4", "power_in"),
        P("A6", "D+_1"), P("B6", "D+_2"),
        P("A7", "D-_1"), P("B7", "D-_2"),
        P("A5", "CC1"), P("B5", "CC2"),
        P("S1", "SHIELD", "power_in")], ref_prefix="J"))
    sch.add_def(SymDef("flux:AP2112K", [
        P(1, "VIN", "power_in"), P(2, "GND", "power_in"), P(3, "EN"),
        P(4, "NC"), P(5, "VOUT", "power_out")], ref_prefix="U"))
    sch.add_def(SymDef("flux:SW_PUSH", [P(1, "A"), P(2, "B")], ref_prefix="SW"))
    sch.add_def(SymDef("flux:LED", [P(1, "A"), P(2, "K")], ref_prefix="D"))
    sch.add_def(SymDef("flux:R_0603", [P(1, "A"), P(2, "B")], ref_prefix="R"))
    sch.add_def(SymDef("flux:C_0603", [P(1, "A"), P(2, "B")], ref_prefix="C"))
    sch.add_def(SymDef("flux:HDR14", [P(i, f"P{i}") for i in range(1, 15)],
                       ref_prefix="J"))
    sch.add_def(SymDef("flux:HDR4", [P(i, f"P{i}") for i in range(1, 5)],
                       ref_prefix="J"))

    # MOD1: nets per pin
    mod_nets = {}
    for name, num in MOD_PINS.items():
        if name == "GND":
            targets = [n for n in ([num] if isinstance(num, int) else num)]
            for n in targets:
                mod_nets[str(n)] = "GND"
        elif name == "3V3":
            for n in num:
                mod_nets[str(n)] = "3V3"
        elif name in ("EN", "USB_DP", "USB_DM", "TX0", "RX0") or \
                name in set(J1_PINS + J2_PINS):
            mod_nets[str(num)] = name
    sch.place(SymInst("MOD1", "flux:ESP32-S31-WROOM-3U",
                      "ESP32-S31-WROOM-3U", x=139.7, y=129.54,
                      pin_nets=mod_nets))
    # J1 USB-C
    sch.place(SymInst("J4", "flux:USB_C_16P", "USB-C", x=50.8, y=129.54,
                      pin_nets={"A4": "5V", "A9": "5V", "B4": "5V", "B9": "5V",
                                "A1": "GND", "A12": "GND", "B1": "GND",
                                "B12": "GND", "A6": "USB_DP", "B6": "USB_DP",
                                "A7": "USB_DM", "B7": "USB_DM",
                                "A5": "CC1", "B5": "CC2", "S1": "GND"}))
    # U1 AP2112K-3.3
    sch.place(SymInst("U1", "flux:AP2112K", "AP2112K-3.3", x=68.58, y=129.54,
                      pin_nets={"1": "5V", "2": "GND", "3": "5V",
                                "4": "NC_NET", "5": "3V3"}))
    # SW1 reset(EN), SW2 boot(IO0)
    sch.place(SymInst("SW1", "flux:SW_PUSH", "RESET", x=50.8, y=101.6,
                      pin_nets={"1": "EN", "2": "GND"}))
    sch.place(SymInst("SW2", "flux:SW_PUSH", "BOOT", x=58.42, y=101.6,
                      pin_nets={"1": "IO0", "2": "GND"}))
    # R2 EN pull-up, R3 IO0 pull-up, R1/R4 LED resistors, CC pulldowns R5/R6
    sch.place(SymInst("R2", "flux:R_0603", "10k", x=66.04, y=101.6,
                      pin_nets={"1": "3V3", "2": "EN"}))
    sch.place(SymInst("R3", "flux:R_0603", "10k", x=73.66, y=101.6,
                      pin_nets={"1": "3V3", "2": "IO0"}))
    sch.place(SymInst("R5", "flux:R_0603", "5.1k", x=50.8, y=73.66,
                      pin_nets={"1": "CC1", "2": "GND"}))
    sch.place(SymInst("R6", "flux:R_0603", "5.1k", x=55.88, y=73.66,
                      pin_nets={"1": "CC2", "2": "GND"}))
    # LEDs: D1 power (3V3->GND via R1 1k), D2 user on IO9 via R4
    sch.place(SymInst("D1", "flux:LED", "RED", x=62.23, y=73.66,
                      pin_nets={"1": "3V3", "2": "LED_PWR"}))
    sch.place(SymInst("R1", "flux:R_0603", "1k", x=67.31, y=73.66,
                      pin_nets={"1": "LED_PWR", "2": "GND"}))
    sch.place(SymInst("D2", "flux:LED", "BLUE", x=72.39, y=73.66,
                      pin_nets={"1": "LED_USER", "2": "GND"}))
    sch.place(SymInst("R4", "flux:R_0603", "1k", x=77.47, y=73.66,
                      pin_nets={"1": "IO9", "2": "LED_USER"}))
    # decoupling
    sch.place(SymInst("C1", "flux:C_0603", "10uF", x=82.55, y=73.66,
                      pin_nets={"1": "5V", "2": "GND"}))
    sch.place(SymInst("C2", "flux:C_0603", "10uF", x=87.63, y=73.66,
                      pin_nets={"1": "3V3", "2": "GND"}))
    sch.place(SymInst("C3", "flux:C_0603", "100nF", x=92.71, y=73.66,
                      pin_nets={"1": "EN", "2": "GND"}))
    # headers
    sch.place(SymInst("J1", "flux:HDR14", "HDR-TOP", x=104.14, y=129.54,
                      pin_nets={str(i + 1): n for i, n in enumerate(J1_PINS)}))
    sch.place(SymInst("J2", "flux:HDR14", "HDR-BOT", x=114.3, y=101.6,
                      pin_nets={str(i + 1): n for i, n in enumerate(J2_PINS)}))
    sch.place(SymInst("J3", "flux:HDR4", "HDR-PROG", x=50.8, y=154.94,
                      pin_nets={"1": "3V3", "2": "GND", "3": "TX0",
                                "4": "RX0"}))
    sch.rails = ["5V", "GND"]
    return sch


class EspFp(Fp):
    """Fp with an overridable library directory (Espressif local lib)."""

    def __init__(self, lib_dir: Path, lib: str, name: str, ref: str, value: str,
                 x: float, y: float, rot: float = 0.0, pad_nets=None):
        self.path = Path(lib_dir) / f"{lib}.pretty" / f"{name}.kicad_mod"
        from fluxnt.kicadgen.pcb import parse
        self.tree = parse(self.path.read_text(encoding="utf-8",
                                              errors="replace"))
        self.tree[1] = ("s", f"{lib}:{name}")
        self.lib, self.name = lib, name
        self.ref, self.value = ref, value
        self.x, self.y, self.rot = x, y, rot
        self.pad_nets = pad_nets or {}


def board() -> Board:
    b = Board("esp32s31-dev", 65.0, 30.0, NETS + ["LED_PWR", "NC_NET"])
    esp_lib = ROOT / "libs"
    # MOD1: module body 22x30 at left; footprint origin center of module
    mod_nets = {}
    for name, num in MOD_PINS.items():
        if name == "GND":
            for n in (num if isinstance(num, list) else [num]):
                mod_nets[str(n)] = "GND"
        elif name == "3V3":
            for n in num:
                mod_nets[str(n)] = "3V3"
        elif name in ("EN", "USB_DP", "USB_DM", "TX0", "RX0") or \
                name in set(J1_PINS + J2_PINS):
            mod_nets[str(num)] = name
    b.place(EspFp(esp_lib, "Espressif", "ESP32-S31-WROOM-3", "MOD1",
                  "ESP32-S31-WROOM-3U", 17.0, 15.0, 90.0, mod_nets))
    # J4 USB-C at bottom edge (pads face -y: place origin below edge)
    b.place(Fp("Connector_USB", "USB_C_Receptacle_HRO_TYPE-C-31-M-12",
               "J4", "USB-C", 62.8, 15.0, 90.0,
               pad_nets={"A4": "5V", "A9": "5V", "B4": "5V", "B9": "5V",
                         "A1": "GND", "A12": "GND", "B1": "GND", "B12": "GND",
                         "A6": "USB_DP", "B6": "USB_DP",
                         "A7": "USB_DM", "B7": "USB_DM",
                         "A5": "CC1", "B5": "CC2", "SH": "GND"}))
    # U1 AP2112K
    b.place(Fp("Package_TO_SOT_Smd", "SOT-23-5", "U1", "AP2112K-3.3",
               32.0, 15.0, 0.0,
               pad_nets={"1": "5V", "2": "GND", "3": "5V", "5": "3V3"}))
    # buttons
    b.place(Fp("Button_Switch_SMD", "SW_Push_1P1T_NO_CK_KMR2", "SW1",
               "RESET", 42.0, 24.0, 0.0, pad_nets={"1": "EN", "2": "GND"}))
    b.place(Fp("Button_Switch_SMD", "SW_Push_1P1T_NO_CK_KMR2", "SW2",
               "BOOT", 48.0, 24.0, 0.0, pad_nets={"1": "IO0", "2": "GND"}))
    # LEDs
    b.place(Fp("LED_SMD", "LED_0603_1608Metric", "D1", "RED",
               42.0, 5.0, 0.0, pad_nets={"1": "3V3", "2": "LED_PWR"}))
    b.place(Fp("LED_SMD", "LED_0603_1608Metric", "D2", "BLUE",
               46.0, 5.0, 0.0, pad_nets={"1": "LED_USER", "2": "GND"}))
    # passives (1.27-grid positions)
    passives = [
        ("R1", "R_0603_1608Metric", "Resistor_SMD", "1k",
         {"1": "LED_PWR", "2": "GND"}, 36.0, 8.0),
        ("R2", "R_0603_1608Metric", "Resistor_SMD", "10k",
         {"1": "3V3", "2": "EN"}, 44.0, 20.0),
        ("R3", "R_0603_1608Metric", "Resistor_SMD", "10k",
         {"1": "3V3", "2": "IO0"}, 48.0, 20.0),
        ("R4", "R_0603_1608Metric", "Resistor_SMD", "1k",
         {"1": "IO9", "2": "LED_USER"}, 50.0, 5.0),
        ("R5", "R_0603_1608Metric", "Resistor_SMD", "5.1k",
         {"1": "CC1", "2": "GND"}, 32.5, 19.0),
        ("R6", "R_0603_1608Metric", "Resistor_SMD", "5.1k",
         {"1": "CC2", "2": "GND"}, 35.5, 19.0),
        ("C1", "C_0805_2012Metric", "Capacitor_SMD", "10uF",
         {"1": "5V", "2": "GND"}, 36.0, 15.0),
        ("C2", "C_0805_2012Metric", "Capacitor_SMD", "10uF",
         {"1": "3V3", "2": "GND"}, 36.0, 11.0),
        ("C3", "C_0603_1608Metric", "Capacitor_SMD", "100nF",
         {"1": "EN", "2": "GND"}, 39.0, 20.0),
    ]
    for ref, fp, lib, val, nets, x, y in passives:
        b.place(Fp(lib, fp, ref, val, x, y, 0.0, pad_nets=nets))
    # headers: J1 top edge, J2 bottom edge (right half, x 30..63)
    for tag, pins, y in (("J1", J1_PINS, 27.94), ("J2", J2_PINS, 2.54)):
        nets = {str(i + 1): n for i, n in enumerate(pins)}
        b.place(Fp("Connector_PinHeader_2.54mm",
                   "PinHeader_1x14_P2.54mm_Vertical", tag,
                   "HDR-" + ("TOP" if tag == "J1" else "BOT"),
                   25.5, y, 90.0, pad_nets=nets))
    # J3 prog header right edge area
    b.place(Fp("Connector_PinHeader_2.54mm", "PinHeader_1x04_P2.54mm_Vertical",
               "J3", "HDR-PROG", 48.0, 8.5, 90.0,
               pad_nets={"1": "3V3", "2": "GND", "3": "TX0", "4": "RX0"}))
    return b


def write_all(project_dir: Path) -> dict:
    from fluxnt.kicadgen.schematic import write as sch_write
    from fluxnt.kicadgen.pcb import write as pcb_write
    sch_dir = project_dir / "schematic" / "board"
    sch_write(schematic(), sch_dir / "esp32s31-dev.kicad_sch")
    pcb_dir = project_dir / "pcb" / "board"
    pcb_dir.mkdir(parents=True, exist_ok=True)
    pcb_write(board(), pcb_dir / "esp32s31-dev.kicad_pcb")
    # project file + lib tables for PCB dir
    import json
    import shutil
    pro = sch_dir / "esp32s31-dev.kicad_pro"
    (pcb_dir / "esp32s31-dev.kicad_pro").write_text(
        pro.read_text(encoding="utf-8"), encoding="utf-8")
    fp_tbl = Path(r"C:\Users\zhang\AppData\Local\Programs\KiCad\10.0"
                  r"\share\kicad\template\fp-lib-table")
    shutil.copy2(fp_tbl, pcb_dir / "fp-lib-table")
    # local Espressif footprint lib next to the pcb project
    esp_dst = pcb_dir / "Espressif.pretty"
    if not esp_dst.exists():
        esp_dst.mkdir()
        for f in (ROOT / "libs" / "Espressif.pretty").glob("*.kicad_mod"):
            shutil.copy2(f, esp_dst / f.name)
    return {"sch": str(sch_dir), "pcb": str(pcb_dir)}
