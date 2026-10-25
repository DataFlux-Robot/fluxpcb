# -*- coding: utf-8 -*-
"""Autodesk Fusion (Eagle-style) .scr router output -> KiCad board.

Freerouting 2.4.1's headless writers are broken on BGA-scale boards:
- .ses saves placement-only sessions (no wiring section)
- .dsn write-back saves a 0-byte file
- .scr (Fusion script) saves the FULL routing as text:
    GRID MIC;                       (units: micrometers, y-up flipped)
    WIRE 'NET' 200 (x1 y1) (x2 y2) (x3 y3);     polyline segments
    VIA 'NET' 600 round (x y);
    LAYER n; / CHANGE LAYER n;      1=F.Cu, 2..N-1=In1.., 16=B.Cu

Runs inside KiCad Python: inject_scr(board, scr) adds tracks/vias through
the pcbnew API (no text surgery)."""
import re
import sys

import pcbnew

MM = 1000000


def _layer_id(n, board):
    """Fusion layer number -> KiCad layer id (1=F, 16=B, inner between)."""
    if n == 1:
        return pcbnew.F_Cu
    if n == 16:
        return pcbnew.B_Cu
    return board.GetLayerID(f"In{n - 1}.Cu")


def inject_scr(board, scr_path):
    txt = open(scr_path, encoding="utf-8", errors="replace").read()
    cur_layer = pcbnew.F_Cu
    stats = {"wires": 0, "segs": 0, "vias": 0, "skipped": 0}

    # WIRE 'NET' 200 (x1 y1) (x2 y2) ...;
    for m in re.finditer(
            r"WIRE\s+'([^']+)'\s+([\d.]+)((?:\s+\(?[-\d.]+\s+[-\d.]+\)?)+)\s*;",
            txt):
        net_name, w, pts_s = m.groups()
        net = board.FindNet(net_name)
        if not net:
            stats["skipped"] += 1
            continue
        w_nm = int(float(w) * 1000)          # um -> nm
        pts = re.findall(r"([-\d.]+)\s+([-\d.]+)", pts_s)
        xy = [(int(float(x) * 1000), int(-float(y) * 1000)) for x, y in pts]
        for (x1, y1), (x2, y2) in zip(xy, xy[1:]):
            if abs(x1 - x2) < 1000 and abs(y1 - y2) < 1000:
                continue
            t = pcbnew.PCB_TRACK(board)
            t.SetStart(pcbnew.VECTOR2I(x1, y1))
            t.SetEnd(pcbnew.VECTOR2I(x2, y2))
            t.SetWidth(max(w_nm, int(0.127 * MM)))
            t.SetLayer(cur_layer)
            t.SetNet(net)
            board.Add(t)
            stats["segs"] += 1
        stats["wires"] += 1

    # VIA 'NET' 600 round (x y);
    for m in re.finditer(r"VIA\s+'([^']+)'\s+([\d.]+)\s+\w+\s+"
                         r"\(?([-\d.]+)\s+([-\d.]+)\)?\s*;", txt):
        net_name, vdia, x, y = m.groups()
        net = board.FindNet(net_name)
        if not net:
            stats["skipped"] += 1
            continue
        d_nm = int(float(vdia) * 1000)
        v = pcbnew.PCB_VIA(board)
        v.SetPosition(pcbnew.VECTOR2I(int(float(x) * 1000),
                                      int(-float(y) * 1000)))
        v.SetWidth(max(d_nm, int(0.5 * MM)))
        v.SetDrill(int(0.25 * MM))
        v.SetNet(net)
        board.Add(v)
        stats["vias"] += 1

    # layer context (best effort: scr emits CHANGE LAYER n; before wires —
    # the WIRE regex above unfortunately misses the interleaved layer
    # changes, so we re-scan sequentially below)
    return stats


