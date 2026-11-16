"""Per board and tool: how the copper was made. Vias by size, vias inside SMD pads, track widths,
the ground net's routed track length, copper zones (pours/planes) and their nets.
    KICAD_PYTHON scripts/habits.py out.json <tool>=<board.kicad_pcb> ..."""
import json
import sys
from collections import Counter

import pcbnew

mm = pcbnew.ToMM


def stats(path):
    b = pcbnew.LoadBoard(path)
    vias = [t for t in b.GetTracks() if isinstance(t, pcbnew.PCB_VIA)]
    tracks = [t for t in b.GetTracks() if not isinstance(t, pcbnew.PCB_VIA)]
    smd = [p for fp in b.GetFootprints() for p in fp.Pads() if p.GetAttribute() == pcbnew.PAD_ATTRIB_SMD]
    in_pad = in_small = 0
    for v in vias:
        pos = v.GetPosition()
        for p in smd:
            if p.GetNetCode() == v.GetNetCode() and p.HitTest(pos):
                in_pad += 1
                # a small pad (under 2 mm across): a pin's pad, not a thermal paddle, where a via
                # needs filling and capping (POFV) or it wicks the joint's solder
                if min(mm(p.GetSize().x), mm(p.GetSize().y)) < 2.0:
                    in_small += 1
                break
    sizes = Counter(f"{mm(v.GetWidth(pcbnew.F_Cu)):.2f}/{mm(v.GetDrillValue()):.2f}" for v in vias)
    widths = Counter(f"{mm(t.GetWidth()):.3f}" for t in tracks)
    gnd = sum(mm(t.GetLength()) for t in tracks if t.GetNetname().upper() in ("GND", "/GND", "GNDD", "DGND"))
    zones = [z for z in b.Zones() if not z.GetIsRuleArea()]
    return {
        "vias": len(vias),
        "via_sizes": dict(sizes.most_common(4)),
        "vias_in_smd_pads": in_pad,
        "vias_in_small_smd_pads": in_small,
        "tracks": len(tracks),
        "track_mm": round(sum(mm(t.GetLength()) for t in tracks)),
        "widths": dict(widths.most_common(4)),
        "gnd_track_mm": round(gnd),
        "zones": len(zones),
        "zone_nets": sorted({z.GetNetname() for z in zones}),
    }


out = {}
for arg in sys.argv[2:]:
    key, path = arg.split("=", 1)
    try:
        out[key] = stats(path)
    except Exception as e:  # a missing or unreadable board
        out[key] = {"error": str(e)[:200]}
    print(key, json.dumps(out[key])[:300], flush=True)
json.dump(out, open(sys.argv[1], "w"), indent=1)
