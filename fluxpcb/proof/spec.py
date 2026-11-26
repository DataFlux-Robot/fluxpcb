"""Spec -> netlist refinement: the top of the conjecture chain.

The old `NETLIST` claim says "the board's nets equal the given netlist" — a
true statement about a possibly wrong netlist.  This module proves the level
above it: the netlist implements the *intent* (which part sits on which
rail, which rails may not touch, which interfaces must exist).  The chain is

    spec  --(membership/forbidden/parts)-->  netlist  --(NETLIST/CONN)-->  board

Each arrow has its own checker: this one is a domain kernel over refs and
net membership; the layout arrow is the Lean kernel plus fab.  A sink moved
off its rail fails here even when every copper item is perfectly connected
to whatever net it now belongs to.

Spec shape (YAML or dict)::

    power:
      rails:
        - {name: 3V3, net: +3V3, source_ref: U2, sink_refs: [U1, R2, R3]}
    parts: [U1, U2, F1]
    forbidden: [[+5V, +3V3]]
    interfaces: [{name: UART, net: TXD, type: clock}]
    requirements: [{id: R1, text: 2-layer 1.6 mm}]
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path


def _hash(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str)
                          .encode()).hexdigest()[:16]


def _rails(spec):
    power = spec.get('power') or {}
    rails = power.get('rails') if isinstance(power, dict) else None
    return rails or spec.get('rails') or []


def _parts(spec):
    out = []
    for p in spec.get('parts') or []:
        out.append(p['ref'] if isinstance(p, dict) else str(p))
    return out


@dataclass
class Spec:
    id: str
    raw: dict
    rails: list = field(default_factory=list)
    parts: list = field(default_factory=list)
    forbidden: list = field(default_factory=list)
    interfaces: list = field(default_factory=list)
    requirements: list = field(default_factory=list)

    def digest(self):
        return _hash(self.raw)


def load(path_or_dict):
    if isinstance(path_or_dict, (str, Path)):
        p = Path(path_or_dict)
        text = p.read_text(encoding='utf-8')
        try:
            import yaml
            raw = yaml.safe_load(text) or {}
        except ImportError:
            raw = json.loads(text)
        sid = p.stem
    else:
        raw = path_or_dict
        sid = raw.get('id') or 'spec'
    return Spec(id=sid, raw=raw, rails=_rails(raw), parts=_parts(raw),
                forbidden=[list(x) for x in (raw.get('forbidden') or [])],
                interfaces=list(raw.get('interfaces') or []),
                requirements=list(raw.get('requirements') or []))


def obligations(spec, st):
    """The netlist-level checks the spec demands, as explicit obligations."""
    out = []
    fp = st.get('footprints') or {}
    by_id = {i['id']: i for i in st['items']}

    def refs_on(net):
        return {by_id[p]['ref'] for p in st['netPads'].get(net, [])
                if by_id.get(p, {}).get('ref')}

    for ref in spec.parts:
        out.append({'id': f'PART:{ref}', 'kind': 'part', 'ref': ref,
                    'ok': ref in fp,
                    'witness': '' if ref in fp else 'ref is not on the board'})
    for r in spec.rails:
        name = r.get('name') or r.get('net')
        net = r.get('net') or name
        present = net in (st.get('netPads') or {})
        out.append({'id': f'RAIL:{name}:net', 'kind': 'rail', 'net': net,
                    'ok': present,
                    'witness': '' if present else f'net {net} has no pads'})
        if not present:
            continue
        on = refs_on(net)
        src = r.get('source_ref')
        if src:
            out.append({'id': f'RAIL:{name}:source', 'kind': 'rail', 'net': net,
                        'ref': src, 'ok': src in on,
                        'witness': '' if src in on else
                        f'{src} has no pad on {net}'})
        for sink in r.get('sink_refs') or []:
            out.append({'id': f'RAIL:{name}:sink:{sink}', 'kind': 'rail',
                        'net': net, 'ref': sink, 'ok': sink in on,
                        'witness': '' if sink in on else
                        f'{sink} has no pad on {net}'})
    for a, b in spec.forbidden:
        shared = set(st['netPads'].get(a, [])) & set(st['netPads'].get(b, []))
        same = a == b or bool(shared)
        out.append({'id': f'SEP:{a}|{b}', 'kind': 'separate',
                    'nets': [a, b], 'ok': not same,
                    'witness': '' if not same else
                    f'{a} and {b} share pads'})
    for i in spec.interfaces:
        net = i.get('net')
        ok = net in (st.get('netPads') or {})
        out.append({'id': f"IFACE:{i.get('name') or net}", 'kind': 'interface',
                    'net': net, 'type': i.get('type', ''), 'ok': ok,
                    'witness': '' if ok else f'net {net} has no pads'})
    return out


def check(spec, st):
    """Prove the netlist implements the spec; returns a domain-kernel theorem."""
    obl = obligations(spec, st)
    bad = [o for o in obl if not o['ok']]
    theorem = {'id': f'SPEC:{spec.id}', 'kernel': 'spec', 'version': '1',
               'hypotheses': {'spec_digest': spec.digest(),
                              'state_digest': st.get('digest')},
               'claims': [o['id'] for o in obl],
               'conclusion': 'the netlist implements the spec',
               'ok': not bad, 'digest': _hash([spec.digest(), st.get('digest'),
                                               [o['id'] for o in obl]])}
    return {'ok': not bad, 'reasons': [f"{o['id']}: {o['witness']}" for o in bad],
            'obligations': obl, 'theorem': theorem,
            'unformalized': [{'id': r.get('id'), 'text': r.get('text'),
                              'why': r.get('why') or
                              'no formal predicate for this requirement yet'}
                             for r in spec.requirements]}


def refine(spec, st, layout_ok):
    """The three-level block a certificate carries."""
    sp = check(spec, st)
    return {'spec_ok': sp['ok'], 'layout_ok': bool(layout_ok),
            'ok': sp['ok'] and bool(layout_ok),
            'reasons': sp['reasons'],
            'netlist': {'nets': len(st.get('netPads') or {}),
                        'pads': len(st.get('items') or []),
                        'digest': st.get('digest')},
            'theorem': sp['theorem'],
            'obligations': sp['obligations'],
            'unformalized': sp['unformalized']}
