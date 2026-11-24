#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""repair_drc — DRC repair using skill-package operators.

Post-processing script: takes a routed board, runs DRC, applies the right
repair operator per violation morphology, then re-verifies.

This bridges the skill-package operator library into the benchmark pipeline
(which runs fluxpcb_ncer.py directly, not the full 12-component pipeline).

Usage:
    python repair_drc.py <board.kicad_pcb> [--output <fixed.kicad_pcb>]
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys

# Importable both as `fluxpcb.repair_drc` and as a bare script (the
# isolated reroute runs this file directly).  Without this the `--reroute`
# entry point cannot import fluxpcb at all.
_PKG_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PKG_ROOT not in sys.path:
    sys.path.insert(0, _PKG_ROOT)


def run_drc(board_path: str, out_json: str) -> dict:
    """Run kicad-cli DRC and return parsed JSON.

    Fails CLOSED: a missing binary, a timeout, a missing report, or an
    unparseable report returns ``{'ok': False, 'error': ...}`` and removes
    any stale report first.  Callers must refuse to produce claims from a
    non-ok result; a missing report is never "zero violations".
    """
    cli = os.environ.get('KICAD_CLI', 'kicad-cli')
    try:
        if os.path.exists(out_json):
            os.remove(out_json)
    except OSError:
        pass
    try:
        p = subprocess.run(
            [cli, 'pcb', 'drc', '--format', 'json', '--output', out_json,
             '--severity-all', board_path],
            capture_output=True, text=True, timeout=300)
    except Exception as e:
        return {'ok': False, 'error': f'kicad-cli failed to run: {e}',
                'violations': [], 'unconnected_items': []}
    if not os.path.exists(out_json):
        return {'ok': False,
                'error': f'no DRC report (exit {p.returncode}): '
                         f'{(p.stderr or "")[:200]}',
                'violations': [], 'unconnected_items': []}
    try:
        with open(out_json, encoding='utf-8') as f:
            d = json.load(f)
    except Exception as e:
        return {'ok': False, 'error': f'unparseable DRC report: {e}',
                'violations': [], 'unconnected_items': []}
    if not isinstance(d, dict) or 'violations' not in d:
        return {'ok': False, 'error': 'DRC report missing violations key',
                'violations': [], 'unconnected_items': []}
    d['ok'] = True
    d['cli'] = cli
    return d


def save_preserving_project(board, board_path: str):
    """Save a board without letting pcbnew rewrite its paired project.

    pcbnew writes the project's design settings from the in-memory board,
    which after a bare ``LoadBoard`` are KiCad defaults.  The saved
    ``.kicad_pro`` then no longer describes the board's rules, and every
    later DRC -- run in a separate process -- judges against the wrong
    table.  Snapshot and restore the bytes around the save.
    """
    base = os.path.splitext(board_path)[0]
    sidecars = {}
    for path in (base + '.kicad_pro', base + '.kicad_dru'):
        if os.path.exists(path):
            with open(path, 'rb') as f:
                sidecars[path] = f.read()
    board.Save(board_path)
    for path, blob in sidecars.items():
        with open(path, 'wb') as f:
            f.write(blob)


def refill_zones(board_path: str):
    """Refill zones (SKILL.md: 'any autorouter output must be zone-refilled')."""
    import pcbnew
    b = pcbnew.LoadBoard(board_path)
    filler = pcbnew.ZONE_FILLER(b)
    filler.Fill(b.Zones())
    save_preserving_project(b, board_path)


def violating_net_counts(drc_json: str,
                        types=('track_width', 'clearance')) -> dict:
    """Net name -> number of DRC violations of the given types it carries."""
    import re
    with open(drc_json, encoding='utf-8') as f:
        d = json.load(f)
    counts = {}
    for v in d.get('violations') or []:
        if v.get('severity') != 'error' or v.get('excluded'):
            continue
        if v.get('type') not in types:
            continue
        nets = set()
        for it in v.get('items') or []:
            m = re.search(r'\[([^\]]+)\]', it.get('description') or '')
            if m:
                nets.add(m.group(1))
        for n in nets:
            counts[n] = counts.get(n, 0) + 1
    return counts


