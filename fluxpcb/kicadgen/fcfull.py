# -*- coding: utf-8 -*-
"""Pixhawk-class full flight controller (h743-fc) generator.

50x50mm 6-layer H743 TFBGA240 board covering the full peripheral set:
  - 8 PWM outputs (TIM1 x4 + TIM4 x4) on a Pixhawk-style 3-row 2.54 header
  - 8 UART connectors (USART1/2/3/6 + UART4/5/7/8)
  - microSD card (SDMMC1) + dual CAN (FDCAN1/2 + SN65HVD230 x2)
  - 2 ADC inputs, dedicated GND header, USB-C, SWD, BOOT debug header
  - dual-trait IMUs: BMI088 (SPI1, LGA-16 pin-compat BMI055) +
    ICM-45686 (SPI2, LGA-14 pin-compat ICM-42688-P)
  - BMP388 x2 (I2C1, address split) + IST8310 (I2C2)

MCU pin allocation from ST-official STM32H743XIHx.xml via mcu_db (FC_WANT,
56 signals, depth-2 eviction allocator). Sensor pin tables live in
reference/fculib/sensor_pinmaps.json (BMI088 from the ArduPilot NxtPX4
symbol, ICM-45686 = ICM-42688-P family pinout) — verify against datasheets
before ordering.
"""
from __future__ import annotations

import json
from pathlib import Path

from fluxpcb.kicadgen.pcb import Board, Fp, parse
from fluxpcb.kicadgen.parts_lib import P
from fluxpcb.kicadgen.schematic import Pin, Schematic, SymDef, SymInst

ROOT = Path(__file__).resolve().parent.parent.parent
FCLIB = ROOT / "reference" / "fculib"
KICAD_FPS = Path(r"C:/Users/zhang/AppData/Local/Programs/KiCad/10.0/share/kicad/footprints")

W, H = 50.0, 50.0
SLUG = "h743-fc"

# TIM3_CH1 re-purposed as the BMI088 gyro CSB2 gpio (FC_PWM_MAP covers only
# TIM1/TIM4); every other signal comes straight from FC_WANT
FC_LOCAL_PWM = {"TIM3_CH1": "IMU1_CS2"}


def _k(lib: str, name: str) -> Path:
    return KICAD_FPS / f"{lib}.pretty" / f"{name}.kicad_mod"


def gen_hdr3x08(out_path: Path) -> Path:
    """Pixhawk-style 3x08 2.54mm through-hole header (24 pins: PWM/GND/5V
    rows) — KiCad ships no 3xNN, so it is generated like the BGA."""
    if out_path.exists():
        return out_path
    pitch, drill, pad = 2.54, 1.0, 1.7
    pads = []
    n = 1
    for r in (-1, 0, 1):                     # row 1 = PWM, 2 = GND, 3 = 5V
        for c in range(8):
            x = (c - 3.5) * pitch
            y = r * pitch
            pads.append(
                f'    (pad "{n}" thru_hole circle (at {x:.2f} {y:.2f}) '
                f'(size {pad} {pad}) (drill {drill}) '
                f'(layers "*.Cu" "*.Mask"))')
            n += 1
    cx, cy = 4 * pitch + 1.0, 1.5 * pitch + 1.0
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        f'(footprint "flux:HDR_3x08_P2.54"\n'
        f'  (version 20250114)\n  (generator fluxpcb)\n  (layer "F.Cu")\n'
        f'  (descr "3x08 P2.54 header, PWM rail (generated)")\n'
        f'  (attr through_hole)\n'
        f'  (property "Reference" "J**" (at 0 -{cy:.2f} 0) (layer "F.SilkS")\n'
        f'    (effects (font (size 1 1) (thickness 0.15))))\n'
        f'  (property "Value" "HDR_3x08" (at 0 {cy:.2f} 0) (layer "F.Fab")\n'
        f'    (effects (font (size 1 1) (thickness 0.15))))\n'
        f'  (fp_line (start {-cx} {-cy}) (end {cx} {-cy}) (stroke (width 0.05) (type solid)) (layer "F.CrtYd"))\n'
        f'  (fp_line (start {cx} {-cy}) (end {cx} {cy}) (stroke (width 0.05) (type solid)) (layer "F.CrtYd"))\n'
        f'  (fp_line (start {cx} {cy}) (end {-cx} {cy}) (stroke (width 0.05) (type solid)) (layer "F.CrtYd"))\n'
        f'  (fp_line (start {-cx} {cy}) (end {-cx} {-cy}) (stroke (width 0.05) (type solid)) (layer "F.CrtYd"))\n'
        + "\n".join(pads) + "\n)\n", encoding="utf-8")
    return out_path


