"""Capacity feasibility — geometry first, search second.

At a given pitch the number of traces that fit between two pads is

    n = floor((pitch - pad) / (track + 2 * clearance))

and each signal layer escapes roughly one additional ring of balls.  If
the required rings exceed signal_layers * n, every router converges to
the same residue (P4: 4/4 rollbacks; H7: 0 opens / 9 clearance at
0 traces/channel).  The claim is produced BEFORE routing.
"""
from __future__ import annotations

import math

from .plan import CapacityClaim


def channel_capacity(pitch_mm: float, pad_mm: float,
                     track_mm: float, clearance_mm: float) -> int:
    """Traces that fit between two neighbouring pads."""
    step = track_mm + 2.0 * clearance_mm
    gap = pitch_mm - pad_mm
    if step <= 0 or gap <= 0:
        return 0
    return max(0, int(math.floor(gap / step + 1e-9)))


def ball_rings(positions, pitch_mm: float) -> dict:
    """Ring index per ball around the centroid (0 = innermost)."""
    if not positions or pitch_mm <= 0:
        return {}
    cx = sum(p[0] for p in positions) / len(positions)
    cy = sum(p[1] for p in positions) / len(positions)
    rings = {}
    for (x, y) in positions:
        r = int(math.ceil(max(abs(x - cx), abs(y - cy)) / pitch_mm - 1e-6))
        rings[r] = rings.get(r, 0) + 1
    return rings


def is_area_array(positions, pitch_mm: float) -> bool:
    """True for a 2-D ball grid, not a header / perimeter row.

    The ring model only means anything on an area array: require >=3
    distinct columns and rows and >=60% lattice occupancy.
    """
    # A DIP/header at 2.54 mm is a lattice too, but its "rings" are
    # meaningless.  Area-array conclusions require fine pitch.
    if len(positions) < 9 or pitch_mm <= 0 or pitch_mm > 1.0:
        return False
    cols = {round(x / pitch_mm) for x, _ in positions}
    rows = {round(y / pitch_mm) for _, y in positions}
    if len(cols) < 3 or len(rows) < 3:
        return False
    return len(positions) / float(len(cols) * len(rows)) >= 0.6


def _suggestions(per_channel: int, pitch: float, pad: float, track: float,
                 clear: float, signal: int, rings: int) -> tuple:
    out = []
    if per_channel == 0:
        max_pad = pitch - (track + 2.0 * clear)
        if max_pad > 0:
            out.append(f"shrink pad {pad:.3f} -> <= {max_pad:.3f} mm")
        max_track = (pitch - pad) / 1.0 - 2.0 * clear
        if max_track > 0:
            out.append(f"or use track <= {max_track:.3f} / clearance "
                       f"<= {(pitch - pad - max_track) / 2:0.3f} mm")
        out.append("or HDI / via-in-pad (changes the escape primitive)")
    if signal * max(1, per_channel) < rings:
        out.append(f"need ~{rings} signal layers (have {signal}) or depopulate")
    return tuple(out)


def capacity_claim(board, rules: dict) -> CapacityClaim:
    """Capacity of the board's tightest large footprint.

    Picks the footprint with the most pads (BGA/QFN class); a board with
    no multi-pad footprint returns verdict='unknown' (nothing to prove).
    """
    import pcbnew  # local: pure helpers above stay pcbnew-free

    fps = [fp for fp in board.GetFootprints() if len(list(fp.Pads())) >= 8]
    if not fps:
        return CapacityClaim(verdict="unknown",
                             evidence={"reason": "no multi-pad footprint"})
    def _lattice(fp):
        pads = list(fp.Pads())
        pos = [(p.GetPosition().x / 1e6, p.GetPosition().y / 1e6)
               for p in pads]
        best = float("inf")
        for i in range(len(pos)):
            for j in range(i + 1, len(pos)):
                d = math.hypot(pos[i][0] - pos[j][0],
                               pos[i][1] - pos[j][1])
                if 0.01 < d < best:
                    best = d
        if math.isinf(best):
            return pads, pos, None
        return pads, pos, round(best / 0.05) * 0.05

    # Area arrays only: a header's "rings" are meaningless.  Pick the
    # largest BGA/QFN-class lattice; otherwise there is nothing to prove.
    fp = None
    pads = pos = None
    pitch = None
    for cand in sorted(fps, key=lambda f: len(list(f.Pads())), reverse=True):
        c_pads, c_pos, c_pitch = _lattice(cand)
        if not (c_pitch and is_area_array(c_pos, c_pitch)):
            continue
        # Overlapping/mechanical pads (pad >= pitch) are not a routing
        # array: 0068's pogo plug has 3 mm pads 0.55 mm apart and was
        # wrongly declared infeasible.  An escapable lattice needs a
        # positive gap before the channel formula means anything.
        c_sizes = [max(q.GetSize().x, q.GetSize().y) / 1e6 for q in c_pads]
        if max(c_sizes) >= c_pitch:
            continue
        fp, pads, pos, pitch = cand, c_pads, c_pos, c_pitch
        break
    if fp is None:
        return CapacityClaim(
            verdict="unknown",
            evidence={"reason": "no area-array footprint (BGA/QFN lattice)"})
    sizes = [max(p.GetSize().x, p.GetSize().y) / 1e6 for p in pads]
    drills = []
    for p in pads:
        try:
            d = p.GetDrillSize()
            drills.append(max(d.x, d.y) / 1e6)
        except Exception:
            drills.append(0.0)
    pad_mm = max(sizes)
    track = float(rules.get("min_track_width") or 0.0)
    clear = float(rules.get("min_clearance") or 0.0)
    if track <= 0 or clear <= 0:
        return CapacityClaim(verdict="unknown",
                             evidence={"reason": "rules missing",
                                       "rules": rules})

    rings = ball_rings(pos, pitch)
    max_ring = max(rings) if rings else 0
    required = max_ring + 1

    copper = board.GetCopperLayerCount()
    signal = copper
    if copper > 2:
        try:
            sd = board.GetDesignSettings().GetStackupDescriptor()
            names = [layer.GetLayerName() for layer in sd.GetList()]
            planes = sum(1 for n in names
                         if any(k in n.lower() for k in ("plane", "gnd",
                                                         "pwr", "power")))
            if planes:
                signal = max(1, copper - planes)
        except Exception:
            signal = max(1, copper - 2)

    per_channel = channel_capacity(pitch, pad_mm, track, clear)
    capacity_rings = signal * max(1, per_channel) if per_channel else 0
    margin = capacity_rings - required
    verdict = ("sufficient" if per_channel > 0 and margin >= 0
               else "infeasible")
    evidence = {
        "footprint": fp.GetReference(),
        "pads": len(pads),
        "pitch_mm": pitch,
        "pad_min_max_mm": [round(min(sizes), 3), round(max(sizes), 3)],
        "drill_min_max_mm": [round(min(drills), 3), round(max(drills), 3)],
        "copper_layers": copper,
        "signal_layers": signal,
        "rules": {"track": track, "clearance": clear},
        "pad_gap_mm": round(pitch - pad_mm, 4),
        "rings": dict(sorted(rings.items())),
        "required_rings": required,
        "capacity_rings": capacity_rings,
    }
    return CapacityClaim(
        verdict=verdict,
        traces_per_channel=per_channel,
        rings=required,
        signal_layers=signal,
        margin=margin,
        evidence=evidence,
        suggestions=_suggestions(per_channel, pitch, pad_mm, track, clear,
                                 signal, required),
    )
