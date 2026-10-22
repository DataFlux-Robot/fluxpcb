# -*- coding: utf-8 -*-
"""Net classification for staged routing (knowhow from 259-project mining).

Order-of-routing + per-class geometry, the classic flow validated by
ChengminLi/AI-PCB-layout ("先布线计划后分块布线"), jackwang988 &
swayerKICAD_ENGINE (critical nets first), jlceda (net classes),
boardwise (per-domain rule packages).

Deterministic (offline-first): spec impedance interfaces / MCU pins /
power rails + name heuristics. LLM (online) may overlay extra
critical_nets annotations via placement.md constraints.
"""
from __future__ import annotations

import re

# routing order: critical -> normal -> power (user-validated order)
ORDER = ("critical", "normal", "power")

DEFAULT_WIDTH_MM = {"critical": 0.15, "normal": 0.2, "power": 0.4}
DEFAULT_CLEAR_MM = {"critical": 0.15, "normal": 0.15, "power": 0.2}

_POWER_RE = re.compile(
    r"^(3V3|5V|VDD|VCC|VBUS|VIN|VOUT|VSYS|VDDA|VDDCORE|VDDCORE_SW|VREF|"
    r"VBAT|VLED|GND|AGND|PGND|\+3V3|\+5V|\+BATT)", re.I)
_CRITICAL_RE = re.compile(
    r"(USB_D[PM]|USB[ _]?D[PM]|_DP|_DM|DIFF|SDA|SCL|MOSI|MISO|SCK|SPI|"
    r"CAN_[HT]|OSC|XTAL|CLK|NRST|SWD)", re.I)


def classify_net(net: str, spec: dict | None = None,
                 critical_nets: set[str] | None = None) -> str:
    """net name -> 'critical' | 'power' | 'normal'."""
    if critical_nets and net in critical_nets:
        return "critical"
    if _POWER_RE.match(net):
        return "power"
    spec = spec or {}
    for itf in spec.get("interfaces", []):
        if itf.get("impedance_ohm"):          # spec-declared high speed
            for c in itf.get("connections", []):
                if (c if isinstance(c, str) else c.get("net", "")) == net:
                    return "critical"
    if _CRITICAL_RE.search(net):
        return "critical"
    return "normal"


def classify_all(nets: list[str], spec: dict | None = None,
                 critical_nets: set[str] | None = None) -> dict[str, str]:
    return {n: classify_net(n, spec, critical_nets) for n in nets}


def routing_order_pairs(pairs: list[str], classes: dict[str, str]) -> list[str]:
    """Sort `net|x1|y1|l1|x2|y2|l2` pair lines: critical first, power last."""
    def key(line: str):
        net = line.split("|", 1)[0]
        c = classes.get(net, "normal")
        return (ORDER.index(c) if c in ORDER else 1, net)
    return sorted(pairs, key=key)


def width_for(net: str, classes: dict[str, str],
              ipc_widths: dict[str, float] | None = None) -> float:
    """Track width per class; critical uses the IPC-solved width when the
    net belongs to a solved net class (netclasses.json width_mm)."""
    c = classes.get(net, "normal")
    if c == "critical" and ipc_widths:
        for _cls, w in ipc_widths.items():
            if isinstance(w, (int, float)) and w:
                return float(w)
    return DEFAULT_WIDTH_MM[c]


def kicad_net_classes(classes: dict[str, str],
                      ipc_widths: dict | None = None) -> list[dict]:
    """KiCad net-class dicts for .kicad_pro — these flow into DSN export so
    Freerouting routes per class (widths/clearances honored per class)."""
    crit_w = DEFAULT_WIDTH_MM["critical"]
    if ipc_widths:
        vals = [w for w in ipc_widths.values() if isinstance(w, (int, float))]
        if vals:
            crit_w = max(min(vals), 0.127)
    return [
        {"name": "Power", "clearance": DEFAULT_CLEAR_MM["power"],
         "diff_pair_gap": 0.25, "diff_pair_via_gap": 0.25,
         "diff_pair_width": 0.4, "line_style": 0,
         "microvia_diameter": 0.3, "microvia_drill": 0.1,
         "pcb_color": "rgba(0, 0, 0, 0.000)", "priority": 1,
         "schematic_color": "rgba(0, 0, 0, 0.000)",
         "track_width": DEFAULT_WIDTH_MM["power"], "tuning_profile": "",
         "via_diameter": 0.6, "via_drill": 0.3, "wire_width": 6,
         "bus_width": 12,
         "netclass_patterns": [n for n, c in classes.items() if c == "power"]},
        {"name": "Critical", "clearance": DEFAULT_CLEAR_MM["critical"],
         "diff_pair_gap": 0.25, "diff_pair_via_gap": 0.25,
         "diff_pair_width": crit_w, "line_style": 0,
         "microvia_diameter": 0.3, "microvia_drill": 0.1,
         "pcb_color": "rgba(0, 0, 0, 0.000)", "priority": 2,
         "schematic_color": "rgba(0, 0, 0, 0.000)",
         "track_width": crit_w, "tuning_profile": "",
         "via_diameter": 0.6, "via_drill": 0.3, "wire_width": 6,
         "bus_width": 12,
         "netclass_patterns": [n for n, c in classes.items() if c == "critical"]},
        {"name": "Default", "clearance": DEFAULT_CLEAR_MM["normal"],
         "diff_pair_gap": 0.25, "diff_pair_via_gap": 0.25,
         "diff_pair_width": 0.2, "line_style": 0,
         "microvia_diameter": 0.3, "microvia_drill": 0.1,
         "pcb_color": "rgba(0, 0, 0, 0.000)", "priority": 2147483647,
         "schematic_color": "rgba(0, 0, 0, 0.000)",
         "track_width": DEFAULT_WIDTH_MM["normal"], "tuning_profile": "",
         "via_diameter": 0.6, "via_drill": 0.3, "wire_width": 6,
         "bus_width": 12, "netclass_patterns": []},
    ]


def apply_net_classes(pro_path, classes: dict[str, str],
                      ipc_widths: dict | None = None) -> bool:
    """Write Power/Critical/Default classes into the board .kicad_pro so
    pcbnew's DSN export (and thus Freerouting) sees per-class geometry."""
    import json
    try:
        data = json.loads(pro_path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return False
    node = data.get("board")
    if not isinstance(node, dict):
        return False
    ns = data.setdefault("net_settings", {})
    ns["classes"] = kicad_net_classes(classes, ipc_widths)
    pro_path.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                        encoding="utf-8")
    return True


def nets_from_board(pcb_path) -> list[str]:
    """All net names from a .kicad_pcb (KiCad 10 name-or-number nets)."""
    import re as _re
    txt = pcb_path.read_text(encoding="utf-8", errors="replace")
    names = set()
    for m in _re.finditer(r'\(\s*net(?:\s*\d+)?\s+"([^"]+)"', txt):
        if m.group(1):
            names.add(m.group(1))
    return sorted(names)