def mcu_ball_nets() -> dict[str, str]:
    """ball -> net for every used MCU ball (FC_WANT + TIM3_CS2 + power)."""
    from fluxpcb.kicadgen import mcu_db
    pins = mcu_db.load_st_xml(mcu_db.MCU_CONFIG["h743"]["xml"])
    pwm = dict(mcu_db.FC_PWM_MAP)
    pwm.update(FC_LOCAL_PWM)
    return mcu_db.build_ball_nets("h743", pins,
                                  want=mcu_db.FC_WANT + list(FC_LOCAL_PWM),
                                  pwm_map=pwm)


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
    mcu_fp = ROOT / "reference" / "h743_bga.kicad_mod"
    if not mcu_fp.exists():
        from fluxpcb.kicadgen import mcu_db
        mcu_db.gen_footprint("h743", mcu_fp)
    return {
        "U1": mcu_fp,
        # BMI088 shares the BMI055 LGA-16 family footprint
        "U2": FCLIB / "BMI088.kicad_mod",
        # ICM-45686 shares the ICM-42688-P 14-LGA footprint
        "U3": FCLIB / "ICM-42688-P.kicad_mod",
        "U4": FCLIB / "BMP388.kicad_mod",
        "U5": FCLIB / "BMP388.kicad_mod",
        "U6": FCLIB / "IST8310.kicad_mod",
        "U7": _k("Package_TO_SOT_Smd", "SOT-23-5"),
        "U8": _k("Package_SO", "SOIC-8_3.9x4.9mm_P1.27mm"),
        "U9": _k("Package_SO", "SOIC-8_3.9x4.9mm_P1.27mm"),
        "CV1": _k("Capacitor_SMD", "C_0805_2012Metric"),
        "CV2": _k("Capacitor_SMD", "C_0805_2012Metric"),
        "FB1": _k("Resistor_SMD", "R_0603_1608Metric"),
        "Y1": _k("Crystal", "Crystal_SMD_5032-2Pin_5.0x3.2mm"),
        "Y2": _k("Crystal", "Crystal_SMD_3215-2Pin_3.2x1.5mm"),
        "J1": _k(hdr127, "PinHeader_1x06_P1.27mm_Vertical"),
        "J2": _k(hdr127, "PinHeader_1x06_P1.27mm_Vertical"),
        "J3": _k(hdr127, "PinHeader_1x06_P1.27mm_Vertical"),
        "J4": _k(hdr127, "PinHeader_1x04_P1.27mm_Vertical"),
        "J5": _k(hdr127, "PinHeader_1x04_P1.27mm_Vertical"),
        "J6": _k(hdr127, "PinHeader_1x04_P1.27mm_Vertical"),
        "J7": _k(hdr127, "PinHeader_1x04_P1.27mm_Vertical"),
        "J8": _k(hdr127, "PinHeader_1x04_P1.27mm_Vertical"),
        "J9": _k(hdr127, "PinHeader_2x05_P1.27mm_Vertical"),
        "J10": _k("Connector_USB", "USB_C_Receptacle_HRO_TYPE-C-31-M-12"),
        "J11": gen_hdr3x08(ROOT / "reference" / "HDR_3x08_P2.54.kicad_mod"),
        "J12": _k(hdr127, "PinHeader_1x04_P1.27mm_Vertical"),
        "J13": _k(hdr127, "PinHeader_1x04_P1.27mm_Vertical"),
        "J14": _k(hdr127, "PinHeader_1x04_P1.27mm_Vertical"),
        "J15": _k(hdr127, "PinHeader_1x05_P1.27mm_Vertical"),
        "J16": _k(hdr254, "PinHeader_1x03_P2.54mm_Vertical"),
        "J17": _k("Connector_Card", "microSD_HC_Molex_47219-2001"),
        "D1": _k("LED_SMD", "LED_0603_1608Metric"),
        "R1": _k("Resistor_SMD", "R_0603_1608Metric"),
    }


def validate_placement(place=None) -> list:
    """Analytic pre-flight: overlap/edge/connector checks in milliseconds."""
    from fluxpcb.kicadgen.hierplace import validate
    return validate(place or placement(), footprints(), W, H,
                    connector_refs=("J10", "J17"))