def inject_scr_ordered(board, scr_path, preserve_set=None):
    """Sequential scan honoring interleaved CHANGE LAYER n; — the accurate
    form: layer context switches between WIRE statements. preserve_set:
    nets whose existing tracks must NOT be removed (they were already
    fully connected by the negotiated router — Y1)."""
    preserve_set = preserve_set or set()
    txt = open(scr_path, encoding="utf-8", errors="replace").read()
    cur_layer = pcbnew.F_Cu
    stats = {"wires": 0, "segs": 0, "vias": 0, "skipped": 0,
             "unknown_layer": 0}

    wire_re = re.compile(
        r"WIRE\s+'([^']+)'\s+([\d.]+)((?:\s+\(?[-\d.]+\s+[-\d.]+\)?)+)\s*;")
    via_re = re.compile(r"VIA\s+'([^']+)'\s+([\d.]+)\s+\w+\s+"
                        r"\(?([-\d.]+)\s+([-\d.]+)\)?\s*;")
    layer_re = re.compile(r"(?:LAYER|CHANGE\s+LAYER)\s+(\d+)\s*;")

    events = []
    for m in wire_re.finditer(txt):
        events.append((m.start(), "w", m))
    for m in via_re.finditer(txt):
        events.append((m.start(), "v", m))
    for m in layer_re.finditer(txt):
        events.append((m.start(), "l", m))
    events.sort(key=lambda e: e[0])

    # FR's scr is a COMPLETE solution for the nets it touched. BUT nets
    # in the preserve set were already fully connected by the negotiated
    # router (fluxrouter/session) — their tracks must survive the .scr
    # injection (Y1: FR replacing successful routes created 144 shorts)
    scr_nets = {m.group(1) for _, k, m in events if k in ("w", "v")}
    scr_nets -= preserve_set
    removed = 0
    for item in list(board.GetTracks()):
        try:
            name = item.GetNetname()
            if name and name in scr_nets:
                board.Remove(item)
                removed += 1
        except AttributeError:
            pass
    stats["replaced"] = removed

    for _, kind, m in events:
        if kind == "l":
            try:
                cur_layer = _layer_id(int(m.group(1)), board)
            except Exception:
                stats["unknown_layer"] += 1
            continue
        if kind == "v":
            net_name, vdia, x, y = m.groups()
            net = board.FindNet(net_name)
            if not net:
                stats["skipped"] += 1
                continue
            v = pcbnew.PCB_VIA(board)
            v.SetPosition(pcbnew.VECTOR2I(int(float(x) * 1000),
                                          int(-float(y) * 1000)))
            v.SetWidth(max(int(float(vdia) * 1000), int(0.5 * MM)))
            v.SetDrill(int(0.25 * MM))
            v.SetNet(net)
            board.Add(v)
            stats["vias"] += 1
            continue
        net_name, w, pts_s = m.groups()
        net = board.FindNet(net_name)
        if not net:
            stats["skipped"] += 1
            continue
        w_nm = max(int(float(w) * 1000), int(0.127 * MM))
        pts = re.findall(r"([-\d.]+)\s+([-\d.]+)", pts_s)
        xy = [(int(float(x) * 1000), int(-float(y) * 1000)) for x, y in pts]
        for (x1, y1), (x2, y2) in zip(xy, xy[1:]):
            if abs(x1 - x2) < 1000 and abs(y1 - y2) < 1000:
                continue
            t = pcbnew.PCB_TRACK(board)
            t.SetStart(pcbnew.VECTOR2I(x1, y1))
            t.SetEnd(pcbnew.VECTOR2I(x2, y2))
            t.SetWidth(w_nm)
            t.SetLayer(cur_layer)
            t.SetNet(net)
            board.Add(t)
            stats["segs"] += 1
        stats["wires"] += 1
    return stats


if __name__ == "__main__":
    pcb, scr = sys.argv[1], sys.argv[2]
    preserve_file = sys.argv[3] if len(sys.argv) > 3 else ""
    b = pcbnew.LoadBoard(pcb)
    preserve_nets = set()
    if preserve_file:
        import json as _j
        import os as _os
        if _os.path.exists(preserve_file):
            try:
                preserve_nets = set(
                    _j.loads(Path(preserve_file).read_text(encoding="utf-8")))
            except (ValueError, OSError):
                pass
    stats = inject_scr_ordered(b, scr, preserve_set=preserve_nets)
    stats["preserved_nets"] = len(preserve_nets)
    b.Save(pcb)
    import json
    print(json.dumps(stats))
