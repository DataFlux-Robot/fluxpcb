from pathlib import Path

p = Path('fluxpcb/kicadgen/px4fmu.py')
t = p.read_text(encoding='utf-8')

OLD = '''def placement() -> dict[str, tuple[float, float, float]]:
    """FC rules: MCU center, IMU pair in the center vibration zone away
    from buck/USB, barometers split, mag far from buck, connectors on
    edges, buck corner."""'''

NEW = '''def footprints() -> dict:
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
    cluster around it, power cluster corner, connector cluster on edges."""'''

assert OLD in t
t = t.replace(OLD, NEW)

OLD2 = '''def write_board(project_dir: Path) -> Path:
    from fluxpcb.kicadgen.pcb import write as pcb_write
    out = project_dir / "pcb" / "board" / "stm32n6-px4.kicad_pcb"
    out.parent.mkdir(parents=True, exist_ok=True)
    pcb_write(board(), out)
    return out'''

NEW2 = '''def write_board(project_dir: Path) -> Path:
    from fluxpcb.kicadgen.pcb import write as pcb_write
    issues = validate_placement()
    if issues:
        raise ValueError("layout pre-flight FAILED (analytic, board NOT written): "
                         + "; ".join(issues[:4]))
    out = project_dir / "pcb" / "board" / "stm32n6-px4.kicad_pcb"
    out.parent.mkdir(parents=True, exist_ok=True)
    pcb_write(board(), out)
    return out'''

assert OLD2 in t
t = t.replace(OLD2, NEW2)
p.write_text(t, encoding='utf-8')
print('wired')