def placement() -> dict[str, tuple[float, float, float]]:
    """50x50 square: MCU + sensor cluster center-left, CAN pair right band,
    power top-right, TF card top-left, UART stack on the left edge, PWM rail
    along the bottom, USB/CAN/ADC/GND on the right edge."""
    return {
        "U1":   (26.0, 24.0, 0.0),    # STM32H743 TFBGA240 center
        "U2":   (16.5, 18.5, 0.0),    # BMI088 (SPI1) — LGA-16
        "U3":   (16.5, 28.5, 0.0),    # ICM-45686 (SPI2) — LGA-14
        "U4":   (18.5, 12.0, 0.0),    # BMP388 #1
        "U5":   (13.5, 34.5, 0.0),    # BMP388 #2
        "U6":   (10.0, 37.5, 0.0),    # IST8310 mag (away from USB/CAN)
        "U7":   (36.5, 5.0, 0.0),     # AP2112K LDO (top-right)
        "U8":   (37.5, 22.0, 0.0),    # SN65HVD230 CAN1
        "U9":   (37.5, 28.0, 0.0),    # SN65HVD230 CAN2
        "CV1":  (33.0, 33.0, 0.0),    # VCAP 2.2uF
        "CV2":  (36.5, 33.0, 0.0),    # VCAP 2.2uF
        "FB1":  (30.5, 38.0, 0.0),    # VDDA ferrite
        "Y1":   (35.0, 9.5, 0.0),     # 24MHz
        "Y2":   (35.0, 14.5, 0.0),    # 32.768k
        "D1":   (28.0, 3.5, 0.0),     # status LED
        "R1":   (30.8, 3.5, 0.0),     # LED resistor
        # left edge: GPS/TELEM1/TELEM2 (1x06 1.27) + GND post (1x05 1.27)
        "J1":   (2.5, 20.2, 0.0),
        "J2":   (2.5, 28.0, 0.0),
        "J3":   (2.5, 35.6, 0.0),
        "J15":  (2.5, 43.4, 0.0),     # GND x5
        # bottom edge: PWM rail (3x08 2.54) + UART4/7/8/RC/USART6 headers
        "J11":  (15.0, 44.5, 0.0),    # PWM 3x08 (courtyard 22.4x7.6)
        "J5":   (28.5, 45.0, 0.0),
        "J6":   (32.0, 45.0, 0.0),
        "J7":   (35.5, 45.0, 0.0),
        "J8":   (39.0, 45.0, 0.0),
        "J4":   (43.0, 44.8, 0.0),
        # right edge, top to bottom: USB-C/CAN1/CAN2/ADC/SWD (1.27 pitch)
        "J10":  (46.4, 16.0, 90.0),   # USB-C
        "J12":  (44.2, 25.5, 90.0),   # CAN1
        "J13":  (44.2, 31.0, 90.0),   # CAN2
        "J14":  (44.2, 37.0, 90.0),   # ADC
        "J9":   (47.0, 44.0, 0.0),    # SWD 2x05 1.27 rot0 (3.4x6.9)
        # top edge: TF card (13.75x17mm) + debug header
        "J16":  (21.0, 3.5, 0.0),     # DEBUG/BOOT 1x03
        "J17":  (8.5, 9.5, 0.0),      # microSD (pads x 1.6..15.4, y 1..18)
    }


_REFINED: dict | None = None


def placement_refined() -> dict:
    """SA-refined placement (elcruzo/tbrandenburg knowhow), accepted only
    when the analytic validation doesn't get worse. Memoized."""
    global _REFINED
    if _REFINED is not None:
        return _REFINED
    from fluxpcb.kicadgen.hierplace import validate
    from fluxpcb.placement import refine
    base = placement()
    fps = footprints()
    try:
        new, c0, c1 = refine(base, fps, W, H, iterations=800)
        if c1 < c0 and len(validate(new, fps, W, H)) <= len(validate(base, fps, W, H)):
            _REFINED = new
            return new
    except Exception:
        pass
    _REFINED = base
    return base


def _stitch_anchors(pl):
    """Power-plane stitching anchors — safe open spots."""
    return [
        (pl["U7"][0] + 1.27, pl["U7"][1] - 1.27, "3V3"),   # LDO out
        (pl["U1"][0] + 8.0, pl["U1"][1] + 8.0, "3V3"),     # BGA 3V3 side
        (pl["J10"][0] - 1.27, pl["J10"][1] - 3.0, "5V"),   # USB 5V
        (pl["U7"][0] - 1.27, pl["U7"][1] + 1.27, "5V"),    # LDO in
    ]


