"""Net-name reading: which net is ground, which are rails, what a net is for.

The benchmark boards do not agree on names.  Of 119, 91 have a literal
"GND"; others use GNDPWR, /GND, DGND, /VSS, or nothing at all.  A physical
audit that hardcodes "GND" silently reports "no plane" on 28 boards, so the
name reading lives here, once, with the fallbacks written down.

    detect_ground(copper) -> {'net', 'why', 'candidates', 'n_pads'}
    rail_candidates(copper) -> [{'net', 'pads', 'v_rail', 'why'}]
    parse_voltage('+3V3') -> 3.3 ; parse_voltage('+BATT') -> None
    net_function('SPI_SCK') -> 'clock' ; 'PWM' -> 'pwm' ; 'MISO' -> 'victim'
"""
from __future__ import annotations

import re

GROUND_TOKENS = ('gnd', 'gndpwr', 'dgnd', 'agnd', 'pgnd', 'vss', '0v')
# a net name is a rail candidate when it carries a voltage token or a
# supply word; "Net-(U3-Pad13)" is NOT a rail even though it starts with N
RAIL_WORDS = ('vcc', 'vdd', 'vbat', 'vbus', 'vin', 'vout', 'v+', 'v-',
              'vcc_ext', 'vccin', 'vddio', 'vref')
CLOCK_WORDS = ('clk', 'sck', 'scl', 'sclk', 'clkout', 'mclk', 'sck')
PWM_WORDS = ('pwm', 'phase', 'gate', 'drive')
TX_WORDS = ('tx', 'txd', 'mosi', 'sdo', 'd+', 'dp')
VICTIM_WORDS = ('rx', 'rxd', 'miso', 'sdi', 'd-', 'dm', 'adc', 'ain',
                'sense', 'sin', 'analog', 'vref', 'irq')


def _auto(name: str) -> bool:
    return name.startswith('Net-(') or name.startswith('N-') or name.startswith('unconnected')


def norm(name: str) -> str:
    return (name or '').strip().lower().lstrip('/')


def is_ground_name(name: str) -> bool:
    n = norm(name)
    return bool(n) and any(t in n for t in GROUND_TOKENS) and not _auto(name)


def parse_voltage(name: str):
    """'+3V3' -> 3.3, '-15V' -> -15, '5V' -> 5, '3.3V' -> 3.3, else None."""
    if not name:
        return None
    s = str(name).strip().lstrip('/')
    m = re.match(r'^([+-]?)(\d+(?:\.\d+)?)V(\d+)?$', s, re.I)
    if m:
        sign = -1.0 if m.group(1) == '-' else 1.0
        whole = float(m.group(2))
        frac = m.group(3)
        v = whole + (float('0.' + frac) if frac else 0.0)
        return round(sign * v, 3)
    return None


def is_rail_name(name: str) -> bool:
    if not name or _auto(name) or is_ground_name(name):
        return False
    if parse_voltage(name) is not None:
        return True
    n = norm(name)
    return any(w in n for w in RAIL_WORDS)


def net_function(name: str) -> str:
    n = norm(name)
    if any(w == n or n.endswith(w) or ('_' + w) in n for w in CLOCK_WORDS):
        return 'clock'
    if any(w in n for w in PWM_WORDS):
        return 'pwm'
    if any(w in n for w in TX_WORDS):
        return 'tx'
    if any(w in n for w in VICTIM_WORDS):
        return 'victim'
    return 'signal'


def _pads_of(copper):
    return {nc: len(plist) for nc, plist in copper.pads.items()}


def detect_ground(copper, nets=None):
    """The ground net, with the reason it was picked.

    Order: literal GND -> a ground-ish name (GNDPWR/DGND/AGND/PGND/VSS/0V,
    most pads first) -> the net with the most pads (a plain fallback, said so).
    """
    names = nets or {nc: nm for nc, nm in copper.nets.items() if nm}
    pads = _pads_of(copper)
    named = [(nc, nm) for nc, nm in names.items() if nm]
    exact = [(nc, nm) for nc, nm in named if norm(nm) == 'gnd']
    if exact:
        nc, nm = max(exact, key=lambda t: pads.get(t[0], 0))
        return {'net': nm, 'why': 'literal GND', 'candidates': [nm],
                'n_pads': pads.get(nc, 0)}
    cands = [(nc, nm) for nc, nm in named if is_ground_name(nm)]
    if cands:
        cands.sort(key=lambda t: -pads.get(t[0], 0))
        nc, nm = cands[0]
        return {'net': nm, 'why': 'ground-ish name', 'n_pads': pads.get(nc, 0),
                'candidates': [c[1] for c in cands]}
    pool = [(nc, nm) for nc, nm in named if not is_rail_name(nm)]
    if not pool:
        return {'net': None, 'why': 'no named net to fall back on',
                'candidates': [], 'n_pads': 0}
    pool.sort(key=lambda t: (-pads.get(t[0], 0), len(t[1])))
    nc, nm = pool[0]
    return {'net': nm, 'why': 'fallback: most pads, no ground-named net',
            'n_pads': pads.get(nc, 0), 'candidates': [p[1] for p in pool[:5]]}


def rail_candidates(copper):
    """Named rails, with the voltage only when the name states one."""
    pads = _pads_of(copper)
    out = []
    for nc, nm in copper.nets.items():
        if not nm or not is_rail_name(nm):
            continue
        out.append({'net': nm, 'pads': pads.get(nc, 0),
                    'v_rail': parse_voltage(nm),
                    'why': ('voltage in the name' if parse_voltage(nm) is not None
                            else 'supply word in the name')})
    out.sort(key=lambda r: (-r['pads'], r['net']))
    return out
