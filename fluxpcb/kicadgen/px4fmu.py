# -*- coding: utf-8 -*-
"""STM32N657X0 (TFBGA264) PX4 flight-controller FMU generator.

All MCU data from ST-official STM32N657X0HxQ.xml (three-way verified);
sensors from ArduPilot/GUDrones/AUXSPACE FC repos. FMUv6-style full
redundancy: ICM-42688-P (SPI1) + BMI055 (SPI2) + BMP388 x2 (I2C1, address
split) + IST8310 (I2C2); FDCAN1, USART2/3+UART5, USB1_HS, TIM PWM x6,
SWD, dual crystals. 55x28mm 6-layer.

Power tree: 5V -> TLV62569 buck -> 3V3 (VDD/VDDIO/VDDSMPS/VDD33USB);
internal SMPS: VLXSMPS -> 2.2uH -> VDDCORE; VDDA via ferrite from 3V3.
"""
from __future__ import annotations

import json
from pathlib import Path

from fluxpcb.kicadgen.pcb import Board, Fp, parse
from fluxpcb.kicadgen.parts_lib import P
from fluxpcb.kicadgen.schematic import Pin, Schematic, SymDef, SymInst

ROOT = Path(__file__).resolve().parent.parent.parent
N6LIB = ROOT / "reference" / "n6lib"
FCLIB = ROOT / "reference" / "fculib_v8"

W, H = 55.0, 28.0


def _alloc():
    d = json.loads((N6LIB / "px4_pin_alloc.json").read_text(encoding="utf-8"))
    return d["alloc"], d["pins"]


def mcu_ball_nets() -> dict[str, str]:
    """ball -> net for every used MCU ball (peripherals + power domains)."""
    alloc, pins = _alloc()
    nets = {}
    for sig, a in alloc.items():
        ball, gpio = a["ball"], a["pin"]
        if sig.startswith("SPI1_"):
            nets[ball] = "SPI1_" + sig.split("_", 1)[1]     # SPI1_SCK...
        elif sig.startswith("SPI2_"):
            nets[ball] = "SPI2_" + sig.split("_", 1)[1]
        elif sig in ("I2C1_SCL", "I2C1_SDA", "I2C2_SCL", "I2C2_SDA"):
            nets[ball] = sig
        elif sig in ("FDCAN1_TX", "FDCAN1_RX"):
            nets[ball] = sig
        elif sig in ("USART2_TX", "USART2_RX", "USART3_TX", "USART3_RX",
                     "UART5_TX", "UART5_RX"):
            nets[ball] = sig
        elif sig.startswith("TIM"):
            nets[ball] = "PWM_" + sig.replace("TIM", "").replace("_CH", "")
        elif "OTG" in sig:
            nets[ball] = "USB_DP" if sig.endswith("DP") else "USB_DM"
        elif "OSC_IN" == sig[-6:] or "OSC_OUT" == sig[-7:]:
            nets[ball] = "OSC_IN" if sig.endswith("OSC_IN") else "OSC_OUT"
        elif "OSC32" in sig:
            nets[ball] = "OSC32_IN" if sig.endswith("IN") else "OSC32_OUT"
        else:
            nets[ball] = gpio
    # power domains from the official pin DB
    for ball, info in pins.items():
        n = info["name"].upper()
        if n.startswith("VSS"):
            nets[ball] = "GND"
        elif n.startswith("VDDSMPS"):
            nets[ball] = "3V3"
        elif n.startswith("VLXSMPS"):
            nets[ball] = "VDDCORE_SW"
        elif n.startswith("VDDCORE"):
            nets[ball] = "VDDCORE"
        elif n.startswith("VDDA") or n in ("VREF+", "VREF-"):
            nets[ball] = "VDDA"
        elif n.startswith("VDD") or n.startswith("VBAT"):
            nets[ball] = "3V3"
        elif n == "PDR_ON" or "NRST" in n:
            nets[ball] = "NRST"
    return nets


