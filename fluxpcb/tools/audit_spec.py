"""Derive an audit spec from a board when nobody declared one.

The 0376 audit had a hand-written spec: rails with currents, parts with
power, θ, a heatsink.  A random benchmark board has none of that.  This
module reads what the board itself says — net names, pad counts, track
lengths, footprint refs — and writes a spec whose physics inputs are
explicitly absent:

  * every rail carries `current_a: null`   -> the audit solves at a declared
    probe current and marks the result `estimate`, plus the input-free
    resistance and J-per-amp;
  * every part carries `power_w: null`     -> the thermal section reports
    `unknown` per part instead of inventing a temperature;
  * a source string on every row says "auto: net-name heuristic".

Name reading comes from `netnames` (literal GND, GNDPWR/DGND/VSS, then a
most-pads fallback).  Nothing here is a measurement of the board's
electrical design; it is a set of questions the audit can still answer.

    derive(copper, board_path, features=None) -> spec dict
    write(spec, out_dir) -> path
"""
from __future__ import annotations

import json
import math
import os
import re

from . import netnames

AUTO = 'auto: net-name heuristic'
ASSUMED = 'assumed: the board file carries no stackup, so 1.6 mm FR4 / 1 oz / er=4.4'


def _net_len(copper, name):
    nc = None
    for k, v in copper.nets.items():
        if v == name:
            nc = k
            break
    if nc is None:
        return 0.0, []
    rows = copper.segs.get(nc, [])
    total = sum(math.hypot(x2 - x1, y2 - y1) for x1, y1, x2, y2, _w, _l in rows)
    refs = sorted({p.get('ref') for p in copper.pads.get(nc, []) if p.get('ref')})
    return total, refs


def _ref_pad_counts(copper):
    out = {}
    for plist in copper.pads.values():
        for p in plist:
            r = p.get('ref')
            if r:
                out[r] = out.get(r, 0) + 1
    return out


def _ref_bbox(copper, ref):
    xs, ys = [], []
    for plist in copper.pads.values():
        for p in plist:
            if p.get('ref') == ref:
                xs.append(p['x'])
                ys.append(p['y'])
    if not xs:
        return None
    return (min(xs), min(ys), max(xs), max(ys))


def _cell_mm(area_mm2):
    if area_mm2 and area_mm2 > 8000:
        return 0.3
    if area_mm2 and area_mm2 > 2000:
        return 0.2
    return 0.15


def derive(copper, board_path=None, features=None, probe_a=0.5, probe_w=1.0):
    """A spec the audit can run, with every physical input marked absent."""
    features = features or {}
    area = features.get('area_mm2') or copper.board_area_mm2
    gnd = netnames.detect_ground(copper)
    gnd_net = gnd.get('net')
    vias = len(copper.vias)

    rails = []
    for r in netnames.rail_candidates(copper)[:6]:
        rails.append({'net': r['net'], 'current_a': None,
                      'v_rail': r['v_rail'], 'pads': r['pads'],
                      'source': f'{AUTO}: {r["why"]}; current not declared'})

    signals = []
    cand = []
    skip = {'NC', 'nc', 'no_connect', 'NO_CONNECT', 'unconnected'}
    for nc, nm in copper.nets.items():
        if not nm or nm == gnd_net or nm in skip or netnames.is_rail_name(nm):
            continue
        total, refs = _net_len(copper, nm)
        if len(refs) >= 2 and total > 0:
            cand.append((total, nm, refs))
    cand.sort(key=lambda t: -t[0])
    counts = _ref_pad_counts(copper)
    for total, nm, refs in cand[:2]:
        refs = sorted(refs, key=lambda r: (-counts.get(r, 0), r))
        driver, load = refs[0], refs[-1]
        signals.append({'net': nm, 'name': nm, 'driver': driver, 'load': load,
                        'current_a': None, 'length_mm': round(total, 1),
                        'source': (f'{AUTO}: longest non-rail nets; driver = the '
                                   f'footprint with more pads; current not declared')})

    parts = []
    for ref in sorted(counts):
        if not re.match(r'^[UQD]\d', ref) or counts[ref] < 3:
            continue
        b = _ref_bbox(copper, ref)
        if not b:
            continue
        parts.append({'ref': ref, 'power_w': None,
                      'w_mm': round(b[2] - b[0] + 2.0, 1),
                      'h_mm': round(b[3] - b[1] + 2.0, 1),
                      'theta_ja': 60.0, 'theta_jc': 5.0, 'theta_cs': 0.5,
                      'tjmax_c': 125.0,
                      'source': (f'{AUTO}: {counts[ref]} pads; theta typical, '
                                 f'not datasheet; power not declared')})
    parts = parts[:6]

    emitters = []
    for nc, nm in copper.nets.items():
        if not nm:
            continue
        fn = netnames.net_function(nm)
        if fn in ('clock', 'pwm', 'tx'):
            emitters.append({'net': nm, 'f_hz': None,
                             'amp_db': {'clock': 0.0, 'pwm': -3.0, 'tx': -6.0}[fn],
                             'harmonics': 5,
                             'source': f'{AUTO}: name reads as {fn}; no frequency or level declared'})
    emitters = emitters[:3]
    # if names carry no function, the map still runs on the longest copper
    longest = sorted(((t, nm) for t, nm, _r in cand), reverse=True)[:3]
    emitter_fallback = [{'net': nm, 'f_hz': None, 'amp_db': -10.0,
                         'harmonics': 5,
                         'source': f'{AUTO}: longest nets; function unknown, '
                                   f'level is a declared placeholder'}
                        for _t, nm in longest]
    victims = [{'net': nm, 'source': f'{AUTO}: name reads as a victim'}
               for nc, nm in copper.nets.items()
               if nm and netnames.net_function(nm) == 'victim'][:3]

    missing = ['measured rail currents (this spec has none: the audit solves at '
               f'{probe_a} A and reports the input-free resistance and J per amp)',
               'part power dissipation (thermal stays unknown without it)',
               'the schematic net-to-function mapping (names are heuristics)']
    if gnd_net is None:
        missing.append('a ground net: the plane/return/cavity sections cannot run')
    if vias:
        missing.append('via/barrel current ratings')

    return {
        'board': os.path.basename(board_path) if board_path else '',
        'note': ('Derived from the board file only. Every current and power is '
                 'null: the audit reports geometry, resistance, J per amp, plane '
                 'coverage, modes and coupling as measurements, and names the '
                 'missing inputs.'),
        'gnd_net': gnd_net,
        'ground': gnd,
        'stackup': {'layers': len(copper.layers), 'thickness_mm': 1.6,
                    'copper_mm': 0.035, 'er': 4.4, 'source': ASSUMED},
        'cell_mm': _cell_mm(area),
        'probe_a': probe_a, 'probe_w': probe_w,
        'rails': rails, 'signals': signals, 'parts': parts,
        'emitters': emitters, 'emitter_fallback': emitter_fallback,
        'victims': victims,
        'shield': {'exists': False, 'source': 'auto: no shield net or can detected'},
        'plane_pair': len(copper.layers) > 2 and False,
        'cavity_tol': 0.1,
        'limits': {'j_a_mm2': 45.0, 'drop_pct': 2.0, 'via_a': 1.0,
                   'gnd_mohm': 10.0, 'tj_of_max': 0.9, 'em_db': -20.0},
        'needed_information': missing,
    }


def write(spec, out_dir, name='auto_spec.json'):
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, name)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(spec, f, indent=1, ensure_ascii=False)
    return path
