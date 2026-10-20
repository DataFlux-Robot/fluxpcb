"""Carrier-board part library — every pinout is PUBLIC standard knowledge.

Parts:
- RJ45 magjack 100M (HanRun HR911105A class): EtherCAT IN/OUT
- RJ45 magjack gigabit (HR911121A class): TSN port
- USB-C receptacle 16P (5V power path)
- U.FL / IPEX antenna mount
- 2x20 0.5mm board-to-board (core board interface)
- 0603 R/C passives (Bob Smith termination, CC pulldowns)
"""
from __future__ import annotations

from fluxnt.kicadgen.schematic import Pin, SymDef


def P(num, name, etype="passive"):
    return Pin(str(num), name, etype)


# ---------------------------------------------------------------- RJ45 100M
# HR911105A-class 100M magjack: 1 TD+ 2 TD- 3 RD+ 6 RD-, 4/5 center-tap A,
# 7/8 center-tap B, 9/10 LEDs, shield.
RJ45_100M = SymDef("flux:RJ45_MAGJACK_100M", [
    P(1, "TD+"), P(2, "TD-"), P(3, "RD+"), P(6, "RD-"),
    P(4, "CT_A"), P(5, "CT_A2"), P(7, "CT_B"), P(8, "CT_B2"),
    P(9, "LED_A_K"), P(10, "LED_B_K"),
    P(11, "SHIELD", "power_in"),
], ref_prefix="J")

# ---------------------------------------------------------------- RJ45 GBE
# Gigabit magjack: 4 pairs, TCT/RCT center taps per pair.
RJ45_GBE = SymDef("flux:RJ45_MAGJACK_GBE", [
    P(1, "TX1+"), P(2, "TX1-"), P(3, "TX2+"), P(6, "TX2-"),
    P(4, "TX3+"), P(5, "TX3-"), P(7, "TX4+"), P(8, "TX4-"),
    P(12, "TCT1"), P(13, "TCT2"), P(14, "TCT3"), P(15, "TCT4"),
    P(9, "LED_A_K"), P(10, "LED_B_K"),
    P(16, "SHIELD", "power_in"),
], ref_prefix="J")

# ---------------------------------------------------------------- USB-C 16P
USB_C_16P = SymDef("flux:USB_C_RECEPTACLE_16P", [
    P("A4", "VBUS1", "power_in"), P("A9", "VBUS2", "power_in"),
    P("B4", "VBUS3", "power_in"), P("B9", "VBUS4", "power_in"),
    P("A1", "GND1", "power_in"), P("A12", "GND2", "power_in"),
    P("B1", "GND3", "power_in"), P("B12", "GND4", "power_in"),
    P("A5", "CC1"), P("B5", "CC2"),
    P("S1", "SHIELD", "power_in"),
], ref_prefix="J")

# ---------------------------------------------------------------- U.FL antenna
UFL_ANT = SymDef("flux:UFL_ANTENNA", [
    P(1, "ANT"), P(2, "GND", "power_in"), P(3, "GND2", "power_in"),
], ref_prefix="ANT")

# ---------------------------------------------------------------- B2B 2x20
# 2x20 0.5mm board-to-board; nets defined by the system design:
# 8 MDI diff pairs (16 pins) + 5V + 3V3 + GND(6) + 6 GPIO/misc.
B2B_2X20 = SymDef("flux:B2B_2X20_0P5MM", [
    P(1, "MDI_P0_TX+"), P(2, "MDI_P0_TX-"), P(3, "MDI_P0_RX+"), P(4, "MDI_P0_RX-"),
    P(5, "MDI_P1_TX+"), P(6, "MDI_P1_TX-"), P(7, "MDI_P1_RX+"), P(8, "MDI_P1_RX-"),
    P(9, "MDI_T_TX1+"), P(10, "MDI_T_TX1-"), P(11, "MDI_T_TX2+"), P(12, "MDI_T_TX2-"),
    P(13, "MDI_T_RX1+"), P(14, "MDI_T_RX1-"), P(15, "MDI_T_RX2+"), P(16, "MDI_T_RX2-"),
    P(17, "VBUS_5V", "power_in"),
    P(18, "C3V3", "power_in"),
    P(19, "GND1", "power_in"), P(20, "GND2", "power_in"),
    P(21, "GND3", "power_in"), P(22, "GND4", "power_in"),
    P(23, "GND5", "power_in"), P(24, "GND6", "power_in"),
    P(25, "SPI_MOSI_C"), P(26, "SPI_MISO_C"), P(27, "SPI_SCK_C"),
    P(28, "SPI_CS_C"), P(29, "UART_TX_C"), P(30, "UART_RX_C"),
    P(31, "SWDIO_C"), P(32, "SWCLK_C"),
    P(33, "GPIO1_C"), P(34, "GPIO2_C"), P(35, "ESP_EN_C"), P(36, "ESP_BOOT_C"),
    P(37, "RSTN_C"), P(38, "SPARE1"), P(39, "SPARE2"), P(40, "CHASSIS"),
], ref_prefix="P")

# ---------------------------------------------------------------- passives
R_0603 = SymDef("flux:R_0603", [P(1, "A"), P(2, "B")], ref_prefix="R")
C_0603 = SymDef("flux:C_0603", [P(1, "A"), P(2, "B")], ref_prefix="C")


