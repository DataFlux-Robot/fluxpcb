#!/usr/bin/env python3
"""KiCad-side helpers. Run under a python that imports pcbnew (KiCad 10).

  prepare <upstream.kicad_pcb> <out_dir>
      Load the upstream routed board (KiCad 5 to 10 formats, with its project
      file beside it under the same base name) and save it in KiCad 10 format as
      out_dir/circuit.reference.kicad_pcb with its project. Geometry unchanged.

  strip <reference.kicad_pcb> <out_dir>
      Delete every track and via (pours, placement and rules kept), save
      out_dir/unrouted.kicad_pcb and export out_dir/board.dsn for Specctra
      routers.

  import-ses <unrouted.kicad_pcb> <board.ses> <routed.kicad_pcb>
      Apply a Specctra session onto the unrouted board and save it routed.

  measure <board.kicad_pcb>
      KiCad's ratsnest: unconnected count, via count, track length in mm.

  srj <unrouted.kicad_pcb> <out.json> <track> <via> <drill>
      The board as a tscircuit SimpleRouteJson: every pad, NPTH hole, copper
      graphic and track keepout as a rectangle (KiCad's own bounding box),
      one connection per net with two or more pads. Ground nets are left out:
      the reference boards pour them, and the pours stay on the board.

  apply-traces <unrouted.kicad_pcb> <traces.json> <routed.kicad_pcb> <track> <via> <drill>
      Write a SimpleRouteJson trace list onto the board as KiCad tracks and
      through vias, on the net each trace names, exactly as returned.

Each command prints one line `__RESULT__{json}` and exits.
"""
import json
import os
import sys

import pcbnew


def measure(path):
    b = pcbnew.LoadBoard(path)
    b.BuildConnectivity()
    conn = b.GetConnectivity()
    try:
        unconnected = conn.GetUnconnectedCount(True)
    except TypeError:
        unconnected = conn.GetUnconnectedCount()
    vias, track_mm = 0, 0.0
    via_list = []
    for t in b.GetTracks():
        if t.Type() == pcbnew.PCB_VIA_T:
            vias += 1
            via_list.append(t)
        else:
            track_mm += pcbnew.ToMM(t.GetLength())
    # vias inside a pin pad (an SMD pad under 2 mm across, own net): a board with them needs
    # filled and capped vias at the fab; KiCad's DRC does not flag a via in its own net's pad
    small = [p for fp in b.GetFootprints() for p in fp.Pads()
             if p.GetAttribute() == pcbnew.PAD_ATTRIB_SMD and min(pcbnew.ToMM(p.GetSize().x), pcbnew.ToMM(p.GetSize().y)) < 2.0]
    in_pin_pads = sum(1 for v in via_list if any(p.GetNetCode() == v.GetNetCode() and p.HitTest(v.GetPosition()) for p in small))
    return {"unconnected": int(unconnected), "vias": vias, "trackMm": round(track_mm, 3), "viasInPinPads": in_pin_pads}