NETS = [
    "5V", "3V3", "GND", "VDDA", "VCAP", "NRST", "BOOT0", "LED_ACT",
    "SWDIO", "SWCLK",
    "OSC_IN", "OSC_OUT", "OSC32_IN", "OSC32_OUT",
    "USB_DP", "USB_DM",
    "SPI1_SCK", "SPI1_MISO", "SPI1_MOSI", "SPI1_NSS", "IMU1_INT", "IMU1_CS2",
    "SPI2_SCK", "SPI2_MISO", "SPI2_MOSI", "SPI2_NSS", "IMU2_INT1",
    "I2C1_SCL", "I2C1_SDA", "BARO1_INT", "BARO2_INT",
    "I2C2_SCL", "I2C2_SDA", "MAG_INT",
    "FDCAN1_TX", "FDCAN1_RX", "CAN1_H", "CAN1_L",
    "FDCAN2_TX", "FDCAN2_RX", "CAN2_H", "CAN2_L",
    "USART1_TX", "USART1_RX", "USART2_TX", "USART2_RX",
    "USART3_TX", "USART3_RX", "USART6_TX", "USART6_RX",
    "UART4_TX", "UART4_RX", "UART5_TX", "UART5_RX",
    "UART7_TX", "UART7_RX", "UART8_TX", "UART8_RX",
    "SDMMC1_CK", "SDMMC1_CMD", "SDMMC1_D0", "SDMMC1_D1",
    "SDMMC1_D2", "SDMMC1_D3",
    "PWM_CH1", "PWM_CH2", "PWM_CH3", "PWM_CH4",
    "PWM_CH5", "PWM_CH6", "PWM_CH7", "PWM_CH8",
    "ADC1_INP10", "ADC1_INP11",
]


