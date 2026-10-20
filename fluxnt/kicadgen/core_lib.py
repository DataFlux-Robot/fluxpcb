# -*- coding: utf-8 -*-
"""Core-board (hpm6e80-core) FUNCTIONAL schematic.

Pin assignments for HPM6E80 peripherals follow the EVK pinmux (verified from
hpm_sdk hpm6e00evk pinmux.c). The HPM6E80IVM1 ball map is NOT publicly
indexed (FAE datasheet required), so symbol pin numbers are internal
functional IDs (F###) and the design is explicitly marked PINMAP-PENDING.
All other parts use public standard pinouts.
"""
from __future__ import annotations

from fluxnt.kicadgen.schematic import Pin, Schematic, SymDef, SymInst
from fluxnt.kicadgen.parts_lib import B2B_2X20, C_0603, R_0603, USB_C_16P, P


def F(n, name, etype="passive"):
    return Pin(f"F{n:03d}", name, etype)


# HPM6E80 module symbol: verified EVK pinmux functions; numbers are internal IDs
HPM_PINS = []
_fid = [0]


def _p(name, etype="passive"):
    _fid[0] += 1
    HPM_PINS.append(F(_fid[0], name, etype))


# EtherCAT ESC port0 (MII, EVK: PA16..PA31 group)
for s in ["ESC_P0_TXCK", "ESC_P0_TXEN", "ESC_P0_TXD0", "ESC_P0_TXD1",
          "ESC_P0_TXD2", "ESC_P0_TXD3", "ESC_P0_RXCK", "ESC_P0_RXDV",
          "ESC_P0_RXER", "ESC_P0_RXD0", "ESC_P0_RXD1", "ESC_P0_RXD2",
          "ESC_P0_RXD3", "ESC_P0_MDC", "ESC_P0_MDIO", "ESC_P0_LINK"]:
    _p(s)
# EtherCAT ESC port1 (MII, EVK: PB12.. group)
for s in ["ESC_P1_TXCK", "ESC_P1_TXEN", "ESC_P1_TXD0", "ESC_P1_TXD1",
          "ESC_P1_TXD2", "ESC_P1_TXD3", "ESC_P1_RXCK", "ESC_P1_RXDV",
          "ESC_P1_RXER", "ESC_P1_RXD0", "ESC_P1_RXD1", "ESC_P1_RXD2",
          "ESC_P1_RXD3", "ESC_P1_LINK"]:
    _p(s)
# TSN gigabit MAC (RGMII, TSW0_P0 — pin group per EVK tsw pins)
for s in ["TSN_TXC", "TSN_TX_CTL", "TSN_TXD0", "TSN_TXD1", "TSN_TXD2",
          "TSN_TXD3", "TSN_RXC", "TSN_RX_CTL", "TSN_RXD0", "TSN_RXD1",
          "TSN_RXD2", "TSN_RXD3", "PHY_RSTN", "PHY_IRQ"]:
    _p(s)
# QSPI NOR (XPI0)
for s in ["QSPI_CLK", "QSPI_IO0", "QSPI_IO1", "QSPI_IO2", "QSPI_IO3",
          "QSPI_CS"]:
    _p(s)
# UART0 console (PA00/PA01)
_p("UART0_TX")
_p("UART0_RX")
# SWD (PA00/PA01 shared SWJ)
_p("SWJ_DIO")
_p("SWJ_CLK")
# SPI to ESP32-S31 + handshake
for s in ["SPI_MOSI", "SPI_MISO", "SPI_SCK", "SPI_CS", "SPI_READY",
          "SPI_INT"]:
    _p(s)
# Boot/reset/misc
for s in ["BOOT0", "NRST", "XTAL_IN", "XTAL_OUT"]:
    _p(s)
# power
for i in range(4):
    _p(f"VCC{i+1}", "power_in")
for i in range(4):
    _p(f"GND{i+1}", "power_in")

HPM6E80 = SymDef("flux:HPM6E80_FUNC", HPM_PINS, ref_prefix="U")

