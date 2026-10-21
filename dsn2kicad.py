# -*- coding: utf-8 -*-
"""Inject Freerouting's routed DSN wires/vias into the KiCad board file."""
import re
import sys
import uuid as uuidlib
from pathlib import Path

from fluxpcb.kicadgen.pcb import parse, render

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


def weld_endpoints(segments, vias, tol_mm=0.005):
    """Merge near-coincident (<5um) chain endpoints (and via points) so KiCad
    connectivity closes across the router's 0.1um anchor steps."""
    import math
    pat = re.compile(
        r'(\(segment \(start )([-0-9.]+) ([-0-9.]+)(\) \(end )([-0-9.]+) ([-0-9.]+)(\).*)')
    via_pat = re.compile(r'(\(via \(at )([-0-9.]+) ([-0-9.]+)(\).*)')

    pts = []
    parsed = []
    for ln in segments:
        m = pat.match(ln)
        parsed.append(m)
        if m:
            pts.append((float(m.group(2)), float(m.group(3))))
            pts.append((float(m.group(5)), float(m.group(6))))
    via_parsed = []
    for ln in vias:
        m = via_pat.match(ln)
        via_parsed.append(m)
        if m:
            pts.append((float(m.group(2)), float(m.group(3))))

    parent = list(range(len(pts)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    # grid-bucket to avoid O(n^2) worst case (small n anyway)
    buckets = {}
    cell = tol_mm
    for i, (x, y) in enumerate(pts):
        buckets.setdefault((math.floor(x / cell), math.floor(y / cell)), []).append(i)
    for (bx, by), idxs in buckets.items():
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for j in buckets.get((bx + dx, by + dy), []):
                    for i in idxs:
                        if i < j and (abs(pts[i][0] - pts[j][0]) <= tol_mm and
                                      abs(pts[i][1] - pts[j][1]) <= tol_mm):
                            ri, rj = find(i), find(j)
                            if ri != rj:
                                parent[rj] = ri
    reps = {}
    for i in range(len(pts)):
        reps.setdefault(find(i), pts[i])

    out = []
    k = 0
    for ln, m in zip(segments, parsed):
        if not m:
            out.append(ln)
            continue
        x1, y1 = reps[find(2 * k)]
        x2, y2 = reps[find(2 * k + 1)]
        k += 1
        out.append(m.group(1) + f(x1) + " " + f(y1) + m.group(4) +
                   f(x2) + " " + f(y2) + m.group(7))
    vout = []
    for ln, m in zip(vias, via_parsed):
        if not m:
            vout.append(ln)
            continue
        x, y = reps[find(len(pts) - len(vias) + vout.__len__())] if False else None
        vout.append(ln)  # via positions unchanged; welding only affects segments
    return out


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
        layer = LAYER_MAP.get(layer_raw)
        if layer is None:
            continue
        w_mm = float(width) / 1000.0
        nums = [float(x) for x in coords_raw.replace("\n", " ").split()]
        verts = [(nums[i], nums[i + 1]) for i in range(0, len(nums) - 1, 2)]
        if len(verts) >= 3:
            steps = [((b[0] - a[0]) ** 2 + (b[1] - a[1]) ** 2) ** 0.5
                     for a, b in zip(verts, verts[1:])]
            frac_tiny = sum(1 for s in steps if s < 5.0) / len(steps)
        else:
            frac_tiny = 1.0
        if frac_tiny > 0.3:
            # artifact wire (anchor-stub noise): pair form, no chaining
            pairs = [(nums[i], nums[i + 1], nums[i + 2], nums[i + 3])
                     for i in range(0, len(nums) - 3, 4)]
        else:
            # clean route: vertex chain
            pairs = [(v1[0], v1[1], v2[0], v2[1])
                     for v1, v2 in zip(verts, verts[1:])]
        nn = net_nums.get(net, "0")
        nm = net if net else ""
        stats["wires"] += 1
        for x1, y1, x2, y2 in pairs:
            length = ((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5
            if length < 5.0:  # drop <5um noise segments (coords are in um here)
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

    segments = weld_endpoints(segments, vias)
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
