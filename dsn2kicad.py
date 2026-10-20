# -*- coding: utf-8 -*-
"""Inject Freerouting's routed DSN wires/vias into the KiCad board file."""
import re
import sys
import uuid as uuidlib
from pathlib import Path

from fluxnt.kicadgen.pcb import parse, render

LAYER_MAP = {
    "top_copper": "F.Cu", "bottom_copper": "B.Cu",
    "inner1": "In1.Cu", "inner2": "In2.Cu",
    "In1.Cu": "In1.Cu", "In2.Cu": "In2.Cu",
    "mid1": "In1.Cu", "mid2": "In2.Cu",
}


def uid():
    return str(uuidlib.uuid4())


def f(v):
    s = f"{v:.4f}".rstrip("0").rstrip(".")
    return s if s else "0"


def convert(dsn_path: Path, pcb_path: Path) -> dict:
    txt = dsn_path.read_text(encoding="utf-8", errors="replace")
    # net name -> number from the pcb file
    pcb_txt = pcb_path.read_text(encoding="utf-8")
    head = pcb_txt[:pcb_txt.find("(footprint")]
    net_nums = dict((n, i) for i, n in
                    re.findall(r'\(net (\d+) "([^"]+)"\)', head))
    net_nums[""] = "0"

    segments, vias = [], []
    stats = {"wires": 0, "segs": 0, "skipped_degenerate": 0, "vias": 0}
    # wire blocks
    for m in re.finditer(
            r'\(wire\s*\(polyline_path "([^"]+)" ([0-9.]+)\s*([0-9eE.,\s\-]+?)\)\s*'
            r'\(net ([^\s)]+)', txt):
        layer_raw, width, coords_raw, net = m.groups()
        net = net.strip('"')
        block = txt[m.start():m.end() + 60]
        if "shoveFixed" in block:
            stats["skipped_shove_fixed"] = stats.get("skipped_shove_fixed", 0) + 1
            continue
        layer = LAYER_MAP.get(layer_raw)
        if layer is None:
            continue
        w_mm = float(width) / 1000.0
        nums = [float(x) for x in coords_raw.replace("\n", " ").split()]
        pairs = [(nums[i], nums[i + 1], nums[i + 2], nums[i + 3])
                 for i in range(0, len(nums) - 3, 4)]
        nn = net_nums.get(net, "0")
        nm = net if net else ""
        stats["wires"] += 1
        for x1, y1, x2, y2 in pairs:
            length = ((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5
            if length < 0.05:  # exact-zero only; sub-um stubs are pad anchors
                stats["skipped_degenerate"] += 1
                continue
            segments.append(
                f'\t(segment (start {f(x1/1000)} {f(-y1/1000)}) '
                f'(end {f(x2/1000)} {f(-y2/1000)}) (width {f(w_mm)}) '
                f'(layer "{layer}") (net {nn}) (uuid "{uid()}"))')
            stats["segs"] += 1
    # via padstack sizes: name pattern Via[a-b]_size:drill_um
    via_specs = {}
    for name in set(re.findall(r'"(Via\[[0-9\-]+\]_[0-9]+:[0-9]+_um)"', txt)):
        m2 = re.search(r"_(\d+):(\d+)_um", name)
        if m2:
            via_specs[name] = (int(m2.group(1)) / 1000, int(m2.group(2)) / 1000)
    # via instances: (via "padstack" "padstack" (net X n) ... (x y) ...) — simpler regex
    for m in re.finditer(
            r'\(via "(Via\[[0-9\-]+\]_[0-9]+:[0-9]+_um)" ([\-0-9.]+) ([\-0-9.]+)'
            r'\s*\(net ([^\s)]+)', txt):
        ps, x, y, net = m.groups()
        spec = via_specs.get(ps, (0.6, 0.3))
        net = net.strip(chr(34))
        if net not in net_nums:
            continue
        nn = net_nums[net]
        nm = net
        vias.append(
            f'	(via (at {f(float(x)/1000)} {f(-float(y)/1000)}) '
            f'(size {f(spec[0])}) (drill {f(spec[1])}) '
            f'(layers "F.Cu" "B.Cu") (remove_unused_layers) '
            f'(net {nn}) (uuid "{uid()}"))')
        stats["vias"] += 1

    if not vias:  # fallback via regex if structure differs
        for m in re.finditer(r'\(via\s+"([^"]+)"\s+"[^"]+"\s*\(net ([^\s)]+)', txt):
            pass  # counted via_specs only

    inject = "\n".join(segments + vias)
    if inject:
        pcb_txt = pcb_txt.rstrip()
        assert pcb_txt.endswith(")")
        # insert before final close paren, after last existing item
        pcb_txt = pcb_txt[:-1] + inject + "\n)\n"
        pcb_path.write_text(pcb_txt, encoding="utf-8")
    return stats


if __name__ == "__main__":
    dsn = Path(sys.argv[1])
    pcb = Path(sys.argv[2])
    print(convert(dsn, pcb))
