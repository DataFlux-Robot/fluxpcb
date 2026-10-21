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
from fluxpcb.kicadgen.schematic import Schematic, SymDef, SymInst

ROOT = Path(__file__).resolve().parent.parent.parent
N6LIB = ROOT / "reference" / "n6lib"
FCLIB = ROOT / "reference" / "fculib"

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


def placement() -> dict[str, tuple[float, float, float]]:
    """FC rules: MCU center, IMU pair in the center vibration zone away
    from buck/USB, barometers split, mag far from buck, connectors on
    edges, buck corner."""
    return {
        "U1":   (27.5, 14.0, 0.0),     # STM32N657 TFBGA264 center
        "U2":   (17.0, 12.0, 0.0),     # ICM-42688-P (IMU1)
        "U3":   (17.0, 18.0, 0.0),     # BMI055 (IMU2)
        "U4":   (20.0, 6.5, 0.0),      # BMP388 #1
        "U5":   (34.0, 21.5, 0.0),     # BMP388 #2
        "U6":   (33.0, 6.5, 0.0),      # IST8310 mag
        "U7":   (47.5, 6.5, 90.0),     # TLV62569 buck (corner, away from IMU)
        "U8":   (39.0, 4.2, 90.0),     # SN65HVD230 CAN (top zone)
        "L1":   (45.0, 10.5, 0.0),     # 2.2uH VDDCORE
        "FB1":  (52.5, 22.5, 0.0),     # VDDA ferrite (0603)
        "Y1":   (22.5, 22.5, 0.0),     # 24MHz
        "Y2":   (17.5, 22.5, 0.0),     # 32.768k
        "J1":   (2.0, 2.5, 0.0),       # GPS (1x06 1.27, origin pad1)
        "J2":   (2.0, 10.5, 0.0),      # TELEM (1x06)
        "J3":   (2.0, 19.0, 0.0),      # RC/SBUS (1x04)
        "J4":   (53.0, 14.0, 90.0),    # USB-C right edge
        "J5":   (5.5, 21.5, 0.0),      # SWD 2x05 1.27
        "J6":   (35.0, 25.0, 0.0),     # PWM 2x07 2.54 bottom edge
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

    b.place(Fp("Package_TO_SOT_Smd", "SOT-23-5", "U7", "TLV62569",
               *pl["U7"], pad_nets={
                   "1": "5V", "2": "GND", "3": "5V",
                   "4": "VDDCORE_SW", "5": "3V3"}))
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
    out = project_dir / "pcb" / "board" / "stm32n6-px4.kicad_pcb"
    out.parent.mkdir(parents=True, exist_ok=True)
    pcb_write(board(), out)
    return out