def prepare(src, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    b = pcbnew.LoadBoard(src)
    dest = os.path.join(out_dir, "circuit.reference.kicad_pcb")
    pcbnew.SaveBoard(dest, b)
    pro = dest[: -len(".kicad_pcb")] + ".kicad_pro"
    return {"reference": dest, "project": pro if os.path.exists(pro) else None}


def without_copper_zones(text):
    """The board file with every top-level (zone ...) removed except keepout rule areas."""
    out, i, depth, start, in_str = [], 0, 0, -1, False
    copied = 0
    while i < len(text):
        c = text[i]
        if in_str:
            if c == "\\":
                i += 1
            elif c == '"':
                in_str = False
        elif c == '"':
            in_str = True
        elif c == "(":
            if depth == 1 and text.startswith("(zone", i) and text[i + 5] in " \t\r\n)":
                start = i
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 1 and start >= 0:
                if "(keepout" not in text[start:i + 1]:
                    line = text.rfind("\n", 0, start) + 1
                    out.append(text[copied:line])
                    copied = i + 1 + (text[i + 1] == "\n")
                start = -1
        i += 1
    out.append(text[copied:])
    return "".join(out)


def strip(src, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    # placement only: tracks, vias and copper zones go; keepout rule areas are rules and stay.
    # Zones leave at the text level: removing a ZONE through pcbnew breaks its later lookups.
    placed = os.path.join(out_dir, "placed.kicad_pcb")
    with open(src) as f:
        text = without_copper_zones(f.read())
    with open(placed, "w") as f:
        f.write(text)
    b = pcbnew.LoadBoard(placed)
    for t in list(b.GetTracks()):
        b.Remove(t)
    unrouted = os.path.join(out_dir, "unrouted.kicad_pcb")
    dsn = os.path.join(out_dir, "board.dsn")
    pcbnew.SaveBoard(unrouted, b)
    ok = pcbnew.ExportSpecctraDSN(b, dsn)
    return {"ok": bool(ok) and os.path.exists(dsn), "unrouted": unrouted, "dsn": dsn}


def import_ses(unrouted, ses, routed):
    b = pcbnew.LoadBoard(unrouted)
    ok = pcbnew.ImportSpecctraSES(b, ses)
    pcbnew.SaveBoard(routed, b)
    return {"ok": bool(ok), "routed": routed}




def copper_layers(b):
    """KiCad copper layer ids, top to bottom, with their SimpleRouteJson names."""
    n = b.GetCopperLayerCount()
    ids = [pcbnew.F_Cu] + [getattr(pcbnew, f"In{i}_Cu") for i in range(1, n - 1)] + [pcbnew.B_Cu]
    names = ["top"] + [f"inner{i}" for i in range(1, n - 1)] + ["bottom"]
    return list(zip(ids, names))


def rect(bb, layers, nets):
    return {
        "type": "rect",
        "layers": layers,
        "center": {"x": pcbnew.ToMM(bb.GetCenter().x), "y": pcbnew.ToMM(bb.GetCenter().y)},
        "width": max(0.1, pcbnew.ToMM(bb.GetWidth())),
        "height": max(0.1, pcbnew.ToMM(bb.GetHeight())),
        "connectedTo": nets,
    }


def srj(src, out, track, via, drill):
    b = pcbnew.LoadBoard(src)
    layers = copper_layers(b)
    name_of = dict(layers)
    all_names = [n for _, n in layers]
    obstacles, by_net = [], {}
    for fp in b.GetFootprints():
        for pad in fp.Pads():
            on = [name_of[l] for l, _ in layers if pad.IsOnLayer(l)]
            bb = pad.GetBoundingBox()
            if pad.GetAttribute() == pcbnew.PAD_ATTRIB_NPTH:
                d = max(pcbnew.ToMM(bb.GetWidth()), pcbnew.ToMM(bb.GetHeight()), pcbnew.ToMM(pad.GetDrillSizeX()), 0.5)
                c = bb.GetCenter()
                obstacles.append({"type": "rect", "layers": all_names, "center": {"x": pcbnew.ToMM(c.x), "y": pcbnew.ToMM(c.y)},
                                  "width": d, "height": d, "connectedTo": []})
                continue
            if not on:
                continue
            net = pad.GetNetname()
            obstacles.append(rect(bb, on, [net] if net else []))
            if not net:
                continue
            p = pad.GetPosition()
            pt = {"x": pcbnew.ToMM(p.x), "y": pcbnew.ToMM(p.y)}
            if pad.GetAttribute() == pcbnew.PAD_ATTRIB_PTH:
                pt["layers"] = all_names
            else:
                pt["layer"] = on[0]
            by_net.setdefault(net, []).append(pt)
        for g in fp.GraphicalItems():
            if g.GetLayer() in name_of:
                obstacles.append(rect(g.GetBoundingBox(), [name_of[g.GetLayer()]], []))
    for d in b.GetDrawings():
        if d.GetLayer() in name_of:
            obstacles.append(rect(d.GetBoundingBox(), [name_of[d.GetLayer()]], []))
    for z in b.Zones():
        if z.GetIsRuleArea() and z.GetDoNotAllowTracks():
            on = [name_of[l] for l, _ in layers if z.IsOnLayer(l)]
            if on:
                obstacles.append(rect(z.GetBoundingBox(), on, []))
    edge = b.GetBoardEdgesBoundingBox()
    doc = {
        "layerCount": len(layers),
        "minTraceWidth": float(track),
        "minViaDiameter": float(via),
        "minViaHoleDiameter": float(drill),
        "obstacles": obstacles,
        "connections": [{"name": n, "pointsToConnect": pts} for n, pts in by_net.items() if len(pts) >= 2],
        "bounds": {"minX": pcbnew.ToMM(edge.GetLeft()) - 2, "maxX": pcbnew.ToMM(edge.GetRight()) + 2,
                   "minY": pcbnew.ToMM(edge.GetTop()) - 2, "maxY": pcbnew.ToMM(edge.GetBottom()) + 2},
    }
    with open(out, "w") as f:
        json.dump(doc, f)
    return {"connections": len(doc["connections"]), "obstacles": len(obstacles), "layers": len(layers)}


def apply_traces(src, traces_path, out, track, via, drill):
    b = pcbnew.LoadBoard(src)
    layers = copper_layers(b)
    layer_id = {n: l for l, n in layers}
    with open(traces_path) as f:
        traces = json.load(f)
    nets = b.GetNetsByName()
    mm = pcbnew.FromMM
    n_tracks = n_vias = 0
    for tr in traces:
        name = tr.get("connection_name") or ""
        net = nets[name] if nets.has_key(name) else None
        prev = None
        for st in tr.get("route", []):
            if st.get("route_type") == "via":
                v = pcbnew.PCB_VIA(b)
                v.SetPosition(pcbnew.VECTOR2I(mm(st["x"]), mm(st["y"])))
                v.SetViaType(pcbnew.VIATYPE_THROUGH)
                v.SetWidth(mm(float(via)))
                v.SetDrill(mm(float(drill)))
                v.SetLayerPair(pcbnew.F_Cu, pcbnew.B_Cu)
                if net:
                    v.SetNet(net)
                b.Add(v)
                n_vias += 1
                prev = None
                continue
            if st.get("route_type") != "wire":
                continue
            layer = layer_id.get(st.get("layer") or "top", pcbnew.F_Cu)
            if prev and prev[2] == layer and abs(st["x"] - prev[0]) + abs(st["y"] - prev[1]) >= 0.01:
                t = pcbnew.PCB_TRACK(b)
                t.SetStart(pcbnew.VECTOR2I(mm(prev[0]), mm(prev[1])))
                t.SetEnd(pcbnew.VECTOR2I(mm(st["x"]), mm(st["y"])))
                t.SetWidth(mm(float(st.get("width") or track)))
                t.SetLayer(layer)
                if net:
                    t.SetNet(net)
                b.Add(t)
                n_tracks += 1
            prev = (st["x"], st["y"], layer)
    pcbnew.SaveBoard(out, b)
    return {"tracks": n_tracks, "vias": n_vias, "routed": out}


def main():
    cmd, args = sys.argv[1], sys.argv[2:]
    result = {
        "prepare": lambda: prepare(*args),
        "strip": lambda: strip(*args),
        "import-ses": lambda: import_ses(*args),
        "measure": lambda: measure(*args),
        "srj": lambda: srj(*args),
        "apply-traces": lambda: apply_traces(*args),
    }[cmd]()
    # Emit, flush, and leave before pcbnew's interpreter teardown can drop the buffer.
    sys.stdout.write("__RESULT__" + json.dumps(result) + "\n")
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    main()
