# -*- coding: utf-8 -*-
"""Build the hpm6e80 carrier board PCB from official footprints."""
from fluxpcb.kicadgen.pcb import Board, Fp, _f, parse, render, uid

NETS = [
    "MDI_P0_TX+", "MDI_P0_TX-", "MDI_P0_RX+", "MDI_P0_RX-",
    "MDI_P1_TX+", "MDI_P1_TX-", "MDI_P1_RX+", "MDI_P1_RX-",
    "MDI_T_TX1+", "MDI_T_TX1-", "MDI_T_TX2+", "MDI_T_TX2-",
    "MDI_T_TX3+", "MDI_T_TX3-", "MDI_T_TX4+", "MDI_T_TX4-",
    "VBUS_5V", "C3V3", "GND",
    "CC1", "CC2", "BS_A", "BS_B", "BS_TERM",
    "LED_ECAT1", "LED_ECAT2", "LED_TSN1", "LED_TSN2", "ANT_RF",
    "SPI_MOSI_C", "SPI_MISO_C", "SPI_SCK_C", "SPI_CS_C",
    "UART_TX_C", "UART_RX_C", "SWDIO_C", "SWCLK_C",
    "GPIO1_C", "GPIO2_C", "ESP_EN_C", "ESP_BOOT_C", "RSTN_C", "SPARE1", "SPARE2",
]


class B2B_2x20(Fp):
    """Generic 2x20 0.5mm-pitch board-to-board, SMD, 0.3x1.5mm pads,
    rows 8.0mm apart (order-time confirmation flagged in BOM)."""

    def __init__(self, ref, x, y, rot, pad_nets):
        self.lib, self.name = "flux", "B2B_2x20_0P5MM"
        self.ref, self.value = ref, "B2B 2x20 0.5mm"
        self.x, self.y, self.rot = x, y, rot
        self.pad_nets = pad_nets
        rows = []
        for i in range(20):
            rows.append(self._pad(str(i + 1), 0.0, -(9.5 - i * 0.5)))
        for i in range(20):
            rows.append(self._pad(str(i + 21), 8.0, -(9.5 - i * 0.5)))
        tree = [
            ("a", "footprint"), ("s", "flux:B2B_2x20_0P5MM"),
            ("n", [("a", "layer"), ("s", "F.Cu")]),
        ] + rows
        self.tree = tree

    def _pad(self, num, dx, dy):
        return ("n", [
            ("a", "pad"), ("s", num), ("a", "smd"), ("a", "rect"),
            ("n", [("a", "at"), ("a", _f(dx)), ("a", _f(dy))]),
            ("n", [("a", "size"), ("a", "1.5"), ("a", "0.25")]),
            ("n", [("a", "layers"), ("s", "F.Cu"), ("s", "F.Paste"), ("s", "F.Mask")]),
            ("n", [("a", "uuid"), ("s", uid())]),
        ])