def violating_nets(drc_json: str, types=('track_width', 'clearance')) -> set:
    """Net names implicated by DRC violations of the given types."""
    return set(violating_net_counts(drc_json, types))


def reroute_nets(board_path: str, nets, timeout_s: int = 240,
                 commit: bool = False, rip: bool = False) -> dict:
    """Rip up ``nets`` and let Freerouting re-route them.

    rip=True is a real partial rip-up: the strip removes only these nets'
    tracks and leaves every other track LOCKED (``(type fix)`` in the DSN),
    so the router solves just these nets against the real obstacle field.

    rip=False is the historical behaviour: the strip removes every track,
    i.e. the whole board is routed again from placement and the net list
    only decides who is reported as "ripped".

    The caller must treat the result as a transaction: evaluate and roll
    back if the hard-claim vector worsens.
    """
    import pcbnew
    import shutil
    from fluxpcb.kicadgen.boardops import discard
    from fluxpcb.router.freerouting import FreeroutingBackend

    nets = set(nets)
    if not nets:
        return {'ok': False, 'reason': 'no nets'}
    backend = FreeroutingBackend(
        threads=int(os.environ.get('FLUXPCB_ROUTER_THREADS', '8') or 8),
        max_passes=int(os.environ.get('FLUXPCB_ROUTER_PASSES', '15') or 15))
    base = os.path.splitext(board_path)[0]
    pro = base + '.kicad_pro'
    b = pcbnew.LoadBoard(board_path)
    if b is None:
        return {'ok': False, 'reason': 'load failed'}
    removed = 0
    if not rip:
        for t in list(b.GetTracks()):
            try:
                if t.GetNetname() in nets:
                    # tracks from the file, never coming back
                    # (kicadgen/boardops.py)
                    discard(b, t)
                    removed += 1
            except Exception:
                continue
    work = base + '.reroute.kicad_pcb'
    save_preserving_project(b, work)
    if os.path.exists(pro):
        shutil.copy(pro, os.path.splitext(work)[0] + '.kicad_pro')
    fr = backend.route(work, work + '.out.kicad_pcb',
                       timeout_s=timeout_s, use_board_rules=True,
                       rip_nets=nets if rip else None)
    if not fr.routed:
        return {'ok': False, 'reason': fr.error,
                'removed': removed or None, 'rip': rip}
    # EXPERIMENTAL: the routed result is returned, not applied, unless the
    # caller opts in.  A caller that commits must wrap this in
    # transactional_repair and evaluate the post-state.
    if commit:
        shutil.copy(work + '.out.kicad_pcb', board_path)
        if (os.path.exists(pro) and os.path.abspath(pro)
                != os.path.abspath(base + '.kicad_pro')):
            shutil.copy(pro, base + '.kicad_pro')
    return {'ok': True, 'nets': sorted(nets), 'removed': removed,
            'seconds': fr.seconds, 'congruent': fr.rule_congruence,
            'kept_locked': fr.kept_locked, 'rip': rip,
            'output': work + '.out.kicad_pcb', 'committed': commit}