class LocalFp(Fp):
    """Fp over a local footprint file (no lib prefix)."""

    def __init__(self, path: Path, ref: str, value: str,
                 x: float, y: float, rot: float = 0.0, pad_nets=None):
        self.path = path
        self.tree = parse(path.read_text(encoding="utf-8", errors="replace"))
        self.tree[1] = ("s", f"flux:{path.stem}")
        self.ref, self.value = ref, value
        self.x, self.y, self.rot = x, y, rot
        self.pad_nets = pad_nets or {}


def footprints() -> dict:
    """ref -> footprint file for analytic (pre-DRC) validation."""
    hdr127 = "Connector_PinHeader_1.27mm"
    hdr254 = "Connector_PinHeader_2.54mm"
    kicad_fps = Path(r"C:/Users/zhang/AppData/Local/Programs/KiCad/10.0/share/kicad/footprints")
    if not kicad_fps.exists():
        kicad_fps = ROOT / "libs" / "kicad-footprints"

    def k(lib, name):
        return kicad_fps / f"{lib}.pretty" / f"{name}.kicad_mod"

    return {
        "U1": N6LIB / "BGA264_STM32N657X0H3Q.kicad_mod",
        "U2": FCLIB / "ICM-42688-P.kicad_mod",
        "U3": FCLIB / "ICM-42688-P.kicad_mod",
        "U4": FCLIB / "BMP388.kicad_mod",
        "U5": FCLIB / "BMP388.kicad_mod",
        "U6": FCLIB / "IST8310.kicad_mod",
        "U7": k("Package_TO_SOT_Smd", "SOT-23-5"),
        "U8": k("Package_SO", "SOIC-8_3.9x4.9mm_P1.27mm"),
        "L1": k("Inductor_SMD", "L_1210_3225Metric"),
        "FB1": k("Resistor_SMD", "R_0603_1608Metric"),
        "Y1": k("Crystal", "Crystal_SMD_5032-2Pin_5.0x3.2mm"),
        "Y2": k("Crystal", "Crystal_SMD_3215-2Pin_3.2x1.5mm"),
        "J1": k(hdr127, "PinHeader_1x06_P1.27mm_Vertical"),
        "J2": k(hdr127, "PinHeader_1x06_P1.27mm_Vertical"),
        "J3": k(hdr127, "PinHeader_1x04_P1.27mm_Vertical"),
        "J4": k("Connector_USB", "USB_C_Receptacle_HRO_TYPE-C-31-M-12"),
        "J5": k(hdr127, "PinHeader_2x05_P1.27mm_Vertical"),
        "J6": k(hdr254, "PinHeader_2x07_P2.54mm_Vertical"),
        "J7": k(hdr254, "PinHeader_1x03_P2.54mm_Vertical"),
        "D1": k("LED_SMD", "LED_0603_1608Metric"),
        "R1": k("Resistor_SMD", "R_0603_1608Metric"),
    }


def validate_placement(place=None) -> list:
    """Analytic pre-flight: overlap/edge/connector checks in milliseconds."""
    from fluxpcb.kicadgen.hierplace import validate
    return validate(place or placement(), footprints(), W, H,
                    connector_refs=("J4",))


def placement() -> dict[str, tuple[float, float, float]]:
    """Cluster-based FC layout: MCU/vibration-zone cluster center, sensor
    cluster around it, power cluster corner, connector cluster on edges."""
    return {
        "U1":   (27.5, 14.0, 0.0),     # STM32N657 TFBGA264 center
        "U2":   (17.0, 12.0, 0.0),     # ICM-42688-P (IMU1)
        "U3":   (17.0, 18.0, 0.0),     # BMI055 (IMU2)
        "U4":   (20.0, 5.4, 0.0),      # BMP388 #1 (clear of BGA bbox)
        "U5":   (45.0, 21.5, 0.0),     # BMP388 #2 (clear of J6)
        "U6":   (33.0, 5.4, 0.0),      # IST8310 mag
        "U7":   (47.5, 6.5, 90.0),     # TLV62569 buck (corner, away from IMU)
        "U8":   (39.0, 4.2, 90.0),     # SN65HVD230 CAN (top zone)
        "L1":   (45.0, 10.5, 0.0),     # 2.2uH VDDCORE
        "FB1":  (52.5, 22.5, 0.0),     # VDDA ferrite (0603)
        "Y1":   (50.0, 26.0, 0.0),     # 24MHz (right of J6 span)
        "Y2":   (8.0, 6.5, 0.0),       # 32.768k (top-left free pocket)
        "J1":   (2.0, 2.5, 0.0),       # GPS (1x06 1.27, origin pad1)
        "J2":   (2.0, 10.5, 0.0),      # TELEM (1x06)
        "J3":   (2.0, 19.0, 0.0),      # RC/SBUS (1x04)
        "J4":   (52.6, 14.0, 90.0),    # USB-C right edge (SH pad clear)
        "J5":   (5.5, 21.5, 0.0),      # SWD 2x05 1.27
        "J6":   (26.0, 25.0, 90.0),    # PWM 2x07 2.54 along bottom edge
        "J7":   (12.5, 26.0, 90.0),    # DEBUG/BOOT header
        "D1":   (51.5, 20.0, 0.0),     # status LED
        "R1":   (48.3, 20.0, 0.0),     # LED resistor 1k
    }


