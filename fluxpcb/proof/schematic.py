"""Schematic -> PCB netlist refinement: the source of truth, not a hand-written spec.

`proof/spec.py` proves the netlist implements a *hand-written* intent.  This
module goes one level up: the schematic is the intent.  It exports KiCad's own
netlist (pin-level), reads the PCB pads (ref + pad number -> net), and proves
the two partitions agree — without comparing net *names*, because the names
are exactly what a layout is allowed to change.

The obligations are:

  SCH:<net>   every pin of a schematic net exists on the PCB and all of them
              sit on one PCB net (no open, no re-split);
  PCB:<net>   a PCB net does not merge pins from two schematic nets (no short);
  PIN:<r>:<p> a schematic pin has a pad on the board (otherwise it is unplaced);
  COMP:<ref>  every component that appears in a schematic net has a footprint
              on the board.

A PCB net with no schematic pins (an added test point, a mounting pad) is
recorded as an extra, not a failure; a PCB pad with a number the schematic
does not know is recorded per component.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
from collections import defaultdict
from pathlib import Path

from .kicad_text import _child, _head, parse


def _walk(node, name):
    if isinstance(node, list):
        if _head(node) == name:
            yield node
        for c in node:
            yield from _walk(c, name)


def _val(node, name):
    c = _child(node, name)
    return c[1] if c is not None and len(c) > 1 else None


def load_netlist(path):
    """{ref: {value, footprint}} and [{name, nodes:[(ref, pin)]}] from a .net."""
    root = parse(Path(path).read_text(encoding='utf-8', errors='replace'))
    comps = {}
    for c in _walk(root, 'comp'):
        ref = _val(c, 'ref')
        if not ref:
            continue
        fp = _val(c, 'footprint')
        if not fp:
            fields = _child(c, 'fields')
            if fields is not None:
                for f in fields:
                    if isinstance(f, list) and _head(f) == 'field' and                             _val(f, 'name') == 'Footprint':
                        fp = _val(f, 'value')
                        break
        comps[ref] = {'value': _val(c, 'value'), 'footprint': fp or ''}
    nets = []
    for n in _walk(root, 'net'):
        nodes = []
        for x in n:
            if isinstance(x, list) and _head(x) == 'node':
                r, p = _val(x, 'ref'), _val(x, 'pin')
                if r and p:
                    nodes.append((r, p))
        nets.append({'name': _val(n, 'name') or '', 'nodes': nodes})
    return comps, nets


def pcb_pins(board_path):
    """(refs, (ref,pin) -> net or None, ref -> value, ref -> fp name)."""
    import pcbnew
    b = pcbnew.LoadBoard(str(board_path))
    refs, pins, values, fps = set(), {}, {}, {}
    for fp in b.GetFootprints():
        ref = str(fp.GetReference())
        refs.add(ref)
        values[ref] = str(fp.GetValue() or '')
        fps[ref] = str(fp.GetFPID().GetLibItemName() or '')
        for pad in fp.Pads():
            num = str(pad.GetNumber() or '').strip()
            if not num:
                continue
            nm = pad.GetNetname()
            pins[(ref, num)] = nm if nm else None
    return refs, pins, values, fps


def _norm(v):
    return (v or '').strip().lower().replace(' ', '')


def refine_tables(comps, nets, refs, pins, values=None, fps=None,
                  aliases=None):
    """The partition proof; pure, so it is testable without pcbnew.

    `aliases` maps a schematic ref to a board ref when the board is
    unannotated (a `REF**` footprint).  The alias is a *declaration*: it is
    recorded in the theorem and never inferred from geometry.
    """
    obligations = []
    values, fps = values or {}, fps or {}
    aliases = dict(aliases or {})
    active = {r for n in nets for (r, p) in n['nodes']}
    for ref in sorted(active):
        bref = aliases.get(ref, ref)
        ok = bref in refs
        obligations.append({'id': f'COMP:{ref}', 'kind': 'component',
                            'ok': ok,
                            'witness': '' if ok else
                            f'component not on the board (looked for {bref})'})
        if ref in aliases and ok:
            obligations.append({'id': f'ALIAS:{ref}', 'kind': 'alias',
                                'ok': True, 'witness': f'{ref} -> {bref}'})
        if ok:
            sv = (comps.get(ref) or {}).get('value')
            bv, bf = values.get(bref), fps.get(bref)
            if sv and bv and _norm(sv) not in (_norm(bv), _norm(bf)):
                obligations.append({
                    'id': f'VALUE:{ref}', 'kind': 'identity', 'ok': False,
                    'witness': f'schematic value {sv!r} != board value {bv!r} '
                               f'/ fp {bf!r}'})
        if not (comps.get(ref) or {}).get('footprint'):
            obligations.append({
                'id': f'FOOTPRINT:{ref}', 'kind': 'identity', 'ok': False,
                'witness': 'schematic symbol has no footprint assigned'})
    sch_of = {}
    for n in nets:
        for r, p in n['nodes']:
            sch_of[(aliases.get(r, r), p)] = n['name']
    for n in nets:
        nodes = n['nodes']
        if str(n['name']).startswith('unconnected-'):
            # KiCad's declared no-connects: a pin with no required copper
            obligations.append({'id': f"SCH:{n['name']}", 'kind': 'nc',
                                'ok': True, 'witness': 'declared no-connect'})
            continue
        missing = [p for p in nodes
                   if (aliases.get(p[0], p[0]), p[1]) not in pins]
        if missing:
            obligations.append({
                'id': f"SCH:{n['name']}", 'kind': 'schematic', 'ok': False,
                'witness': 'unplaced pins: ' + ','.join(
                    f'{aliases.get(r, r)}.{p}' for r, p in missing[:6])})
            for r, p in missing:
                obligations.append({'id': f'PIN:{r}:{p}', 'kind': 'pin',
                                    'ok': False,
                                    'witness': 'no pad with this number'})
            continue
        if len(nodes) < 2:
            continue                  # a single-pin net says nothing about glue
        present = [p for p in nodes
                   if pins.get((aliases.get(p[0], p[0]), p[1])) is not None]
        pcbnets = {pins[(aliases.get(r, r), p)] for r, p in present}
        ok = len(pcbnets) == 1 and len(present) == len(nodes)
        obligations.append({
            'id': f"SCH:{n['name']}", 'kind': 'schematic', 'ok': ok,
            'witness': '' if ok else
            (f'split over PCB nets {sorted(pcbnets)}' if len(pcbnets) > 1
             else f'{len(nodes) - len(present)} pins on no net')})
    pcb_to_sch = defaultdict(set)
    pcb_members = defaultdict(list)
    for (r, p), net in pins.items():
        if net is None:
            continue
        pcb_members[net].append((r, p))
        if (r, p) in sch_of:
            pcb_to_sch[net].add(sch_of[(r, p)])
    netted = {r for (r, p), net in pins.items() if net is not None}
    alias_targets = set(aliases.values())
    for ref in sorted(refs - set(comps) - alias_targets):
        if ref in netted:
            obligations.append({
                'id': f'EXTRA:{ref}', 'kind': 'identity', 'ok': False,
                'witness': f'unannotated footprint value {values.get(ref)!r} '
                           f'fp {fps.get(ref)!r} carries netted pads'})
    extra_pads = []
    for net, members in sorted(pcb_members.items()):
        schnets = pcb_to_sch.get(net, set())
        extra = [p for p in members if p not in sch_of]
        extra_pads += [(net, r, p) for r, p in extra]
        ok = len(schnets) <= 1
        if len(members) >= 2 or schnets:
            obligations.append({
                'id': f'PCB:{net}', 'kind': 'board', 'ok': ok,
                'witness': '' if ok else
                f'merges schematic nets {sorted(schnets)}'})
    return obligations, extra_pads


def refine(netlist_path, board_path, aliases=None):
    comps, nets = load_netlist(netlist_path)
    refs, pins, values, fps = pcb_pins(board_path)
    aliases = dict(aliases or {})
    obligations, extra_pads = refine_tables(comps, nets, refs, pins,
                                            values, fps, aliases)
    bad = [o for o in obligations if not o['ok']]
    part_bad = [o for o in bad if o['kind'] in ('component', 'schematic',
                                                'pin', 'board')]
    ident_bad = [o for o in bad if o['kind'] == 'identity']
    payload = {'netlist': Path(netlist_path).name,
               'comps': len(comps), 'nets': len(nets),
               'aliases': aliases,
               'claims': [o['id'] for o in obligations],
               'extra_pads': len(extra_pads)}
    dig = hashlib.sha256(json.dumps(payload, sort_keys=True)
                         .encode()).hexdigest()[:16]
    counts = {'components': len(comps), 'schematic_nets': len(nets),
              'pcb_refs': len(refs), 'pcb_pads': len(pins),
              'multi_pin_nets': sum(1 for n in nets if len(n['nodes']) >= 2),
              'failed': len(bad), 'partition_failed': len(part_bad),
              'identity_failed': len(ident_bad),
              'extra_pads': len(extra_pads)}
    return {'ok': not bad,
            'partition_ok': not part_bad, 'identity_ok': not ident_bad,
            'reasons': [f"{o['id']}: {o['witness']}" for o in bad],
            'obligations': obligations, 'extra_pads': extra_pads[:40],
            'counts': counts,
            'theorem': {'id': 'SCH:refinement', 'kernel': 'schematic',
                        'version': '1', 'aliases': aliases,
                        'claims': ['pin partition: schematic == PCB'],
                        'ok': not bad, 'digest': dig}}


def export_netlist(sch_path, out_path=None):
    """kicad-cli sch export netlist -> path (the netlist is the artifact)."""
    from ..routing import find_kicad
    cli, _ = find_kicad()
    if not cli:
        raise RuntimeError('kicad-cli not found')
    out = Path(out_path) if out_path else Path(
        tempfile.mkstemp(suffix='.net')[1])
    p = subprocess.run([cli, 'sch', 'export', 'netlist', '-o', str(out),
                        str(sch_path)], capture_output=True, text=True,
                       encoding='utf-8', errors='replace', timeout=300)
    if p.returncode != 0 or not out.exists():
        raise RuntimeError('netlist export failed: '
                           + (p.stderr or p.stdout or '')[-300:])
    return out