def parse_violations(drc: dict) -> list:
    """DRC JSON -> Violation objects, with explicit measurement presence.

    Structured fields win; Chinese and English descriptions are fallbacks.
    A violation without a measured actual/required keeps has_measurement
    False so operators that mint floors skip it instead of using 0.0.
    """
    import re

    from fluxpcb.verifiers.operators import Violation, _measure_from_description

    raw_violations = drc.get('violations', [])
    violations = []
    for v in raw_violations:
        vtype = v.get('type', '')
        items = v.get('items', [])
        desc = v.get('description', '')

        required = actual = 0.0
        req_ok = act_ok = False
        for key in ('required_mm', 'required'):
            if v.get(key) is not None:
                try:
                    required, req_ok = float(v[key]), True
                    break
                except (TypeError, ValueError):
                    pass
        for key in ('actual_mm', 'actual'):
            if v.get(key) is not None:
                try:
                    actual, act_ok = float(v[key]), True
                    break
                except (TypeError, ValueError):
                    pass
        if not (req_ok and act_ok):
            # The text says it in either language ("Clearance 0.2000 mm;
            # actual 0.1795 mm", "最小宽度 0.2000 mm; 实际 0.1500 mm").  A
            # width finding used to leave its minimum unread.
            req_txt, act_txt = _measure_from_description(desc)
            if not req_ok and req_txt is not None:
                required, req_ok = req_txt, True
            if not act_ok and act_txt is not None:
                actual, act_ok = act_txt, True

        net = ''
        net2 = ''
        uuid = ''
        pos = (0.0, 0.0)
        items_out = []
        for item in items:
            idesc = item.get('description', '')
            m = re.search(r'\[([^\]]+)\]', idesc)
            if m:
                if not net:
                    net = m.group(1)
                elif not net2:
                    net2 = m.group(1)
            items_out.append({'description': idesc,
                              'pos': item.get('pos'),
                              'uuid': item.get('uuid', '')})
            if vtype == 'track_dangling' and not uuid:
                uuid = item.get('uuid', '')
                p = item.get('pos', {})
                pos = (p.get('x', 0.0), p.get('y', 0.0))
            # A clearance/width pair names its copper; keep a track uuid so
            # a geometric operator can move the segment, not just the net.
            low = idesc.lower()
            if (vtype in ('clearance', 'track_width', 'shorting_items')
                    and not uuid and item.get('uuid')
                    and ('track' in low or '走线' in low or '线段' in low)):
                uuid = item.get('uuid', '')

        violations.append(Violation(
            type=vtype, net=net, net2=net2,
            actual_mm=actual, required_mm=required,
            location=pos, track_uuid=uuid, items=items_out,
            has_measurement=bool(req_ok and act_ok),
        ))

    # Opens are first-class violations: the repair loop cannot close what it
    # never sees.  Keep the item list so a connectivity operator can use the
    # two anchors directly.
    for u in drc.get('unconnected_items') or []:
        if u.get('excluded'):
            continue
        desc = u.get('description') or ''
        m = re.search(r'\[([^\]]+)\]', desc)
        net = m.group(1) if m else ''
        if not net:
            for it in (u.get('items') or []):
                m2 = re.search(r'\[([^\]]+)\]', it.get('description') or '')
                if m2:
                    net = m2.group(1)
                    break
        pos = u.get('pos') or {}
        violations.append(Violation(
            type='unconnected', net=net,
            location=(pos.get('x', 0.0), pos.get('y', 0.0)),
            items=u.get('items') or [u],
            has_measurement=False,
        ))
    return violations


REROUTE_TYPES = ('track_width', 'clearance')
RIP_TYPES = ('shorting_items', 'track_width', 'clearance')


def reroute_residues(board_path: str, drc_json: str,
                     timeout_s: int = 240, types=REROUTE_TYPES,
                     rip: bool = False, max_nets: int = 0) -> dict:
    """Rip up the nets implicated by violations and re-route them.

    The caller must run this inside a transaction: the output is copied
    over the board, then evaluated and rolled back if the hard-claim vector
    does not improve.  ``max_nets`` bounds a partial rip-up to the nets
    that carry the most violations.
    """
    import hashlib
    counts = violating_net_counts(drc_json, types=types)
    nets = set(counts)
    if not nets:
        return {'operators_run': [], 'reason': 'no nets with these violations'}
    capped = False
    if max_nets and len(nets) > max_nets:
        ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
        nets = {n for n, _ in ranked[:max_nets]}
        capped = True
    info = reroute_nets(board_path, nets, timeout_s=timeout_s,
                        commit=False, rip=rip)
    if not info.get('ok'):
        return {'operators_run': [], 'error': info.get('reason'),
                'nets': sorted(nets), 'rip': rip}
    shutil.copy(info['output'], board_path)
    h = hashlib.sha256(open(board_path, 'rb').read()).hexdigest()
    return {'operators_run': ['reroute_violating_nets'],
            'nets': sorted(nets), 'removed': info.get('removed'),
            'kept_locked': info.get('kept_locked'),
            'capped': capped, 'rip': rip,
            'seconds': info.get('seconds'),
            'congruent': info.get('congruent'),
            'board_sha': h[:16]}