def board() -> Board:
    alloc, pins = _alloc()
    ball_nets = mcu_ball_nets()
    nets = ["5V", "3V3", "GND", "VDDCORE", "VDDCORE_SW", "VDDA", "NRST",
            "OSC_IN", "OSC_OUT", "OSC32_IN", "OSC32_OUT",
            "USB_DP", "USB_DM", "FDCAN1_TX", "FDCAN1_RX", "CAN_H", "CAN_L",
            "SPI1_SCK", "SPI1_MISO", "SPI1_MOSI", "SPI1_NSS",
            "SPI2_SCK", "SPI2_MISO", "SPI2_MOSI", "SPI2_NSS",
            "I2C1_SCL", "I2C1_SDA", "I2C2_SCL", "I2C2_SDA",
            "USART2_TX", "USART2_RX", "USART3_TX", "USART3_RX",
            "UART5_TX", "UART5_RX",
            "IMU1_INT", "IMU2_INT1", "BARO1_INT", "BARO2_INT", "MAG_INT",
            "PWM_CH1", "PWM_CH2", "PWM_CH3", "PWM_CH4", "PWM_CH5", "PWM_CH6",
            "BOOT0", "LED_ACT", "SWDIO", "SWCLK"]
    b = Board("stm32n6-px4", W, H, sorted(set(nets)), n_layers=6)
    pl = placement()

    # ensure the generated BGA footprint exists
    fp_bga = N6LIB / "BGA264_STM32N657X0H3Q.kicad_mod"
    if not fp_bga.exists():
        import subprocess, sys
        subprocess.run([sys.executable, str(N6LIB / "gen_footprint.py")], check=True)
    b.place(LocalFp(fp_bga, "U1", "STM32N657X0H3Q", *pl["U1"],
                    pad_nets=ball_nets))

    # sensors — pads per the fetched footprints
    # dual-IMU redundancy via two ICM-42688-P (verified pin table;
    # BMI055 kept as reference asset — its dual-SPI pin map unverified)
    def imu_nets(prefix, int_net):
        return {"1": "GND", "5": "3V3", "8": "3V3", "6": "GND",
                "13": f"{prefix}_SCK", "14": f"{prefix}_MOSI",
                "1_": None, "12": f"{prefix}_NSS", "4": int_net}
    b.place(LocalFp(FCLIB / "ICM-42688-P.kicad_mod", "U2", "ICM-42688-P",
                    *pl["U2"], pad_nets={
                        "1": "SPI1_MISO", "5": "3V3", "8": "3V3", "6": "GND",
                        "13": "SPI1_SCK", "14": "SPI1_MOSI",
                        "12": "SPI1_NSS", "4": "IMU1_INT"}))
    b.place(LocalFp(FCLIB / "ICM-42688-P.kicad_mod", "U3", "ICM-42688-P",
                    *pl["U3"], pad_nets={
                        "1": "SPI2_MISO", "5": "3V3", "8": "3V3", "6": "GND",
                        "13": "SPI2_SCK", "14": "SPI2_MOSI",
                        "12": "SPI2_NSS", "4": "IMU2_INT1"}))

    for ref, pos, sdo in (("U4", "U4", "GND"), ("U5", "U5", "3V3")):
        b.place(LocalFp(FCLIB / "BMP388.kicad_mod", ref, "BMP388",
                        *pl[pos], pad_nets={
                            "1": "3V3", "10": "3V3", "3": "GND", "8": "GND",
                            "9": "GND", "2": "I2C1_SCL", "4": "I2C1_SDA",
                            "5": sdo, "6": "3V3",
                            "7": "BARO1_INT" if sdo == "GND" else "BARO2_INT"}))
    b.place(LocalFp(FCLIB / "IST8310.kicad_mod", "U6", "IST8310", *pl["U6"],
                    pad_nets={
                        "1": "I2C2_SCL", "16": "I2C2_SDA",
                        "2": "VDDA", "13": "VDDA",
                        "9": "GND", "11": "GND", "14": "VDDA",
                        "15": "MAG_INT"}))

    # 5V->3V3 via AP2112K LDO (proven family part; buck upgrade in roadmap)
    b.place(Fp("Package_TO_SOT_Smd", "SOT-23-5", "U7", "AP2112K-3.3",
               *pl["U7"], pad_nets={
                   "1": "5V", "2": "GND", "3": "5V", "5": "3V3"}))
    b.place(Fp("Package_SO", "SOIC-8_3.9x4.9mm_P1.27mm", "U8", "SN65HVD230",
               *pl["U8"], pad_nets={
                   "1": "FDCAN1_TX", "4": "FDCAN1_RX", "2": "GND",
                   "3": "3V3", "7": "CAN_H", "6": "CAN_L", "8": "3V3"}))
    b.place(Fp("Inductor_SMD", "L_1210_3225Metric", "L1", "2.2uH",
               *pl["L1"], pad_nets={"1": "VDDCORE_SW", "2": "VDDCORE"}))
    b.place(Fp("Resistor_SMD", "R_0603_1608Metric", "FB1", "600R@100M",
               *pl["FB1"], pad_nets={"1": "3V3", "2": "VDDA"}))
    b.place(Fp("Crystal", "Crystal_SMD_5032-2Pin_5.0x3.2mm", "Y1", "24MHz",
               *pl["Y1"], pad_nets={"1": "OSC_IN", "2": "GND"}))
    b.place(Fp("Crystal", "Crystal_SMD_3215-2Pin_3.2x1.5mm", "Y2", "32.768k",
               *pl["Y2"], pad_nets={"1": "OSC32_IN", "2": "OSC32_OUT"}))
    hdr127 = "Connector_PinHeader_1.27mm"
    hdr254 = "Connector_PinHeader_2.54mm"
    b.place(Fp(hdr127, "PinHeader_1x06_P1.27mm_Vertical", "J1", "GPS",
               *pl["J1"], pad_nets={"1": "3V3", "2": "USART3_TX",
                                    "3": "USART3_RX", "4": "I2C2_SCL",
                                    "5": "I2C2_SDA", "6": "GND"}))
    b.place(Fp(hdr127, "PinHeader_1x06_P1.27mm_Vertical", "J2", "TELEM",
               *pl["J2"], pad_nets={"1": "5V", "2": "USART2_TX",
                                    "3": "USART2_RX", "4": "GND",
                                    "5": "GND", "6": "GND"}))
    b.place(Fp(hdr127, "PinHeader_1x04_P1.27mm_Vertical", "J3", "RC",
               *pl["J3"], pad_nets={"1": "5V", "2": "UART5_RX",
                                    "3": "UART5_TX", "4": "GND"}))
    b.place(Fp("Connector_USB", "USB_C_Receptacle_HRO_TYPE-C-31-M-12",
               "J4", "USB-C", *pl["J4"],
               pad_nets={"A4": "5V", "A9": "5V", "B4": "5V", "B9": "5V",
                         "A1": "GND", "A12": "GND", "B1": "GND", "B12": "GND",
                         "A6": "USB_DP", "B6": "USB_DP",
                         "A7": "USB_DM", "B7": "USB_DM", "S1": "GND"}))
    b.place(Fp(hdr127, "PinHeader_2x05_P1.27mm_Vertical", "J5", "SWD",
               *pl["J5"], pad_nets={"1": "3V3", "2": "SWDIO", "3": "GND",
                                    "4": "SWCLK", "5": "GND", "6": "GND",
                                    "7": "GND", "8": "GND", "9": "NRST",
                                    "10": "GND"}))
    pwm = {"1": "PWM_CH1", "2": "PWM_CH2", "3": "PWM_CH3", "4": "PWM_CH4",
           "5": "PWM_CH5", "6": "PWM_CH6", "7": "GND", "8": "GND",
           "9": "GND", "10": "GND", "11": "GND", "12": "GND",
           "13": "5V", "14": "GND"}
    b.place(Fp(hdr254, "PinHeader_2x07_P2.54mm_Vertical", "J6", "PWM",
               *pl["J6"], pad_nets=pwm))
    b.place(Fp(hdr254, "PinHeader_1x03_P2.54mm_Vertical", "J7", "DEBUG",
               *pl["J7"], pad_nets={"1": "3V3", "2": "BOOT0", "3": "GND"}))
    b.place(Fp("LED_SMD", "LED_0603_1608Metric", "D1", "BLUE", *pl["D1"],
               pad_nets={"1": "3V3", "2": "LED_ACT"}))
    b.place(Fp("Resistor_SMD", "R_0603_1608Metric", "R1", "1k",
               *pl["R1"], pad_nets={"1": "LED_ACT", "2": "GND"}))
    # SWD from official XML: U6=PA13 SWDIO, T6=PA14 SWCLK
    bn = b.footprints[0].pad_nets
    bn["U6"] = "SWDIO"
    bn["T6"] = "SWCLK"
    b.ground_zones()
    return b