def build_board():
    board = Board("hpm6e80-carrier", 90.0, 55.0, NETS)

    # J1/J2: EtherCAT 100M magjacks (top edge, HanRun HR911105A)
    for tag, x, idx in (("J1", 14.0, "P0"), ("J2", 38.0, "P1")):
        board.place(Fp("Connector_RJ", "RJ45_Hanrun_HR911105A_Horizontal",
                       tag, "RJ45-ECAT-" + idx, x, 12.0,
                       pad_nets={
                           "1": f"MDI_{idx}_TX+", "2": f"MDI_{idx}_TX-",
                           "3": f"MDI_{idx}_RX+", "6": f"MDI_{idx}_RX-",
                           "4": "BS_A", "5": "BS_A", "7": "BS_B", "8": "BS_B",
                           "9": "LED_ECAT1", "10": "LED_ECAT2",
                           "11": "GND", "12": "GND",
                           "SH": "GND"}))
    # J3: TSN gigabit (same body family; GBE magjack pin mapping on 8 data pads
    # + CT to Bob Smith; footprint order-time confirmation flagged in BOM)
    board.place(Fp("Connector_RJ", "RJ45_Hanrun_HR911105A_Horizontal",
                   "J3", "RJ45-TSN-GBE", 66.0, 12.0,
                   pad_nets={
                       "1": "MDI_T_TX1+", "2": "MDI_T_TX1-",
                       "3": "MDI_T_TX2+", "6": "MDI_T_TX2-",
                       "4": "MDI_T_TX3+", "5": "MDI_T_TX3-",
                       "7": "MDI_T_TX4+", "8": "MDI_T_TX4-",
                       "9": "LED_TSN1", "10": "LED_TSN2",
                       "11": "BS_A", "12": "BS_A", "SH": "GND"}))
    # J4: USB-C 5V input (left edge, rotated 270deg)
    board.place(Fp("Connector_USB", "USB_C_Receptacle_HRO_TYPE-C-31-M-12",
                   "J4", "USB-C-5V", 10.0, 50.0, 0.0,
                   pad_nets={"A4": "VBUS_5V", "A9": "VBUS_5V",
                             "B4": "VBUS_5V", "B9": "VBUS_5V",
                             "A1": "GND", "A12": "GND", "B1": "GND", "B12": "GND",
                             "A5": "CC1", "B5": "CC2", "SH": "GND"}))
    # CC pulldowns + Bob Smith
    board.place(Fp("Resistor_SMD", "R_0603_1608Metric", "R1", "5.1k", 22.0, 48.0,
                   pad_nets={"1": "CC1", "2": "GND"}))
    board.place(Fp("Resistor_SMD", "R_0603_1608Metric", "R2", "5.1k", 26.0, 48.0,
                   pad_nets={"1": "CC2", "2": "GND"}))
    board.place(Fp("Resistor_SMD", "R_0603_1608Metric", "R3", "75R", 50.0, 30.0,
                   pad_nets={"1": "BS_A", "2": "BS_TERM"}))
    board.place(Fp("Resistor_SMD", "R_0603_1608Metric", "R4", "75R", 54.0, 30.0,
                   pad_nets={"1": "BS_A", "2": "BS_TERM"}))
    board.place(Fp("Resistor_SMD", "R_0603_1608Metric", "R5", "75R", 58.0, 30.0,
                   pad_nets={"1": "BS_B", "2": "BS_TERM"}))
    board.place(Fp("Capacitor_SMD", "C_0603_1608Metric", "C1", "1nF/2kV", 62.0, 30.0,
                   pad_nets={"1": "BS_TERM", "2": "GND"}))
    # ANT1
    board.place(Fp("Connector_Coaxial", "U.FL_Hirose_U.FL-R-SMT-1_Vertical",
                   "ANT1", "U.FL-ANT", 84.0, 48.0,
                   pad_nets={"1": "ANT_RF", "2": "GND"}))
    # P1: B2B to core board
    b2b = {}
    for i, net in enumerate([
            "MDI_P0_TX+", "MDI_P0_TX-", "MDI_P0_RX+", "MDI_P0_RX-",
            "MDI_P1_TX+", "MDI_P1_TX-", "MDI_P1_RX+", "MDI_P1_RX-",
            "MDI_T_TX1+", "MDI_T_TX1-", "MDI_T_TX2+", "MDI_T_TX2-",
            "MDI_T_TX3+", "MDI_T_TX3-", "MDI_T_TX4+", "MDI_T_TX4-",
            "VBUS_5V", "C3V3", "GND", "GND", "GND",
            "SPI_MOSI_C", "SPI_MISO_C", "SPI_SCK_C", "SPI_CS_C",
            "UART_TX_C", "UART_RX_C", "SWDIO_C", "SWCLK_C",
            "GPIO1_C", "GPIO2_C", "ESP_EN_C", "ESP_BOOT_C",
            "RSTN_C", "SPARE1", "SPARE2", "GND"], 1):
        b2b[str(i)] = net
    board.place(B2B_2x20("P1", 76.0, 40.0, 0.0, b2b))

    return board


def main():
    from fluxpcb.kicadgen.pcb import write
    write(build_board(), "carrier_out/hpm6e80-carrier.kicad_pcb")
    print("carrier PCB written")


if __name__ == "__main__":
    main()