def board() -> Board:
    from fluxpcb.kicadgen import mcu_db as _mdb
    ball_nets = mcu_ball_nets()
    b = Board(SLUG, W, H, sorted(set(NETS)), n_layers=6)
    pl = placement_refined()

    fp_bga = ROOT / "reference" / "h743_bga.kicad_mod"
    if not fp_bga.exists():
        _mdb.gen_footprint("h743", fp_bga)
    b.place(LocalFp(fp_bga, "U1", _mdb.MCU_CONFIG["h743"]["value"], *pl["U1"],
                    pad_nets=ball_nets))

    # dual-trait IMUs: BMI088 accel+gyro (separate CSB1/CBB2) + ICM-45686
    # BMI088 PS->GND = SPI mode; INT1 (pin16) = accel/gyro data interrupt
    b.place(LocalFp(FCLIB / "BMI088.kicad_mod", "U2", "BMI088", *pl["U2"],
                    pad_nets={
                        "3": "3V3", "11": "3V3", "4": "GND", "6": "GND",
                        "7": "GND", "8": "SPI1_SCK", "9": "SPI1_MOSI",
                        "15": "SPI1_MISO", "14": "SPI1_NSS",
                        "5": "IMU1_CS2", "16": "IMU1_INT"}))
    b.place(LocalFp(FCLIB / "ICM-42688-P.kicad_mod", "U3", "ICM-45686",
                    *pl["U3"], pad_nets={
                        "5": "3V3", "8": "3V3", "6": "GND",
                        "13": "SPI2_SCK", "14": "SPI2_MOSI",
                        "1": "SPI2_MISO", "12": "SPI2_NSS",
                        "4": "IMU2_INT1"}))

    for ref, sdo in (("U4", "GND"), ("U5", "3V3")):
        b.place(LocalFp(FCLIB / "BMP388.kicad_mod", ref, "BMP388",
                        *pl[ref], pad_nets={
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

    # power: 5V -> AP2112K -> 3V3; H7 VCAP pair; VDDA ferrite
    b.place(Fp("Package_TO_SOT_Smd", "SOT-23-5", "U7", "AP2112K-3.3",
               *pl["U7"], pad_nets={"1": "5V", "2": "GND", "3": "5V",
                                    "5": "3V3"}))
    for ref in ("U8", "U9"):
        can = "1" if ref == "U8" else "2"
        b.place(Fp("Package_SO", "SOIC-8_3.9x4.9mm_P1.27mm", ref,
                   "SN65HVD230", *pl[ref], pad_nets={
                       "1": f"FDCAN{can}_TX", "4": f"FDCAN{can}_RX",
                       "2": "GND", "3": "3V3", "8": "3V3",
                       "7": f"CAN{can}_H", "6": f"CAN{can}_L"}))
    for ref in ("CV1", "CV2"):
        b.place(Fp("Capacitor_SMD", "C_0805_2012Metric", ref, "2.2uF",
                   *pl[ref], pad_nets={"1": "VCAP", "2": "GND"}))
    b.place(Fp("Resistor_SMD", "R_0603_1608Metric", "FB1", "600R@100M",
               *pl["FB1"], pad_nets={"1": "3V3", "2": "VDDA"}))
    b.place(Fp("Crystal", "Crystal_SMD_5032-2Pin_5.0x3.2mm", "Y1", "24MHz",
               *pl["Y1"], pad_nets={"1": "OSC_IN", "2": "GND"}))
    b.place(Fp("Crystal", "Crystal_SMD_3215-2Pin_3.2x1.5mm", "Y2", "32.768k",
               *pl["Y2"], pad_nets={"1": "OSC32_IN", "2": "OSC32_OUT"}))

    hdr127 = "Connector_PinHeader_1.27mm"
    hdr254 = "Connector_PinHeader_2.54mm"
    # 8 UART connectors
    b.place(Fp(hdr127, "PinHeader_1x06_P1.27mm_Vertical", "J1", "GPS",
               *pl["J1"], pad_nets={"1": "5V", "2": "USART1_TX",
                                    "3": "USART1_RX", "4": "I2C2_SCL",
                                    "5": "I2C2_SDA", "6": "GND"}))
    b.place(Fp(hdr127, "PinHeader_1x06_P1.27mm_Vertical", "J2", "TELEM1",
               *pl["J2"], pad_nets={"1": "5V", "2": "USART2_TX",
                                    "3": "USART2_RX", "4": "GND",
                                    "5": "GND", "6": "GND"}))
    b.place(Fp(hdr127, "PinHeader_1x06_P1.27mm_Vertical", "J3", "TELEM2",
               *pl["J3"], pad_nets={"1": "5V", "2": "USART3_TX",
                                    "3": "USART3_RX", "4": "GND",
                                    "5": "GND", "6": "GND"}))
    for ref, tx, rx, rail in (("J4", "USART6_TX", "USART6_RX", "3V3"),
                              ("J5", "UART4_TX", "UART4_RX", "3V3"),
                              ("J6", "UART7_TX", "UART7_RX", "3V3"),
                              ("J7", "UART8_TX", "UART8_RX", "3V3"),
                              ("J8", "UART5_TX", "UART5_RX", "5V")):
        b.place(Fp(hdr127, "PinHeader_1x04_P1.27mm_Vertical", ref,
                   ref.upper().replace("J", "UART_"), *pl[ref],
                   pad_nets={"1": rail, "2": tx, "3": rx, "4": "GND"}))
    b.place(Fp(hdr127, "PinHeader_2x05_P1.27mm_Vertical", "J9", "SWD",
               *pl["J9"], pad_nets={"1": "3V3", "2": "SWDIO", "3": "GND",
                                    "4": "SWCLK", "5": "GND", "6": "GND",
                                    "7": "GND", "8": "GND", "9": "NRST",
                                    "10": "GND"}))
    b.place(Fp("Connector_USB", "USB_C_Receptacle_HRO_TYPE-C-31-M-12",
               "J10", "USB-C", *pl["J10"],
               pad_nets={"A4": "5V", "A9": "5V", "B4": "5V", "B9": "5V",
                         "A1": "GND", "A12": "GND", "B1": "GND",
                         "B12": "GND", "A6": "USB_DP", "B6": "USB_DP",
                         "A7": "USB_DM", "B7": "USB_DM", "S1": "GND"}))
    pwm = {str(i): f"PWM_CH{i}" for i in range(1, 9)}
    pwm.update({str(i): "GND" for i in range(9, 17)})
    pwm.update({str(i): "5V" for i in range(17, 25)})
    b.place(LocalFp(gen_hdr3x08(ROOT / "reference" / "HDR_3x08_P2.54.kicad_mod"),
                    "J11", "PWM", *pl["J11"], pad_nets=pwm))
    for ref, h, l in (("J12", "CAN1_H", "CAN1_L"), ("J13", "CAN2_H", "CAN2_L")):
        b.place(Fp(hdr127, "PinHeader_1x04_P1.27mm_Vertical", ref,
                   ref.replace("J", "CAN"), *pl[ref],
                   pad_nets={"1": "5V", "2": h, "3": l, "4": "GND"}))
    b.place(Fp(hdr127, "PinHeader_1x04_P1.27mm_Vertical", "J14", "ADC",
               *pl["J14"], pad_nets={"1": "3V3", "2": "ADC1_INP10",
                                     "3": "ADC1_INP11", "4": "GND"}))
    b.place(Fp(hdr127, "PinHeader_1x05_P1.27mm_Vertical", "J15", "GND",
               *pl["J15"], pad_nets={str(i): "GND" for i in range(1, 6)}))
    b.place(Fp(hdr254, "PinHeader_1x03_P2.54mm_Vertical", "J16", "DEBUG",
               *pl["J16"], pad_nets={"1": "3V3", "2": "BOOT0", "3": "GND"}))
    # microSD (Molex 47219-2001): 1=DAT2 2=DAT3 3=CMD 4=VDD 5=CLK 6=VSS
    # 7=DAT0 8=DAT1, SH shell pads
    b.place(LocalFp(_k("Connector_Card", "microSD_HC_Molex_47219-2001"),
                    "J17", "microSD", *pl["J17"],
                    pad_nets={"1": "SDMMC1_D2", "2": "SDMMC1_D3",
                              "3": "SDMMC1_CMD", "4": "3V3",
                              "5": "SDMMC1_CK", "6": "GND",
                              "7": "SDMMC1_D0", "8": "SDMMC1_D1",
                              "SH": "GND"}))
    b.place(Fp("LED_SMD", "LED_0603_1608Metric", "D1", "BLUE", *pl["D1"],
               pad_nets={"1": "3V3", "2": "LED_ACT"}))
    b.place(Fp("Resistor_SMD", "R_0603_1608Metric", "R1", "1k", *pl["R1"],
               pad_nets={"1": "LED_ACT", "2": "GND"}))
    # 6L real inner planes + stitching vias (In1/In4 GND, In2 3V3, In3 5V)
    b.ground_zones()
    b._extra_vias = b.stitch_vias(_stitch_anchors(pl))
    return b


def write_board(project_dir: Path) -> Path:
    from fluxpcb.kicadgen.pcb import write as pcb_write
    from fluxpcb.kicadgen.hierplace import validate
    issues = validate(placement_refined(), footprints(), W, H)
    if issues:
        raise ValueError("layout pre-flight FAILED (analytic, board NOT written): "
                         + "; ".join(issues[:4]))
    out = project_dir / "pcb" / "board" / (SLUG + ".kicad_pcb")
    out.parent.mkdir(parents=True, exist_ok=True)
    pcb_write(board(), out)
    return out


# ================================================================ schematic
def mcu_symdef():
    """H743 symbol from the ST-official pin DB (265 balls, 4-column grid)."""
    from fluxpcb.kicadgen import mcu_db as _mdb
    pins_db = _mdb.load_st_xml(_mdb.MCU_CONFIG["h743"]["xml"])
    _rows, _c0, _c1 = _mdb.grid(pins_db)
    order = [f"{r}{c}" for r in _rows for c in range(_c0, _c1 + 1)]
    pins = []
    per_col = (len(pins_db) + 3) // 4
    xs = (-20.32, -10.16, 10.16, 20.32)
    kept = [b for b in order if b in pins_db]
    for i, ball in enumerate(kept):
        info = pins_db[ball]
        name, typ = info["name"], info["type"]
        nu = name.upper()
        if typ == "Power" or nu.startswith(("VDD", "VSS", "VBAT", "VCAP")):
            etype = "power_in"
        else:
            etype = "passive"
        col = min(3, i // per_col)
        y = (per_col - 1 - (i % per_col)) * 2.54
        x = xs[col]
        ang = 0 if col < 2 else 180
        pins.append(Pin(ball, name, etype, x=x, y=y, angle=ang))
    return SymDef(_mdb.MCU_CONFIG["h743"]["lib_symbol"], pins,
                  ref_prefix="U", auto_layout=False)


def _sensor_symdef(name: str, pinmap: dict, lib: str) -> SymDef:
    pins = []
    for num, pname in pinmap.items():
        pu = str(pname).upper()
        if "VDD" in pu or pu in ("AVDD", "DVDD") or "VSS" in pu or "GND" in pu:
            etype = "power_in"
        else:
            etype = "passive"
        pins.append(Pin(str(num), str(pname), etype))
    return SymDef(f"flux:{lib}", pins, ref_prefix="U")


def schematic() -> Schematic:
    ball_nets = mcu_ball_nets()
    sp = json.loads((FCLIB / "sensor_pinmaps.json").read_text(encoding="utf-8"))

    sch = Schematic(title=SLUG, project=SLUG)
    sch.add_def(mcu_symdef())
    sch.add_def(_sensor_symdef("BMI088", sp["BMI088"], "BMI088"))
    sch.add_def(_sensor_symdef("ICM-45686", sp["ICM-45686"], "ICM-45686"))
    sch.add_def(_sensor_symdef("BMP388", sp["BMP388"], "BMP388"))
    sch.add_def(_sensor_symdef("IST8310", sp["IST8310"], "IST8310"))
    sch.add_def(SymDef("flux:AP2112K", [P(1, "VIN", "power_in"),
                                        P(2, "GND", "power_in"), P(3, "EN"),
                                        P(5, "VOUT", "power_out")], ref_prefix="U"))
    sch.add_def(SymDef("flux:SOIC8_CAN", [P(i, f"P{i}") for i in range(1, 9)],
                       ref_prefix="U"))
    sch.add_def(SymDef("flux:C_0603", [P(1, "A"), P(2, "B")], ref_prefix="C"))
    sch.add_def(SymDef("flux:R_0603", [P(1, "A"), P(2, "B")], ref_prefix="R"))
    sch.add_def(SymDef("flux:LED", [P(1, "A"), P(2, "K")], ref_prefix="D"))
    sch.add_def(SymDef("flux:XTAL", [P(1, "A"), P(2, "B")], ref_prefix="Y"))
    for n, pins in (("HDR6", 6), ("HDR4", 4), ("HDR10", 10), ("HDR3", 3),
                    ("HDR5", 5), ("HDR24", 24)):
        sch.add_def(SymDef(f"flux:{n}", [P(i, f"P{i}") for i in range(1, pins + 1)],
                           ref_prefix="J"))
    sch.add_def(SymDef("flux:USB_C_16P", [
        P("A4", "VBUS"), P("A9", "VBUS"), P("B4", "VBUS"), P("B9", "VBUS"),
        P("A1", "GND"), P("A12", "GND"), P("B1", "GND"), P("B12", "GND"),
        P("A6", "D+"), P("A7", "D-"), P("B6", "D+"), P("B7", "D-"),
        P("S1", "SH")], ref_prefix="J"))
    sch.add_def(SymDef("flux:MICROSD", [
        P(1, "DAT2"), P(2, "DAT3"), P(3, "CMD"), P(4, "VDD"), P(5, "CLK"),
        P(6, "VSS"), P(7, "DAT0"), P(8, "DAT1"), P("SH", "SHIELD")],
        ref_prefix="J"))

    # NC = balls with no net
    from fluxpcb.kicadgen import mcu_db as _m
    pins_db = _m.load_st_xml(_m.MCU_CONFIG["h743"]["xml"])
    nc = {b for b in pins_db if b not in ball_nets}

    def place_nc(ref, lib, value, x, y, pin_nets):
        sd = sch.symdefs[lib]
        unused = {pp.number for pp in sd.pins if pp.number not in pin_nets}
        sch.place(SymInst(ref, lib, value, x=x, y=y,
                          pin_nets=pin_nets, nc_pins=unused))

    sch.place(SymInst("U1", _m.MCU_CONFIG["h743"]["lib_symbol"],
                      _m.MCU_CONFIG["h743"]["value"],
                      x=250, y=150, pin_nets=ball_nets, nc_pins=nc))
    # BMI088: PS->GND (SPI), CSB1 accel-CS, CSB2 gyro-CS, INT1 interrupt
    place_nc("U2", "flux:BMI088", "BMI088", 150, 40, {
        "3": "3V3", "11": "3V3", "4": "GND", "6": "GND", "7": "GND",
        "8": "SPI1_SCK", "9": "SPI1_MOSI", "15": "SPI1_MISO",
        "14": "SPI1_NSS", "5": "IMU1_CS2", "16": "IMU1_INT"})
    place_nc("U3", "flux:ICM-45686", "ICM-45686", 150, 260, {
        "5": "3V3", "8": "3V3", "6": "GND", "13": "SPI2_SCK",
        "14": "SPI2_MOSI", "1": "SPI2_MISO", "12": "SPI2_NSS",
        "4": "IMU2_INT1"})
    for ref, y, sdo, intn in (("U4", 40, "GND", "BARO1_INT"),
                              ("U5", 260, "3V3", "BARO2_INT")):
        sch.place(SymInst(ref, "flux:BMP388", "BMP388", x=340, y=y, pin_nets={
            "1": "3V3", "10": "3V3", "3": "GND", "8": "GND", "9": "GND",
            "2": "I2C1_SCL", "4": "I2C1_SDA", "5": sdo, "6": "3V3",
            "7": intn}))
    place_nc("U6", "flux:IST8310", "IST8310", 340, 150, {
        "1": "I2C2_SCL", "16": "I2C2_SDA", "2": "VDDA", "13": "VDDA",
        "9": "GND", "11": "GND", "14": "VDDA", "15": "MAG_INT"})
    place_nc("U7", "flux:AP2112K", "AP2112K-3.3", 60, 60, {
        "1": "5V", "2": "GND", "3": "5V", "5": "3V3"})
    for i, (ref, can) in enumerate((("U8", "1"), ("U9", "2"))):
        place_nc(ref, "flux:SOIC8_CAN", "SN65HVD230", 60, 110 + i * 30, {
            "1": f"FDCAN{can}_TX", "4": f"FDCAN{can}_RX", "2": "GND",
            "3": "3V3", "7": f"CAN{can}_H", "6": f"CAN{can}_L", "8": "3V3"})
    for i, ref in enumerate(("CV1", "CV2")):
        sch.place(SymInst(ref, "flux:C_0603", "2.2uF", x=60, y=180 + i * 10,
                          pin_nets={"1": "VCAP", "2": "GND"}))
    sch.place(SymInst("FB1", "flux:R_0603", "600R@100M", x=60, y=210,
                      pin_nets={"1": "3V3", "2": "VDDA"}))
    sch.place(SymInst("Y1", "flux:XTAL", "24MHz", x=180, y=300,
                      pin_nets={"1": "OSC_IN", "2": "GND"}))
    sch.place(SymInst("Y2", "flux:XTAL", "32.768k", x=180, y=310,
                      pin_nets={"1": "OSC32_IN", "2": "OSC32_OUT"}))
    # 8 UART connectors
    place_nc("J1", "flux:HDR6", "GPS", 30, 30, {
        "1": "5V", "2": "USART1_TX", "3": "USART1_RX",
        "4": "I2C2_SCL", "5": "I2C2_SDA", "6": "GND"})
    place_nc("J2", "flux:HDR6", "TELEM1", 30, 80, {
        "1": "5V", "2": "USART2_TX", "3": "USART2_RX",
        "4": "GND", "5": "GND", "6": "GND"})
    place_nc("J3", "flux:HDR6", "TELEM2", 30, 130, {
        "1": "5V", "2": "USART3_TX", "3": "USART3_RX",
        "4": "GND", "5": "GND", "6": "GND"})
    for i, (ref, tx, rx, rail) in enumerate((
            ("J4", "USART6_TX", "USART6_RX", "3V3"),
            ("J5", "UART4_TX", "UART4_RX", "3V3"),
            ("J6", "UART7_TX", "UART7_RX", "3V3"),
            ("J7", "UART8_TX", "UART8_RX", "3V3"),
            ("J8", "UART5_TX", "UART5_RX", "5V"))):
        place_nc(ref, "flux:HDR4", ref.replace("J", "UART_"), 30, 190 + i * 25, {
            "1": rail, "2": tx, "3": rx, "4": "GND"})
    place_nc("J9", "flux:HDR10", "SWD", 30, 330, {
        "1": "3V3", "2": "SWDIO", "3": "GND", "4": "SWCLK", "9": "NRST",
        "5": "GND", "6": "GND", "7": "GND", "8": "GND", "10": "GND"})
    place_nc("J10", "flux:USB_C_16P", "USB-C", 430, 60, {
        "A4": "5V", "A9": "5V", "B4": "5V", "B9": "5V",
        "A1": "GND", "A12": "GND", "B1": "GND", "B12": "GND",
        "A6": "USB_DP", "B6": "USB_DP", "A7": "USB_DM", "B7": "USB_DM",
        "S1": "GND"})
    pwm = {str(i): f"PWM_CH{i}" for i in range(1, 9)}
    pwm.update({str(i): "GND" for i in range(9, 17)})
    pwm.update({str(i): "5V" for i in range(17, 25)})
    sch.place(SymInst("J11", "flux:HDR24", "PWM", x=250, y=330, pin_nets=pwm))
    for i, (ref, h, l) in enumerate((("J12", "CAN1_H", "CAN1_L"),
                                     ("J13", "CAN2_H", "CAN2_L"))):
        place_nc(ref, "flux:HDR4", ref.replace("J", "CAN"), 430, 130 + i * 30, {
            "1": "5V", "2": h, "3": l, "4": "GND"})
    place_nc("J14", "flux:HDR4", "ADC", 430, 200, {
        "1": "3V3", "2": "ADC1_INP10", "3": "ADC1_INP11", "4": "GND"})
    place_nc("J15", "flux:HDR5", "GND", 430, 250, {
        str(i): "GND" for i in range(1, 6)})
    place_nc("J16", "flux:HDR3", "DEBUG", 430, 300, {
        "1": "3V3", "2": "BOOT0", "3": "GND"})
    place_nc("J17", "flux:MICROSD", "microSD", 430, 350, {
        "1": "SDMMC1_D2", "2": "SDMMC1_D3", "3": "SDMMC1_CMD", "4": "3V3",
        "5": "SDMMC1_CK", "6": "GND", "7": "SDMMC1_D0", "8": "SDMMC1_D1",
        "SH": "GND"})
    sch.place(SymInst("D1", "flux:LED", "BLUE", x=100, y=280,
                      pin_nets={"1": "3V3", "2": "LED_ACT"}))
    sch.place(SymInst("R1", "flux:R_0603", "1k", x=100, y=295,
                      pin_nets={"1": "LED_ACT", "2": "GND"}))
    sch.rails = ["5V", "GND", "VDDA", "VCAP"]
    return sch


def write_schematic(project_dir: Path) -> Path:
    from fluxpcb.kicadgen.schematic import write as sch_write
    out = project_dir / "schematic" / "board" / (SLUG + ".kicad_sch")
    out.parent.mkdir(parents=True, exist_ok=True)
    sch_write(schematic(), out)
    return out