def reroute_residues_isolated(board_path: str, drc_json: str,
                              timeout_s: int = 240, types=REROUTE_TYPES,
                              rip: bool = False,
                              max_nets: int = 0) -> dict:
    """Run reroute_residues in a fresh interpreter.

    pcbnew's SWIG wrappers become invalid after a net-level rip-up in the
    same process; isolating the reroute keeps the caller's board state
    usable for the post-action evaluation.
    """
    cmd = [sys.executable, os.path.abspath(__file__), '--reroute',
           board_path, drc_json, str(timeout_s),
           '--types', ','.join(types),
           '--rip' if rip else '--full',
           '--max-nets', str(max_nets)]
    r = subprocess.run(cmd, capture_output=True, text=True,
                       timeout=timeout_s + 600)
    for line in (r.stdout or '').splitlines():
        if line.startswith('__REROUTE__'):
            return json.loads(line[len('__REROUTE__'):])
    return {'operators_run': [], 'error': (r.stderr or '')[-200:]}


# Loop mode 'morph': one violation morphology per transaction round, in
# this order.  Only classes that have an available operator are listed.
MORPH_ORDER = ('shorting_items', 'unconnected', 'track_dangling',
               'clearance', 'track_width')


def count_class(drc_json: str, vtype: str) -> int:
    """How many violations of one class the latest DRC report holds."""
    with open(drc_json, encoding='utf-8') as f:
        d = json.load(f)
    if vtype == 'unconnected':
        return sum(1 for u in d.get('unconnected_items') or []
                   if not u.get('excluded'))
    return sum(1 for v in d.get('violations') or []
               if v.get('type') == vtype and v.get('severity') == 'error'
               and not v.get('excluded'))


class MorphPicker:
    """Chooses the morphology for the next transaction round.

    Walks MORPH_ORDER once per sweep, so a class whose repair keeps being
    rolled back cannot monopolise the rounds; the next sweep revisits it
    after other classes have had their turn.
    """

    def __init__(self):
        self.tried = []

    def pick(self, drc_json: str):
        for vtype in MORPH_ORDER:
            if vtype in self.tried:
                continue
            if count_class(drc_json, vtype) > 0:
                self.tried.append(vtype)
                return vtype
        self.tried = []
        return None


def no_assert_dialogs():
    """A GUI assertion dialog is the one failure a headless run cannot
    recover from: the process waits for a click that never comes.  KiCad
    10 raised one from PCB_VIA.GetWidth() called without a layer, and
    every cell that had a via ran to its wall-clock budget with nothing
    on record.  Suppressed, the call returns and the DRC judges the
    result.  The right call is still the right call — this is the net.
    """
    try:
        import wx
        wx.DisableAsserts()
    except Exception:
        pass


def repair_with_operators(board_path: str, drc_json: str,
                          allowed_ops=None, classes=None, params=None,
                          only=None) -> dict:
    """Apply skill-package repair operators to DRC violations.

    `allowed_ops` is the policy: operators outside it are never selected.
    `classes` restricts the pass to given violation types (None = all).
    `only` runs that one registry entry and nothing else.

    Returns a summary of what was repaired, including the operator ledger.
    """
    # Ensure fluxpcb is importable regardless of how the script is invoked
    _here = os.path.dirname(os.path.abspath(__file__))
    _root = os.path.dirname(_here)
    if _root not in sys.path:
        sys.path.insert(0, _root)

    from fluxpcb.verifiers.operators import RepairOperators, Violation

    with open(drc_json, encoding='utf-8') as f:
        drc = json.load(f)

    if drc.get('ok') is False:
        # A failed measurement must not be repaired as if it were clean.
        return {'error': drc.get('error', 'DRC report not ok'),
                'operators_run': [], 'repaired': 0, 'failed': 0, 'ledger': []}

    violations = parse_violations(drc)

    ops = RepairOperators(params=params)
    no_assert_dialogs()
    import pcbnew
    board = pcbnew.LoadBoard(board_path)

    results = ops.dispatch_repair(board, violations, allowed=allowed_ops,
                                  classes=classes, only=only)

    # Save repaired board (project bytes preserved: DRC must keep judging
    # against the board's own rules)
    save_preserving_project(board, board_path)

    return {
        'operators_run': [r.operator for r in results],
        'repaired': sum(r.repaired_count for r in results),
        'failed': sum(r.failed_count for r in results),
        'ledger': ops.ledger,
    }


