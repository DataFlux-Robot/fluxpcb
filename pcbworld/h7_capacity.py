#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""H7 capacity check: is the BGA escape feasible at this stackup?

Geometry first, search second.  At a given pitch, the number of traces that
fit between two balls is

    n = floor((pitch - pad_or_antipad) / (trace_width + 2 * clearance))

and each signal layer can escape roughly one additional ball ring.  If the
number of ball rings exceeds signal_layers * n, the configuration is
infeasible and every router/search converges to the same residue.
"""
import json
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, '..', 'flux-neurotransmitter'))

import pcbnew  # noqa: E402

BOARD = os.path.join(ROOT, '..', 'flux-neurotransmitter', 'projects', 'h743-fc',
                     'pcb', 'board', 'h743-fc.kicad_pcb')
PRO = os.path.splitext(BOARD)[0] + '.kicad_pro'


def rules():
    d = json.load(open(PRO, encoding='utf-8'))
    r = d.get('board', {}).get('design_settings', {}).get('rules', {})
    classes = d.get('net_settings', {}).get('classes', []) or []
    clr = max([float(c.get('clearance') or 0) for c in classes] or [0.0])
    return {'min_track': float(r.get('min_track_width') or 0.09),
            'min_clearance': float(r.get('min_clearance') or clr or 0.09),
            'class_clearance': clr,
            'min_hole_to_hole': float(r.get('min_hole_to_hole') or 0.25),
            'min_annular': float(r.get('min_via_annular_width') or 0.1)}


def main():
    b = pcbnew.LoadBoard(BOARD)
    # BGA = footprint with the most pads
    fp = max(b.GetFootprints(), key=lambda f: len(list(f.Pads())))
    pads = [p for p in fp.Pads()]
    pos = [(p.GetPosition().x / 1e6, p.GetPosition().y / 1e6) for p in pads]
    sizes = [max(p.GetSize().x, p.GetSize().y) / 1e6 for p in pads]
    drills = []
    for p in pads:
        try:
            d = p.GetDrillSize()
            drills.append(max(d.x, d.y) / 1e6)
        except Exception:
            drills.append(0.0)
    # pitch: smallest positive center distance, snapped to 50um
    best = 1e9
    for i in range(len(pos)):
        for j in range(i + 1, len(pos)):
            d = math.hypot(pos[i][0] - pos[j][0], pos[i][1] - pos[j][1])
            if 0.01 < d < best:
                best = d
    pitch = round(best / 0.05) * 0.05
    cx = sum(x for x, _ in pos) / len(pos)
    cy = sum(y for _, y in pos) / len(pos)
    rings = {}
    for (x, y) in pos:
        r = int(math.ceil(max(abs(x - cx), abs(y - cy)) / pitch - 1e-6))
        rings[r] = rings.get(r, 0) + 1
    max_ring = max(rings) if rings else 0
    # signal layers: copper layers minus plane estimate from stackup names
    cu = b.GetCopperLayerCount()
    signal = cu
    try:
        sd = b.GetDesignSettings().GetStackupDescriptor()
        names = []
        for layer in sd.GetList():
            names.append(layer.GetLayerName())
        planes = sum(1 for n in names if 'plane' in n.lower() or 'gnd' in n.lower()
                     or 'pwr' in n.lower() or 'power' in n.lower())
        if planes:
            signal = cu - planes
    except Exception:
        signal = max(1, cu - 2)
    r = rules()
    pitch_mm = pitch
    pad_gap = pitch_mm - max(sizes) if sizes else 0
    per_channel = max(0, int(math.floor(
        pad_gap / (r['min_track'] + 2 * r['min_clearance']))))
    capacity_rings = signal * max(1, per_channel)
    out = {
        'board': os.path.basename(BOARD),
        'bga': fp.GetReference(),
        'pads': len(pads),
        'pitch_mm': pitch_mm,
        'pad_size_min_max_mm': [round(min(sizes), 3), round(max(sizes), 3)],
        'drill_min_max_mm': [round(min(drills), 3), round(max(drills), 3)],
        'copper_layers': cu,
        'signal_layers_est': signal,
        'rules': r,
        'pad_gap_mm': round(pad_gap, 3),
        'traces_per_channel': per_channel,
        'rings': dict(sorted(rings.items())),
        'max_ring': max_ring,
        'required_rings': max_ring + 1,
        'capacity_rings': capacity_rings,
        'margin_rings': capacity_rings - (max_ring + 1),
        'verdict': ('capacity_sufficient' if capacity_rings - (max_ring + 1) >= 0
                    else 'capacity_infeasible_at_this_stackup'),
        'notes': ('each signal layer escapes ~1 additional ring; '
                  'if margin < 0, change stackup/via-in-pad/depopulate before routing'),
    }
    json.dump(out, open(os.path.join(HERE, 'h7_capacity.json'), 'w'), indent=1)
    print(json.dumps(out, indent=1))


if __name__ == '__main__':
    main()