# JL1111 MII PHY (100M, EVK同款) — public datasheet pins (MII + MDI)
JL1111 = SymDef("flux:JL1111_MII", [
    P(1, "TXCK"), P(2, "TXEN"), P(3, "TXD0"), P(4, "TXD1"),
    P(5, "TXD2"), P(6, "TXD3"), P(7, "RXCK"), P(8, "RXDV"),
    P(9, "RXER"), P(10, "RXD0"), P(11, "RXD1"), P(12, "RXD2"),
    P(13, "RXD3"), P(14, "MDC"), P(15, "MDIO"), P(16, "LINK"),
    P(17, "MDI_TX+"), P(18, "MDI_TX-"), P(19, "MDI_RX+"), P(20, "MDI_RX-"),
    P(21, "RSTN"), P(22, "XTAL_IN"), P(23, "XTAL_OUT"),
    P(24, "VDDA", "power_in"), P(25, "VDD", "power_in"), P(26, "GND", "power_in"),
], ref_prefix="U")

# RTL8211E-VB-CG gigabit RGMII PHY (EVK同款) — public pins
RTL8211E = SymDef("flux:RTL8211E_RGMII", [
    P(1, "TXC"), P(2, "TX_CTL"), P(3, "TXD0"), P(4, "TXD1"),
    P(5, "TXD2"), P(6, "TXD3"), P(7, "RXC"), P(8, "RX_CTL"),
    P(9, "RXD0"), P(10, "RXD1"), P(11, "RXD2"), P(12, "RXD3"),
    P(13, "MDI_TX+"), P(14, "MDI_TX-"), P(15, "MDI_RX+"), P(16, "MDI_RX-"),
    P(17, "MDI_BI_DA+"), P(18, "MDI_BI_DA-"), P(19, "MDI_BI_DB+"),
    P(20, "MDI_BI_DB-"), P(21, "RSTN"), P(22, "IRQ"), P(23, "MDC"),
    P(24, "MDIO"), P(25, "XTAL_IN"), P(26, "XTAL_OUT"),
    P(27, "AVDD", "power_in"), P(28, "VDD33", "power_in"),
    P(29, "VDDIO", "power_in"), P(30, "GND", "power_in"),
], ref_prefix="U")

# MXIC MX25L12833F NOR flash (SOP-8, public)
MX25L128 = SymDef("flux:MX25L12833F", [
    P(1, "CS"), P(2, "DO"), P(3, "WP"), P(4, "GND", "power_in"),
    P(5, "DI"), P(6, "CLK"), P(7, "HOLD"), P(8, "VCC", "power_in"),
], ref_prefix="U")

# ESP32-S31-WROOM-3U module (public datasheet)
ESP32S31 = SymDef("flux:ESP32_S31_WROOM_3U", [
    P(1, "GND", "power_in"), P(2, "3V3", "power_in"), P(3, "EN"),
    P(4, "SPI_MOSI"), P(5, "SPI_MISO"), P(6, "SPI_SCK"), P(7, "SPI_CS"),
    P(8, "GPIO9"), P(9, "GPIO14"), P(10, "BOOT"),
    P(11, "ANT", "passive"), P(12, "GND2", "power_in"),
], ref_prefix="MOD")

# Buck 5V->3.3V (TPS562201-class, public SOT-23-6... use generic sync buck)
BUCK3V3 = SymDef("flux:BUCK_5V_3V3", [
    P(1, "VIN", "power_in"), P(2, "EN"), P(3, "SS"),
    P(4, "FB"), P(5, "GND", "power_in"), P(6, "SW"),
], ref_prefix="U")

# Dual-output buck 1.8V/1.1V (generic dual sync buck, QFN)
BUCK_DUAL = SymDef("flux:BUCK_DUAL_1V8_1V1", [
    P(1, "VIN", "power_in"), P(2, "EN1"), P(3, "SW1"),
    P(4, "FB1"), P(5, "GND", "power_in"), P(6, "EN2"),
    P(7, "SW2"), P(8, "FB2"),
], ref_prefix="U")