def _digest(path):
    import hashlib

    with open(path, 'rb') as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def repair_stepwise(board_path: str, drc_json: str, evaluate,
                    allowed_ops=None, classes=None, params=None,
                    kind: str = 'repair', rule_congruent: bool = True) -> dict:
    """One repair round as a chain of small transactions, one per operator.

    A round used to be one transaction: every operator's copper went in
    together and the gate judged the sum.  One operator that brought in a
    violation class the board did not have threw away every other
    operator's repair with it (0496: 67 repaired, rolled back for
    'tracks_crossing'; 0599: 45, the same way).  Here each operator is
    judged on its own against the board as the previous ones left it:
    the ones that pass keep their copper, the one that fails gets its
    own copper back where it was and is on record by name.

    `evaluate(board_path) -> Claims` is the caller's (it refills zones
    and runs the DRC into `drc_json`).  Returns what repair_with_operators
    returns — operators run, repaired, failed, the ledger — plus
    `steps`: one entry per operator with its decision.  When no operator
    had anything to do the result is `{'no_op': True}`.
    """
    from fluxpcb.verifiers.operator_spec import TEXT_TO_SILK, default_registry
    from fluxpcb.verifiers.txn import claims_from_drc, transactional_repair

    def report():
        with open(drc_json, encoding='utf-8') as f:
            return json.load(f)

    def ok(name):
        return allowed_ops is None or name in allowed_ops

    def wanted(vtype):
        return classes is None or vtype in classes

    def has(classes_):
        d = report()
        if d.get('ok') is False:
            return False
        return any(v.get('severity') == 'error' and not v.get('excluded')
                   and v.get('type') in classes_ for v in d.get('violations') or [])

    if not os.path.exists(drc_json):
        evaluate(board_path)              # no report yet: make one
    entries = []
    if ok(TEXT_TO_SILK) and wanted('clearance') and has({'clearance'}):
        entries.append((TEXT_TO_SILK, (TEXT_TO_SILK,), {'clearance'}))
    for spec in default_registry().ordered():
        if (spec.available and any(wanted(c) for c in spec.classes)
                and all(ok(n) for n in spec.emitted()) and has(set(spec.classes))):
            entries.append((spec.name, spec.emitted(), set(spec.classes)))

    out = {'operators_run': [], 'repaired': 0, 'failed': 0, 'ledger': [], 'steps': []}
    first = report()
    claims = claims_from_drc(first, rule_congruent=rule_congruent)
    for (name, emitted, classes_) in entries:
        def one(path, _emitted=emitted, _name=name):
            was = _digest(path)
            stats = repair_with_operators(path, drc_json, allowed_ops=_emitted,
                                          classes=classes, params=params,
                                          only=_name)
            # an operator that changed no byte is not judged: there is
            # nothing on the board to roll back, and a DRC costs more
            # than the answer is worth
            if _digest(path) == was:
                stats['no_op'] = True
            return stats

        step = transactional_repair(board_path, one, evaluate, max_rounds=1,
                                    initial=claims, kind=f'{kind}:{name}')
        acted = [e for e in step.log if isinstance(e, dict)]
        stats = (acted[0].get('action') or {}) if acted else {}
        entry = {'operator': name, 'stage': kind}
        if step.committed:
            entry['decision'] = 'commit'
        elif step.rolled_back:
            entry['decision'] = 'rollback'
        elif step.errors:
            entry['decision'] = 'action_error'
        else:
            continue                      # nothing was left for it to do
        entry.update(why=(acted[0].get('why') if acted else None),
                     before=(acted[0].get('before') if acted else {}),
                     after=(acted[0].get('after') if acted else {}),
                     repaired=stats.get('repaired'), failed=stats.get('failed'))
        out['steps'].append(entry)
        out['operators_run'].extend(stats.get('operators_run') or [])
        out['repaired'] += stats.get('repaired') or 0
        out['failed'] += stats.get('failed') or 0
        out['ledger'].extend(stats.get('ledger') or [])
        if step.committed:
            claims = claims_from_drc(report(), rule_congruent=rule_congruent)
    if not out['steps']:
        return {'no_op': True, 'operators_run': [], 'steps': []}
    return out