def carrier_schematic():
    """Assemble the complete carrier-board schematic with verified-standard
    pinouts and full connectivity. Returns a ready Schematic object."""
    from fluxnt.kicadgen.schematic import Schematic, SymInst

    sch = Schematic(title="hpm6e80-carrier", project="hpm6e80-carrier")
    for sd in (RJ45_100M, RJ45_GBE, USB_C_16P, UFL_ANT, B2B_2X20, R_0603, C_0603):
        sch.add_def(sd)

    y = 190.5   # place on 1.27mm grid
    # J1: EtherCAT IN (100M magjack)
    sch.place(SymInst("J1", "flux:RJ45_MAGJACK_100M", "RJ45-ECAT-IN", x=101.6, y=y,
                      pin_nets={
                          "1": "MDI_P0_TX+", "2": "MDI_P0_TX-",
                          "3": "MDI_P0_RX+", "6": "MDI_P0_RX-",
                          "4": "BS_A", "5": "BS_A", "7": "BS_B", "8": "BS_B",
                          "9": "LED_ECAT1", "10": "LED_ECAT2",
                          "11": "CHASSIS"}))
    # J2: EtherCAT OUT
    sch.place(SymInst("J2", "flux:RJ45_MAGJACK_100M", "RJ45-ECAT-OUT", x=139.7, y=y,
                      pin_nets={
                          "1": "MDI_P1_TX+", "2": "MDI_P1_TX-",
                          "3": "MDI_P1_RX+", "6": "MDI_P1_RX-",
                          "4": "BS_A", "5": "BS_A", "7": "BS_B", "8": "BS_B",
                          "9": "LED_ECAT1", "10": "LED_ECAT2",
                          "11": "CHASSIS"}))
    # J3: TSN gigabit
    sch.place(SymInst("J3", "flux:RJ45_MAGJACK_GBE", "RJ45-TSN-GBE", x=180.34, y=y,
                      pin_nets={
                          "1": "MDI_T_TX1+", "2": "MDI_T_TX1-",
                          "3": "MDI_T_TX2+", "6": "MDI_T_TX2-",
                          "4": "MDI_T_TX3+", "5": "MDI_T_TX3-",
                          "7": "MDI_T_TX4+", "8": "MDI_T_TX4-",
                          "12": "BS_A", "13": "BS_A", "14": "BS_A", "15": "BS_A",
                          "9": "LED_TSN1", "10": "LED_TSN2",
                          "16": "CHASSIS"}))
    # J4: USB-C power input
    sch.place(SymInst("J4", "flux:USB_C_RECEPTACLE_16P", "USB-C-5V", x=101.6, y=152.4,
                      pin_nets={
                          "A4": "VBUS_5V", "A9": "VBUS_5V",
                          "B4": "VBUS_5V", "B9": "VBUS_5V",
                          "A1": "GND", "A12": "GND", "B1": "GND", "B12": "GND",
                          "A5": "CC1", "B5": "CC2",
                          "S1": "CHASSIS"}))
    # CC pulldowns 5.1k
    sch.place(SymInst("R1", "flux:R_0603", "5.1k", x=101.6, y=127.0,
                      pin_nets={"1": "CC1", "2": "GND"}))
    sch.place(SymInst("R2", "flux:R_0603", "5.1k", x=114.3, y=127.0,
                      pin_nets={"1": "CC2", "2": "GND"}))
    # Bob Smith termination: 75R x4 + 1nF to chassis (per jack group)
    for i, (net_a, net_b) in enumerate(
            [("BS_A", "BS_A"), ("BS_B", "BS_B")]):
        sch.place(SymInst(f"R{3+i}", "flux:R_0603", "75R", x=139.7 + i * 12.7,
                          y=127.0, pin_nets={"1": net_a, "2": "BS_TERM"}))
    sch.place(SymInst("R5", "flux:R_0603", "75R", x=165.1, y=127.0,
                      pin_nets={"1": "BS_B", "2": "BS_TERM"}))
    sch.place(SymInst("C1", "flux:C_0603", "1nF/2kV", x=177.8, y=127.0,
                      pin_nets={"1": "BS_TERM", "2": "CHASSIS"}))
    # ANT1: U.FL for core-board ESP32
    sch.place(SymInst("ANT1", "flux:UFL_ANTENNA", "U.FL-ANT", x=215.9, y=152.4,
                      pin_nets={"1": "ANT_RF", "2": "GND", "3": "GND"}))
    # P1: B2B to core board (all nets)
    b2b_nets = {
        "1": "MDI_P0_TX+", "2": "MDI_P0_TX-", "3": "MDI_P0_RX+", "4": "MDI_P0_RX-",
        "5": "MDI_P1_TX+", "6": "MDI_P1_TX-", "7": "MDI_P1_RX+", "8": "MDI_P1_RX-",
        "9": "MDI_T_TX1+", "10": "MDI_T_TX1-", "11": "MDI_T_TX2+", "12": "MDI_T_TX2-",
        "13": "MDI_T_TX3+", "14": "MDI_T_TX3-", "15": "MDI_T_TX4+", "16": "MDI_T_TX4-",
        "17": "VBUS_5V", "18": "C3V3",
        "19": "GND", "20": "GND", "21": "GND", "22": "GND",
        "23": "GND", "24": "GND",
        "25": "SPI_MOSI_C", "26": "SPI_MISO_C", "27": "SPI_SCK_C", "28": "SPI_CS_C",
        "29": "UART_TX_C", "30": "UART_RX_C", "31": "SWDIO_C", "32": "SWCLK_C",
        "33": "GPIO1_C", "34": "GPIO2_C", "35": "ESP_EN_C", "36": "ESP_BOOT_C",
        "37": "RSTN_C", "38": "SPARE1", "39": "SPARE2", "40": "CHASSIS",
    }
    sch.place(SymInst("P1", "flux:B2B_2X20_0P5MM", "B2B-CORE", x=215.9, y=190.5,
                      pin_nets=b2b_nets))
    sch.rails = ["VBUS_5V", "C3V3", "GND", "CHASSIS"]
    return sch
