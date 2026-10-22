# -*- coding: utf-8 -*-
"""MCU database layer — ST openMS pin XML -> PX4 FMU netlist machinery.

Generalizes the proven STM32N6 flow (ST-official XML, three-way verified)
to any ST MCU in STM32_open_pin_data: H743 TFBGA240 joins N6 TFBGA264 as
the second px4-fmu MCU instance.

Sources: STMicroelectronics/STM32_open_pin_data (official pin databases).
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path

NS = "{http://dummy.com}"
ROOT = Path(__file__).resolve().parent.parent.parent
REF = ROOT / "reference"

# PX4 FMU peripheral want-list (AF signals both MCUs share)
PX4_WANT = [
    "SPI1_SCK", "SPI1_MISO", "SPI1_MOSI", "SPI1_NSS",
    "SPI2_SCK", "SPI2_MISO", "SPI2_MOSI", "SPI2_NSS",
    "I2C1_SCL", "I2C1_SDA", "I2C2_SCL", "I2C2_SDA",
    "USART2_TX", "USART2_RX", "USART3_TX", "USART3_RX",
    "UART5_TX", "UART5_RX",
    "FDCAN1_TX", "FDCAN1_RX",
    "TIM1_CH1", "TIM1_CH2", "TIM1_CH3", "TIM1_CH4",
    "TIM3_CH1", "TIM3_CH2",
    "RCC_OSC_IN", "RCC_OSC_OUT", "RCC_OSC32_IN", "RCC_OSC32_OUT",
]

USB_SIGNALS = {"n6": ["USB1_OTG_HS_DP", "USB1_OTG_HS_DM"],
               "h743": ["USB_OTG_FS_DP", "USB_OTG_FS_DM"]}

SWD_SIGNALS = ["DEBUG_JTMS-SWDIO", "DEBUG_JTCK-SWCLK"]
NRST_NAMES = ("NRST",)
BOOT_NAMES = {"n6": ("PDR_ON",), "h743": ("BOOT0",)}


MCU_CONFIG = {
    "n6": {
        "xml": REF / "n6lib" / "STM32N657X0HxQ.xml",
        "lib_symbol": "flux:STM32N657X0",
        "value": "STM32N657X0H3Q",
        "ref_prefix": "U1",
        "usb": "USB1_OTG_HS",
        # internal SMPS: VLXSMPS switch node needs an external inductor
        "smps": True,
        "vcap": False,
    },
    "h743": {
        "xml": REF / "n6lib" / "STM32H743XIHx.xml",
        "lib_symbol": "flux:STM32H743XI",
        "value": "STM32H743XIHx",
        "ref_prefix": "U1",
        "usb": "USB_OTG_FS",
        # H7: internal 1.8V LDO needs only VCAP caps (no inductor)
        "smps": False,
        "vcap": True,
    },
}


def load_st_xml(xml_path: Path) -> dict[str, dict]:
    """ST openMS pin XML -> {ball_position: {name, type, signals[]}}."""
    root = ET.parse(xml_path).getroot()
    pins = {}
    for p in root.iter(NS + "Pin"):
        pos = p.get("Position")
        pins[pos] = {
            "name": p.get("Name"),
            "type": p.get("Type"),
            "signals": [s.get("Name") for s in p.findall(NS + "Signal")],
        }
    return pins


def allocate(want: list[str], pins: dict) -> dict[str, dict]:
    """Priority AF allocation: signals -> {ball, pin_name, type}.

    Fixed-home signals (USB/SWD/OSC — dedicated pins on ST MCUs) claim
    their balls first so a shared pin like PA11 (TIM1_CH4 vs USB_DM)
    never orphans the USB function."""
    prio = []
    for s in want:
        if any(k in s for k in ("USB", "SWD", "SWCLK", "OSC", "NRST")):
            prio.append((0, s))
        elif "FDCAN" in s or "I2C" in s:
            prio.append((1, s))
        elif s.startswith(("SPI", "USART", "UART")):
            prio.append((2, s))
        else:
            prio.append((3, s))          # TIM/other — most fungible
    prio.sort(key=lambda t: t[0])

    used_balls: set[str] = set()
    alloc: dict[str, dict] = {}
    for _, sig in prio:
        if sig in alloc:
            continue
        for pos in sorted(pins):
            if pos in used_balls:
                continue
            if sig in pins[pos]["signals"]:
                alloc[sig] = {"ball": pos, "pin": pins[pos]["name"],
                              "type": pins[pos]["type"]}
                used_balls.add(pos)
                break
    return alloc


def power_balls(pins: dict) -> dict[str, str]:
    """ball -> rail net from name prefixes. VDD*/VDDA/VBAT/VREF+->3V3
    (VREF- nets to GND), VSS*->GND, VCAP->VCAP (caps to GND)."""
    out = {}
    for pos, info in pins.items():
        n = info["name"].upper()
        if n.startswith("VSS"):
            out[pos] = "GND"
        elif n.startswith("VCAP"):
            out[pos] = "VCAP"
        elif n == "VREF-" or n == "VSSA":
            out[pos] = "GND"
        elif n.startswith(("VDD", "VBAT", "VREF")):
            out[pos] = "3V3"
    return out


def grid(pins: dict) -> tuple[list[str], int, int]:
    """(row_letters, min_col, max_col) derived from the data — no guessing."""
    rows = sorted({p[0] for p in pins})
    cols = [int(p[1:]) for p in pins]
    return rows, min(cols), max(cols)


def all_balls(pins: dict) -> list[str]:
    """Every populated ball position in grid order."""
    rows, c0, c1 = grid(pins)
    order = {r: i for i, r in enumerate(rows)}
    return sorted(pins, key=lambda p: (order[p[0]], int(p[1:])))


def build_ball_nets(variant: str, pins: dict) -> dict[str, str]:
    """ball -> net for every used ball (PX4 peripherals + USB + SWD + NRST/
    BOOT + power domains). This is the single source for board pad nets and
    the schematic pin map."""
    cfg = MCU_CONFIG[variant]
    want = list(PX4_WANT) + USB_SIGNALS[variant] + SWD_SIGNALS
    alloc = allocate(want, pins)
    nets: dict[str, str] = {}
    # peripherals -> functional nets
    for sig, a in alloc.items():
        gpio = a["pin"]
        if sig.startswith("SPI1_"):
            nets[a["ball"]] = sig
        elif sig.startswith("SPI2_"):
            nets[a["ball"]] = sig
        elif sig in ("I2C1_SCL", "I2C1_SDA", "I2C2_SCL", "I2C2_SDA",
                     "FDCAN1_TX", "FDCAN1_RX",
                     "USART2_TX", "USART2_RX", "USART3_TX", "USART3_RX",
                     "UART5_TX", "UART5_RX"):
            nets[a["ball"]] = sig
        elif sig.startswith("TIM"):
            nets[a["ball"]] = "PWM_" + sig.replace("TIM", "").replace("_CH", "")
        elif "DP" in sig or "DM" in sig:
            nets[a["ball"]] = "USB_DP" if sig.endswith("DP") else "USB_DM"
        elif sig.endswith("SWDIO"):
            nets[a["ball"]] = "SWDIO"
        elif sig.endswith("SWCLK"):
            nets[a["ball"]] = "SWCLK"
        elif sig.endswith("OSC_IN"):
            nets[a["ball"]] = "OSC_IN"
        elif sig.endswith("OSC_OUT"):
            nets[a["ball"]] = "OSC_OUT"
        elif sig.endswith("OSC32_IN"):
            nets[a["ball"]] = "OSC32_IN"
        elif sig.endswith("OSC32_OUT"):
            nets[a["ball"]] = "OSC32_OUT"
        else:
            nets[a["ball"]] = gpio
    # dedicated non-AF pins
    for pos, info in pins.items():
        n = info["name"]
        if n in NRST_NAMES:
            nets[pos] = "NRST"
        elif n in BOOT_NAMES[variant]:
            nets[pos] = "BOOT0"
    # power domains (override nothing above)
    nets.update(power_balls(pins))
    return nets


def gen_footprint(variant: str, out_path: Path) -> Path:
    """BGA footprint from the XML-derived grid (rows/cols/pitch 0.8mm)."""
    cfg = MCU_CONFIG[variant]
    pins = load_st_xml(cfg["xml"])
    rows, c0, c1 = grid(pins)
    nrows, ncols = len(rows), c1 - c0 + 1
    pitch = 0.8
    ball = 0.4
    a1 = (-((ncols - 1) / 2) * pitch, -((nrows - 1) / 2) * pitch)
    row_i = {r: i for i, r in enumerate(rows)}
    pads = []
    for pos in sorted(pins, key=lambda p: (row_i[p[0]], int(p[1:]))):
        r, c = row_i[pos[0]], int(pos[1:]) - c0
        x = a1[0] + c * pitch
        y = a1[1] + r * pitch
        pads.append(
            f'    (pad "{pos}" smd circle (at {x:.2f} {y:.2f}) '
            f'(size {ball} {ball}) (layers "F.Cu" "F.Paste" "F.Mask"))')
    cr = ncols * pitch / 2 + 0.7
    body = cfg["value"].replace("x", "x")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        f'(footprint "flux:{variant.upper()}_BGA"\n'
        f'  (version 20250114)\n  (generator fluxpcb)\n  (layer "F.Cu")\n'
        f'  (descr "{cfg["value"]} from ST official pin XML")\n  (attr smd)\n'
        f'  (property "Reference" "U**" (at 0 -{cr + 1.2:.1f} 0) (layer "F.SilkS")\n'
        f'    (effects (font (size 1 1) (thickness 0.15))))\n'
        f'  (property "Value" "{cfg["value"]}" (at 0 {cr + 1.2:.1f} 0) (layer "F.Fab")\n'
        f'    (effects (font (size 1 1) (thickness 0.15))))\n'
        f'  (fp_line (start {-cr} {-cr}) (end {cr} {-cr}) (stroke (width 0.05) (type solid)) (layer "F.CrtYd"))\n'
        f'  (fp_line (start {cr} {-cr}) (end {cr} {cr}) (stroke (width 0.05) (type solid)) (layer "F.CrtYd"))\n'
        f'  (fp_line (start {cr} {cr}) (end {-cr} {cr}) (stroke (width 0.05) (type solid)) (layer "F.CrtYd"))\n'
        f'  (fp_line (start {-cr} {cr}) (end {-cr} {-cr}) (stroke (width 0.05) (type solid)) (layer "F.CrtYd"))\n'
        + "\n".join(pads) + "\n)\n", encoding="utf-8")
    return out_path