def repair_rounds(board_path: str, drc_json: str, evaluate, rounds: int = 3,
                  allowed_ops=None, classes=None, params=None, initial=None,
                  hook=None, rule_congruent: bool = True,
                  kind: str = 'repair', stepwise: bool = True):
    """The repair stage of route-and-repair: transactional rounds of
    operator passes until the board is clean, a round does not improve
    it, or `rounds` is used.  What ``flux run`` and the benchmark both
    call; the bench script only hands it its own ``evaluate``.

    Returns the transaction report (committed / rolled back rounds, each
    with the operators' statistics and ledger).
    """
    from fluxpcb.verifiers.txn import transactional_repair

    def action(path):
        picked = list(classes) if classes is not None else None
        if not stepwise:
            return repair_with_operators(path, drc_json, allowed_ops=allowed_ops,
                                         classes=picked, params=params)
        return repair_stepwise(path, drc_json, evaluate, allowed_ops=allowed_ops,
                               classes=picked, params=params, kind=kind,
                               rule_congruent=rule_congruent)

    return transactional_repair(board_path, action, evaluate, max_rounds=rounds,
                                initial=initial, hook=hook, kind=kind,
                                stop_on_rollback=True)


# what the DRC calls a track or via nothing is connected to at one end
DANGLING = ('track_dangling', 'via_dangling')


def dangling_uuids(drc: dict) -> list:
    """Item uuids the DRC reports as unconnected stubs, whatever its
    severity for them (a stub is a warning, so the error criterion does
    not see it)."""
    out = set()
    for v in (drc or {}).get('violations') or []:
        if v.get('type') in DANGLING and not v.get('excluded'):
            out |= {str(i.get('uuid')) for i in v.get('items') or []
                    if i.get('uuid')}
    return sorted(out)


def dangling_counts(drc: dict) -> dict:
    return {k: sum(1 for v in (drc or {}).get('violations') or []
                   if v.get('type') == k and not v.get('excluded'))
            for k in DANGLING}


def remove_items(board_path: str, uuids) -> int:
    """Take items off the board file by uuid.  Returns how many went."""
    import pcbnew

    from fluxpcb.kicadgen.boardops import discard

    b = pcbnew.LoadBoard(board_path)
    want, gone = set(uuids), 0
    for t in list(b.GetTracks()):
        if t.m_Uuid.AsString() in want:
            discard(b, t)
            gone += 1
    save_preserving_project(b, board_path)
    return gone