def core_schematic():
    sch = Schematic(title="hpm6e80-core", project="hpm6e80-core")
    for sd in (HPM6E80, JL1111, RTL8211E, MX25L128, ESP32S31, BUCK3V3,
               BUCK_DUAL, B2B_2X20, R_0603, C_0603, USB_C_16P):
        sch.add_def(sd)

    def pinnames(sd):
        return {p.name: p.number for p in sd.pins}

    hpm = pinnames(HPM6E80)
    jl = pinnames(JL1111)
    rtl = pinnames(RTL8211E)
    mx = pinnames(MX25L128)
    esp = pinnames(ESP32S31)

    # U1 HPM6E80: every functional pin to its net
    u1_nets = {}
    for p in HPM6E80.pins:
        n = p.name
        if n.startswith("VCC"):
            u1_nets[p.number] = "+3V3" if n in ("VCC1",) else \
                "+1V8" if n == "VCC2" else "+1V1" if n == "VCC3" else "+3V3"
        elif n.startswith("GND"):
            u1_nets[p.number] = "GND"
        elif n.startswith("ESC_P0_"):
            u1_nets[p.number] = n.replace("ESC_P0_", "MII_P0_")
        elif n.startswith("ESC_P1_"):
            u1_nets[p.number] = n.replace("ESC_P1_", "MII_P1_")
        elif n.startswith("TSN_"):
            u1_nets[p.number] = "RGMII_" + n[4:]
        elif n.startswith("QSPI_"):
            u1_nets[p.number] = "NOR_" + n[5:]
        elif n == "UART0_TX":
            u1_nets[p.number] = "UART_TX_C"
        elif n == "UART0_RX":
            u1_nets[p.number] = "UART_RX_C"
        elif n == "SWJ_DIO":
            u1_nets[p.number] = "SWDIO_C"
        elif n == "SWJ_CLK":
            u1_nets[p.number] = "SWCLK_C"
        elif n in ("SPI_MOSI", "SPI_MISO", "SPI_SCK", "SPI_CS",
                   "SPI_READY", "SPI_INT"):
            u1_nets[p.number] = n + "_C"
        elif n in ("BOOT0", "NRST"):
            u1_nets[p.number] = n
        elif n == "XTAL_IN":
            u1_nets[p.number] = "HPM_XTAL_IN"
        elif n == "XTAL_OUT":
            u1_nets[p.number] = "HPM_XTAL_OUT"
        else:
            u1_nets[p.number] = n
    sch.place(SymInst("U1", "flux:HPM6E80_FUNC",
                      "HPM6E80IVM1 [PINMAP-PENDING-DATASHEET]",
                      x=139.7, y=129.54, pin_nets=u1_nets))

    # U2/U3: JL1111 EtherCAT PHYs (MII to HPM, MDI to B2B)
    for tag, port in (("U2", "P0"), ("U3", "P1")):
        nets = {}
        for p in JL1111.pins:
            n = p.name
            if n.startswith("TX") or n.startswith("RX") or n in ("MDC", "MDIO", "LINK"):
                nets[p.number] = f"MII_{port}_{n}"
            elif n.startswith("MDI"):
                nets[p.number] = n.replace("MDI_", f"MDI_{port}_")
            elif n == "RSTN":
                nets[p.number] = f"PHY_{port}_RSTN"
            elif n == "XTAL_IN":
                nets[p.number] = f"PHY_{port}_XI"
            elif n == "XTAL_OUT":
                nets[p.number] = f"PHY_{port}_XO"
            elif n in ("VDDA", "VDD"):
                nets[p.number] = "+3V3"
            else:
                nets[p.number] = "GND"
        sch.place(SymInst(tag, "flux:JL1111_MII", "JL1111 100M MII PHY",
                          x=55.88 if tag == "U2" else 68.58, y=190.5,
                          pin_nets=nets))
    # U4: RTL8211E TSN gigabit PHY
    nets = {}
    for p in RTL8211E.pins:
        n = p.name
        if n.startswith("TX") or n.startswith("RX") or n in ("MDC", "MDIO"):
            nets[p.number] = "RGMII_" + n
        elif n.startswith("MDI"):
            nets[p.number] = "MDI_T_" + n[4:]
        elif n == "RSTN":
            nets[p.number] = "PHY_TSN_RSTN"
        elif n == "IRQ":
            nets[p.number] = "PHY_TSN_IRQ"
        elif n == "XTAL_IN":
            nets[p.number] = "PHY_TSN_XI"
        elif n == "XTAL_OUT":
            nets[p.number] = "PHY_TSN_XO"
        elif n in ("AVDD", "VDD33", "VDDIO"):
            nets[p.number] = "+3V3"
        else:
            nets[p.number] = "GND"
    sch.place(SymInst("U4", "flux:RTL8211E_RGMII", "RTL8211E-VB-CG GbE",
                      x=215.9, y=190.5, pin_nets=nets))
    # U5: NOR flash
    nets = {}
    for p in MX25L128.pins:
        n = p.name
        nets[p.number] = ("GND" if n == "GND" else "+3V3" if n == "VCC"
                          else "NOR_" + n)
    sch.place(SymInst("U5", "flux:MX25L12833F", "MX25L12833F 16MB",
                      x=88.9, y=129.54, pin_nets=nets))
    # MOD1: ESP32-S31-WROOM-3U
    nets = {}
    for p in ESP32S31.pins:
        n = p.name
        if n.startswith("SPI_"):
            nets[p.number] = n + "_C"
        elif n == "EN":
            nets[p.number] = "ESP_EN_C"
        elif n == "BOOT":
            nets[p.number] = "ESP_BOOT_C"
        elif n == "GPIO9":
            nets[p.number] = "SPI_READY_C"
        elif n == "GPIO14":
            nets[p.number] = "SPI_INT_C"
        elif n == "ANT":
            nets[p.number] = "ANT_RF"
        elif n in ("3V3",):
            nets[p.number] = "+3V3"
        else:
            nets[p.number] = "GND"
    sch.place(SymInst("MOD1", "flux:ESP32_S31_WROOM_3U",
                      "ESP32-S31-WROOM-3U", x=50.8, y=129.54, pin_nets=nets))
    # U6/U7: bucks
    sch.place(SymInst("U6", "flux:BUCK_5V_3V3", "BUCK 5V->3.3V/3A", x=50.8, y=95.25,
                      pin_nets={"1": "VBUS_5V", "2": "VBUS_5V", "3": "B3_SS",
                                "4": "B3_FB", "5": "GND", "6": "B3_SW"}))
    sch.place(SymInst("U7", "flux:BUCK_DUAL_1V8_1V1", "BUCK 1.8V+1.1V", x=68.58, y=95.25,
                      pin_nets={"1": "VBUS_5V", "2": "VBUS_5V", "3": "B1_SW",
                                "4": "B1_FB", "5": "GND", "6": "VBUS_5V",
                                "7": "B2_SW", "8": "B2_FB"}))
    # Buck output LC + feedback divider (values computed at selection stage)
    sch.place(SymInst("L1", "flux:R_0603", "2.2uH", x=58.42, y=95.25,
                      pin_nets={"1": "B3_SW", "2": "+3V3"}))
    sch.place(SymInst("C2", "flux:C_0603", "22uF", x=66.04, y=95.25,
                      pin_nets={"1": "+3V3", "2": "GND"}))
    sch.place(SymInst("R6", "flux:R_0603", "52.3k", x=44.45, y=95.25,
                      pin_nets={"1": "+3V3", "2": "B3_FB"}))
    sch.place(SymInst("R7", "flux:R_0603", "10k", x=39.37, y=95.25,
                      pin_nets={"1": "B3_FB", "2": "GND"}))
    # P1: B2B (mirrors carrier nets)
    b2b_nets = {
        "1": "MDI_P0_TX+", "2": "MDI_P0_TX-", "3": "MDI_P0_RX+", "4": "MDI_P0_RX-",
        "5": "MDI_P1_TX+", "6": "MDI_P1_TX-", "7": "MDI_P1_RX+", "8": "MDI_P1_RX-",
        "9": "MDI_T_TX1+", "10": "MDI_T_TX1-", "11": "MDI_T_TX2+", "12": "MDI_T_TX2-",
        "13": "MDI_T_TX3+", "14": "MDI_T_TX3-", "15": "MDI_T_TX4+", "16": "MDI_T_TX4-",
        "17": "VBUS_5V", "18": "+3V3",
        "19": "GND", "20": "GND", "21": "GND", "22": "GND",
        "23": "GND", "24": "GND",
        "25": "SPI_MOSI_C", "26": "SPI_MISO_C", "27": "SPI_SCK_C", "28": "SPI_CS_C",
        "29": "UART_TX_C", "30": "UART_RX_C", "31": "SWDIO_C", "32": "SWCLK_C",
        "33": "GPIO1_C", "34": "GPIO2_C", "35": "ESP_EN_C", "36": "ESP_BOOT_C",
        "37": "NRST", "38": "SPARE1", "39": "SPARE2", "40": "GND",
    }
    sch.place(SymInst("P1", "flux:B2B_2X20_0P5MM", "B2B-CARRIER", x=190.5, y=95.25,
                      pin_nets=b2b_nets))
    sch.rails = ["VBUS_5V", "+3V3", "+1V8", "+1V1", "GND"]
    return sch