def write_board(project_dir: Path) -> Path:
    from fluxpcb.kicadgen.pcb import write as pcb_write
    issues = validate_placement()
    if issues:
        raise ValueError("layout pre-flight FAILED (analytic, board NOT written): "
                         + "; ".join(issues[:4]))
    out = project_dir / "pcb" / "board" / "stm32n6-px4.kicad_pcb"
    out.parent.mkdir(parents=True, exist_ok=True)
    pcb_write(board(), out)
    return out


# ================================================================ schematic
def mcu_symdef():
    """264-ball MCU symbol, two-column grid (132 pins/column) from the
    ST-official pin DB. VLXSMPS = power_out (internal SMPS switch node)."""
    _, pins_db = _alloc()
    # BGA row letters skip I/O/Q/S (confusable): A..H, J..N, P, R, T, U
    rows = list("ABCDEFGHJKLMNPRTU")
    order = [f"{r}{c}" for r in rows for c in range(1, 18)]
    pins = []
    per_col = 66          # 4 columns x 66 = 264; 165mm tall fits an A3 sheet
    xs = (-20.32, -10.16, 10.16, 20.32)
    kept = [b for b in order if b in pins_db]
    for i, ball in enumerate(kept):
        info = pins_db[ball]
        name, typ = info["name"], info["type"]
        nu = name.upper()
        if nu.startswith("VLXSMPS"):
            etype = "passive"   # multiple outputs on one net trip pin_to_pin
        elif typ == "Power" or nu.startswith(("VDD", "VSS", "VBAT")):
            etype = "power_in"
        else:
            etype = "passive"
        col = min(3, i // per_col)
        y = (per_col - 1 - (i % per_col)) * 2.54
        x = xs[col]
        ang = 0 if col < 2 else 180
        pins.append(Pin(ball, name, etype, x=x, y=y, angle=ang))
    return SymDef("flux:STM32N657X0", pins, ref_prefix="U", auto_layout=False)


def _sensor_symdef(name: str, pinmap: dict, lib: str) -> SymDef:
    pins = []
    for num, pname in pinmap.items():
        pu = str(pname).upper()
        if "VDD" in pu or pu in ("AVDD", "DVDD"):
            etype = "power_in"
        elif "VSS" in pu or "GND" in pu:
            etype = "power_in"
        else:
            etype = "passive"
        pins.append(Pin(str(num), str(pname), etype))
    return SymDef(f"flux:{lib}", pins, ref_prefix="U")


def schematic() -> Schematic:
    import json as _json
    ball_nets = mcu_ball_nets()
    ball_nets["U6"] = "SWDIO"
    ball_nets["T6"] = "SWCLK"
    sp = _json.loads((FCLIB / "sensor_pinmaps.json").read_text(encoding="utf-8"))

    sch = Schematic(title="stm32n6-px4", project="stm32n6-px4")
    mcu = mcu_symdef()
    sch.add_def(mcu)
    sch.add_def(_sensor_symdef("ICM-42688-P", sp["ICM-42688-P"], "ICM-42688-P"))
    sch.add_def(_sensor_symdef("BMP388", sp["BMP388"], "BMP388"))
    sch.add_def(_sensor_symdef("IST8310", sp["IST8310"], "IST8310"))
    sch.add_def(SymDef("flux:AP2112K", [P(1, "VIN", "power_in"),
                                        P(2, "GND", "power_in"), P(3, "EN"),
                                        P(5, "VOUT", "power_out")], ref_prefix="U"))
    sch.add_def(SymDef("flux:SOIC8_CAN", [P(i, f"P{i}") for i in range(1, 9)],
                       ref_prefix="U"))
    sch.add_def(SymDef("flux:IND", [P(1, "A"), P(2, "B")], ref_prefix="L"))
    sch.add_def(SymDef("flux:R_0603", [P(1, "A"), P(2, "B")], ref_prefix="R"))
    sch.add_def(SymDef("flux:LED", [P(1, "A"), P(2, "K")], ref_prefix="D"))
    sch.add_def(SymDef("flux:XTAL", [P(1, "A"), P(2, "B")], ref_prefix="Y"))
    for n, pins in (("HDR6", 6), ("HDR4", 4), ("HDR10", 10),
                    ("HDR14", 14), ("HDR3", 3)):
        sch.add_def(SymDef(f"flux:{n}", [P(i, f"P{i}") for i in range(1, pins + 1)],
                           ref_prefix="J"))
    sch.add_def(SymDef("flux:USB_C_16P", [
        P("A4", "VBUS"), P("A9", "VBUS"), P("B4", "VBUS"), P("B9", "VBUS"),
        P("A1", "GND"), P("A12", "GND"), P("B1", "GND"), P("B12", "GND"),
        P("A6", "D+"), P("A7", "D-"), P("B6", "D+"), P("B7", "D-"),
        P("S1", "SH")], ref_prefix="J"))

    # NC = balls with no net
    nc: set[str] = set()
    _, pins_db = _alloc()
    for ball, info in pins_db.items():
        if ball not in ball_nets:
            nc.add(ball)

    def place_nc(ref, lib, value, x, y, pin_nets):
        sd = sch.symdefs[lib]
        unused = {pp.number for pp in sd.pins if pp.number not in pin_nets}
        sch.place(SymInst(ref, lib, value, x=x, y=y,
                          pin_nets=pin_nets, nc_pins=unused))

    sch.place(SymInst("U1", "flux:STM32N657X0", "STM32N657X0H3Q",
                      x=210, y=150, pin_nets=ball_nets, nc_pins=nc))
    imu = {"1": "MISO", "5": "VDDIO", "8": "VDD", "6": "GND",
           "13": "SCK", "14": "MOSI", "12": "CS", "4": "INT"}
    for ref, px, intn in (("U2", 130, "IMU1_INT"), ("U3", 130, "IMU2_INT1")):
        pref = "SPI1" if ref == "U2" else "SPI2"
        nets = {k: ({"VDDIO": "3V3", "VDD": "3V3", "GND": "GND",
                     "MISO": f"{pref}_MISO", "MOSI": f"{pref}_MOSI",
                     "SCK": f"{pref}_SCK", "CS": f"{pref}_NSS",
                     "INT": intn}[v]) for k, v in imu.items()}
        place_nc(ref, "flux:ICM-42688-P", "ICM-42688-P",
                 px, 60 if ref == "U2" else 220, nets)
    for ref, y, sdo, intn in (("U4", 40, "GND", "BARO1_INT"),
                              ("U5", 240, "3V3", "BARO2_INT")):
        sch.place(SymInst(ref, "flux:BMP388", "BMP388", x=330, y=y, pin_nets={
            "1": "3V3", "10": "3V3", "3": "GND", "8": "GND", "9": "GND",
            "2": "I2C1_SCL", "4": "I2C1_SDA", "5": sdo, "6": "3V3",
            "7": intn}))
    place_nc("U6", "flux:IST8310", "IST8310", 330, 140, {
        "1": "I2C2_SCL", "16": "I2C2_SDA", "2": "VDDA", "13": "VDDA",
        "9": "GND", "11": "GND", "14": "VDDA", "15": "MAG_INT"})
    place_nc("U7", "flux:AP2112K", "AP2112K-3.3", 60, 60, {
        "1": "5V", "2": "GND", "3": "5V", "5": "3V3"})
    place_nc("U8", "flux:SOIC8_CAN", "SN65HVD230", 60, 110, {
        "1": "FDCAN1_TX", "4": "FDCAN1_RX", "2": "GND", "3": "3V3",
        "7": "CAN_H", "6": "CAN_L", "8": "3V3"})
    sch.place(SymInst("L1", "flux:IND", "2.2uH", x=60, y=160,
                      pin_nets={"1": "VDDCORE_SW", "2": "VDDCORE"}))
    sch.place(SymInst("FB1", "flux:R_0603", "600R@100M", x=60, y=200,
                      pin_nets={"1": "3V3", "2": "VDDA"}))
    sch.place(SymInst("Y1", "flux:XTAL", "24MHz", x=150, y=260,
                      pin_nets={"1": "OSC_IN", "2": "GND"}))
    sch.place(SymInst("Y2", "flux:XTAL", "32.768k", x=150, y=270,
                      pin_nets={"1": "OSC32_IN", "2": "OSC32_OUT"}))
    place_nc("J1", "flux:HDR6", "GPS", 30, 30, {
        "1": "3V3", "2": "USART3_TX", "3": "USART3_RX",
        "4": "I2C2_SCL", "5": "I2C2_SDA", "6": "GND"})
    place_nc("J2", "flux:HDR6", "TELEM", 30, 80, {
        "1": "5V", "2": "USART2_TX", "3": "USART2_RX", "4": "GND"})
    place_nc("J3", "flux:HDR4", "RC", 30, 130, {
        "1": "5V", "2": "UART5_RX", "3": "UART5_TX", "4": "GND"})
    place_nc("J4", "flux:USB_C_16P", "USB-C", 380, 60, {
        "A4": "5V", "A9": "5V", "B4": "5V", "B9": "5V",
        "A1": "GND", "A12": "GND", "B1": "GND", "B12": "GND",
        "A6": "USB_DP", "B6": "USB_DP", "A7": "USB_DM", "B7": "USB_DM",
        "S1": "GND"})
    place_nc("J5", "flux:HDR10", "SWD", 30, 180, {
        "1": "3V3", "2": "SWDIO", "3": "GND", "4": "SWCLK",
        "9": "NRST", "5": "GND", "6": "GND", "7": "GND", "8": "GND",
        "10": "GND"})
    pwm = {str(i): f"PWM_CH{i}" for i in range(1, 7)}
    pwm.update({"7": "GND", "8": "GND", "9": "GND", "10": "GND",
                "11": "GND", "12": "GND", "13": "5V", "14": "GND"})
    sch.place(SymInst("J6", "flux:HDR14", "PWM", x=200, y=270, pin_nets=pwm))
    place_nc("J7", "flux:HDR3", "DEBUG", 90, 260, {
        "1": "3V3", "2": "BOOT0", "3": "GND"})
    sch.place(SymInst("D1", "flux:LED", "BLUE", x=100, y=230,
                      pin_nets={"1": "3V3", "2": "LED_ACT"}))
    sch.place(SymInst("R1", "flux:R_0603", "1k", x=100, y=245,
                      pin_nets={"1": "LED_ACT", "2": "GND"}))
    sch.rails = ["5V", "GND", "VDDCORE", "VDDCORE_SW", "VDDA"]
    return sch


def write_schematic(project_dir: Path) -> Path:
    from fluxpcb.kicadgen.schematic import write as sch_write
    out = project_dir / "schematic" / "board" / "stm32n6-px4.kicad_sch"
    out.parent.mkdir(parents=True, exist_ok=True)
    sch_write(schematic(), out)
    return out