def trim_dangling(board_path: str, drc_json: str, evaluate, initial=None,
                  rounds: int = 8, restore_project=None) -> dict:
    """Take dangling stubs off the board: leaves only, each round
    confirmed by the DRC.  One more error or one more open puts the
    stubs back.

    6 of the 110 boards that passed the first full run carried a stub;
    a referee that counts stubs (the strict criterion) failed them.
    Taking one stub off can expose the next (a chain of them): 0077
    needed four rounds, and three left one on.

    Returns the stage record: removed, rounds, committed, rolled_back
    and a log of the rounds.
    """
    import shutil

    from fluxpcb.verifiers.txn import claims_from_drc

    trim = {'stage': 'trim', 'mode': 'leaves', 'committed': 0, 'rolled_back': 0,
            'denied': 0, 'errors': 0, 'log': [], 'removed': 0, 'rounds': 0}

    def read():
        with open(drc_json, encoding='utf-8') as f:
            return json.load(f)

    before = initial
    for _ in range(rounds):
        stubs = dangling_uuids(read())
        if not stubs:
            break
        trim['rounds'] += 1
        backup = board_path + '.before-trim'
        shutil.copyfile(board_path, backup)
        gone = remove_items(board_path, stubs)
        if restore_project is not None:
            restore_project()
        try:
            after = evaluate(board_path)
        except Exception:
            after = None
        if before is None:
            before = claims_from_drc(read())
        if after is not None and after.total <= before.total:
            before = after
            trim['removed'] += gone
            trim['committed'] += 1
            trim['log'].append({'round': trim['rounds'], 'decision': 'commit',
                                'removed': gone})
            os.remove(backup)
        else:
            shutil.copyfile(backup, board_path)
            os.remove(backup)
            if restore_project is not None:
                restore_project()
            before = evaluate(board_path)      # the report goes with the board
            trim['rolled_back'] += 1
            trim['log'].append({'round': trim['rounds'], 'decision': 'rollback',
                                'why': ('DRC failed' if after is None else
                                        f'total {before.total} -> {after.total}')})
            break
    return trim


def main():
    """CLI: refill -> transactional repair -> report.

    Uses the same transaction gate as the benchmark; a repair batch that
    does not strictly improve the hard-claim vector is rolled back.
    """
    if len(sys.argv) < 2:
        print('Usage: python repair_drc.py <board.kicad_pcb> [--output <fixed>]')
        sys.exit(1)

    board_path = sys.argv[1]
    output_path = board_path
    if '--output' in sys.argv:
        output_path = sys.argv[sys.argv.index('--output') + 1]

    from fluxpcb.verifiers.txn import claims_from_drc, transactional_repair

    print('=== DRC Repair Pipeline (transactional) ===')
    print(f'Board: {board_path}')
    print()

    def evaluate(path):
        refill_zones(path)
        dj = path + '.drc_live.json'
        d = run_drc(path, dj)
        if d.get('ok') is False:
            raise RuntimeError(d.get('error', 'DRC failed'))
        holder['drc'] = dj
        return claims_from_drc(d, True)

    holder = {'drc': None}

    def action(path):
        if not holder['drc']:
            return {}
        return repair_with_operators(path, holder['drc'])

    before = evaluate(board_path)
    print(f"Baseline: {before.n_errors} violations, "
          f"{before.n_unconnected} unconnected")
    rep = transactional_repair(board_path, action, evaluate,
                               max_rounds=3, initial=before)
    after = evaluate(board_path)
    print(f'Transactions: {rep.committed} committed, '
          f'{rep.rolled_back} rolled back')
    print(f"Final: {after.n_errors} violations, {after.n_unconnected} unconnected")

    if output_path != board_path:
        import shutil
        shutil.copy2(board_path, output_path)


if __name__ == '__main__':
    if len(sys.argv) >= 2 and sys.argv[1] == '--reroute':
        _b = sys.argv[2] if len(sys.argv) > 2 else ''
        _d = sys.argv[3] if len(sys.argv) > 3 else ''
        _t = int(sys.argv[4]) if len(sys.argv) > 4 else 240
        _types, _max = REROUTE_TYPES, 0
        rest = sys.argv[5:]
        for i in range(0, len(rest) - 1, 2):
            if rest[i] == '--types':
                _types = tuple(t for t in rest[i + 1].split(',') if t)
            elif rest[i] == '--max-nets':
                _max = int(rest[i + 1])
        _rip = '--rip' in rest
        no_assert_dialogs()
        _out = reroute_residues(_b, _d, _t, types=_types, rip=_rip,
                                max_nets=_max)
        # This interpreter is the other half of a cell: report the project
        # code it loaded, so the cell's fingerprint covers it — the parent
        # process cannot see another interpreter's sys.modules.
        try:
            from fluxpcb.meta import codefp as _cf
            _out['loaded'] = _cf.loaded_project_files()
        except Exception as _e:                      # evidence, not a result
            _out['loaded_error'] = f'{type(_e).__name__}: {_e}'
        print('__REROUTE__' + json.dumps(_out), flush=True)
    else:
        main()
