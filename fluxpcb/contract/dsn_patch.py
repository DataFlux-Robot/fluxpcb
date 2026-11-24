"""dsn_patch — parsed, verified DSN rule transfer.

The previous adapter did two dangerous things:

* ``int(round(mm * 1000))`` — an integer-micrometre channel cannot represent
  0.1524 mm (6 mil = 152.4 um); every such board was routed against a rule
  0.4 um away from the one DRC judges.
* a blind string replacement that (a) silently no-ops when the exporter's
  text differs and (b) can swallow structural parentheses.

Here the rewrite is span-based, every intended edit is asserted, and the
result is re-parsed and diffed against the project's table.  A patch that
does not fire raises instead of pretending to succeed.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .quant import nm_to_um_str, parse_um
from .rules import ClassRule, RuleModel, _is_via_padstack
from .sexpr import (direct_children, find_node, match_pairs, node_name,
                    tokenize, unquote)


class CongruenceError(RuntimeError):
    """Raised when the DSN rule table does not match the project's."""

    def __init__(self, report):
        self.report = report
        super().__init__(report.summary())


@dataclass
class PatchReport:
    ok: bool = False
    classes_written: int = 0
    nets_assigned: int = 0
    default_rule_injected: bool = False
    via_padstacks_patched: int = 0
    text_changed: bool = False
    verified: bool = False
    notes: list = field(default_factory=list)
    diff: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            'ok': self.ok,
            'classes_written': self.classes_written,
            'nets_assigned': self.nets_assigned,
            'default_rule_injected': self.default_rule_injected,
            'via_padstacks_patched': self.via_padstacks_patched,
            'text_changed': self.text_changed,
            'verified': self.verified,
            'notes': self.notes,
            'diff': self.diff,
        }

    def summary(self) -> str:
        if self.ok:
            return (f'DSN rules transferred and verified: {self.nets_assigned} nets, '
                    f'{self.classes_written} classes, '
                    f'default rule {"injected" if self.default_rule_injected else "present"}')
        return (f'DSN rule transfer NOT verified: {self.diff.get("mismatch_count", 0)} '
                f'mismatches; notes={self.notes[:3]}')


def _q(name: str) -> str:
    return f'"{name}"' if re.search(r'[\s()"]', name) else name


def _dsn_net_names(toks, match, network) -> list:
    nets = []
    for ci in direct_children(toks, match, network):
        if node_name(toks, ci) == 'net':
            v = toks[ci + 2][1] if ci + 2 < len(toks) else ''
            nets.append(unquote(v))
    return nets


def _class_text(name, nets, width_nm, clearance_nm, via_name, base=None) -> str:
    head = f'(class {_q(name)}'
    if base:
        head += f' BASE {_q(base)}'
    if nets:
        head += ' ' + ' '.join(_q(n) for n in nets)
    body = [f'    {head}']
    body.append('      (circuit')
    if via_name:
        body.append(f'        (use_via "{via_name}")')
    body.append('      )')
    body.append('      (rule')
    body.append(f'        (width {nm_to_um_str(width_nm)})')
    body.append(f'        (clearance {nm_to_um_str(clearance_nm)})')
    body.append('      )')
    body.append('    )')
    return '\n'.join(body) + '\n    '


def patch_dsn(text: str, model: RuleModel, inject_default: bool = True,
              strict: bool = True):
    """Rewrite the DSN's class rules from ``model`` and verify the result.

    Args:
        text: the DSN as exported by KiCad.
        model: the project's effective rules (``RuleModel.from_project``),
            built with the DSN's net list so patterns resolve.
        inject_default: add a pcb-level ``(rule)`` so objects outside every
            class (pads with no net) do not fall to the router's internal
            default.
        strict: raise ``CongruenceError`` when the read-back diff fails.

    Returns:
        (new_text, PatchReport)
    """
    toks = tokenize(text)
    match = match_pairs(toks)
    pcb = find_node(toks, 'pcb', match=match)
    if pcb is None:
        raise ValueError('not a DSN: no (pcb ...) node')
    network = find_node(toks, 'network', root=pcb, match=match)
    if network is None:
        raise ValueError('DSN has no (network ...) node')

    report = PatchReport()
    # A net can be listed in a (class ...) without a (net ...) declaration
    # when it has no pins.  Dropping it would silently change the table, so
    # the two sources are unioned.
    declared = _dsn_net_names(toks, match, network)
    class_nets = RuleModel.from_dsn(text).all_nets()
    all_nets = sorted(set(declared) | set(class_nets))
    if not all_nets:
        report.notes.append('DSN declares no nets')

    # existing class nodes and the via padstack name they reference
    class_nodes = [c for c in direct_children(toks, match, network)
                   if node_name(toks, c) == 'class']
    via_name = None
    for cn in class_nodes:
        for ch in direct_children(toks, match, cn):
            if node_name(toks, ch) != 'circuit':
                continue
            uv = find_node(toks, 'use_via', root=ch, match=match)
            if uv is not None and uv + 2 < len(toks):
                via_name = unquote(toks[uv + 2][1])
                break
        if via_name:
            break
    if via_name is None:
        padstacks = [toks[i + 2][1] for i, t in enumerate(toks)
                     if t[0] == 'open' and node_name(toks, i) == 'padstack'
                     and i + 2 < len(toks)]
        if padstacks:
            via_name = unquote(padstacks[0])

    # via geometry: class values win, padstacks are the fallback.  The DSN
    # name is a label; copper lives in (shape (circle ...)) and the drill
    # lives ONLY in the name, so both must be retagged consistently.
    max_d = max([c.via_d_nm for c in model.classes.values() if c.via_d_nm] or [0.0])
    max_dr = max([c.via_drill_nm for c in model.classes.values()
                  if c.via_drill_nm] or [0.0])
    if max_d <= 0:
        max_d = model.via_extremes()[0] or 0.0
    if max_dr <= 0:
        max_dr = model.via_extremes()[1] or 0.0
    via_name_out = via_name
    if via_name and (max_d > 0 or max_dr > 0):
        via_name_out = re.sub(
            r'_\d+(?:\.\d+)?:\d+(?:\.\d+)?_um$',
            f'_{nm_to_um_str(max_d)}:{nm_to_um_str(max_dr)}_um', via_name)

    # partition the DSN's nets by the project's classes
    net_set = set(all_nets)
    groups, claimed = {}, set()
    for name, cls in model.classes.items():
        nets = [n for n in cls.nets if n in net_set and n not in claimed]
        if nets:
            groups[name] = nets
            claimed.update(nets)
    leftovers = [n for n in all_nets if n not in claimed]
    default_name = model.default.name
    if leftovers:
        groups.setdefault(default_name, [])
        groups[default_name].extend(leftovers)
        report.notes.append(f'{len(leftovers)} nets unclaimed -> {default_name}')

    # emit all class blocks at the first class node, delete the rest
    blocks = []
    for name, nets in groups.items():
        cls = model.classes.get(name, model.default)
        blocks.append(_class_text(name, nets, cls.width_nm, cls.clearance_nm,
                                  via_name_out))
    if not blocks:
        blocks.append(_class_text(default_name, [], model.default.width_nm,
                                  model.default.clearance_nm, via_name_out))
    new_classes = ''
    if blocks:
        new_classes = blocks[0] + ''.join(
            b.lstrip() for b in blocks[1:])

    edits = []
    if class_nodes:
        start = toks[class_nodes[0]][2]
        end = toks[match[class_nodes[-1]]][3]
        edits.append((start, end, new_classes.rstrip()))
    else:
        close = toks[match[network]][2]
        edits.append((close, close, '\n    ' + new_classes.rstrip() + '\n  '))
    report.classes_written = len(groups)
    report.nets_assigned = sum(len(v) for v in groups.values())

    # pcb-level default rule
    existing_rule = find_node(toks, 'rule', root=pcb, match=match)
    if (existing_rule is None and inject_default
            and model.default.clearance_nm > 0):
        unit = find_node(toks, 'unit', root=pcb, match=match)
        anchor = toks[match[unit]][3] if unit is not None else toks[pcb][3]
        snippet = ('\n  (rule\n'
                   f'    (width {nm_to_um_str(model.default.width_nm)})\n'
                   f'    (clearance {nm_to_um_str(model.default.clearance_nm)})\n'
                   '  )')
        edits.append((anchor, anchor, snippet))
        report.default_rule_injected = True
    elif existing_rule is not None:
        # replace the existing default rule with the project's
        start = toks[existing_rule][2]
        end = toks[match[existing_rule]][3]
        snippet = ('(rule\n'
                   f'    (width {nm_to_um_str(model.default.width_nm)})\n'
                   f'    (clearance {nm_to_um_str(model.default.clearance_nm)})\n'
                   '  )')
        edits.append((start, end, snippet))

    # via padstack geometry: the name is a label; copper lives in the shape
    for i, t in enumerate(toks):
        if t[0] != 'open' or node_name(toks, i) != 'padstack':
            continue
        pname = toks[i + 2][1] if i + 2 < len(toks) else ''
        if not _is_via_padstack(pname):
            continue
        patched = False
        shape = find_node(toks, 'shape', root=i, match=match)
        if shape is not None and max_d > 0:
            for ch in direct_children(toks, match, shape):
                if node_name(toks, ch) != 'circle':
                    continue
                atoms = [x for x in range(ch + 1, match[ch]) if toks[x][0] == 'atom']
                if atoms:
                    a = atoms[-1]
                    edits.append((toks[a][2], toks[a][3], nm_to_um_str(max_d)))
                    patched = True
                break
        drill = find_node(toks, 'drill', root=i, match=match)
        if drill is not None and max_dr > 0:
            atoms = [x for x in range(drill + 1, match[drill]) if toks[x][0] == 'atom']
            if atoms:
                a = atoms[0]
                edits.append((toks[a][2], toks[a][3], nm_to_um_str(max_dr)))
                patched = True
        # KiCad encodes the drill ONLY in the padstack name; keep the label
        # truthful even when there is no (drill ...) node to patch.
        name_tok = i + 2
        if (via_name_out and name_tok < len(toks)
                and toks[name_tok][0] in ('atom', 'string')
                and unquote(toks[name_tok][1]) != via_name_out):
            edits.append((toks[name_tok][2], toks[name_tok][3],
                          f'"{via_name_out}"'))
            patched = True
        if patched:
            report.via_padstacks_patched += 1

    # apply edits back-to-front so earlier offsets stay valid
    new_text = text
    for start, end, repl in sorted(edits, key=lambda e: (e[0], e[1]), reverse=True):
        new_text = new_text[:start] + repl + new_text[end:]
    report.text_changed = new_text != text

    # ---- read-back verification (the patch must prove itself) --------
    try:
        readback = RuleModel.from_dsn(new_text, source='dsn_readback')
        diff = model.diff(readback)
        report.verified = diff.ok
        report.diff = diff.as_dict()
        report.ok = diff.ok
        if diff.ok:
            report.notes.append(diff.summary())
    except Exception as e:  # malformed output is a failure, not a warning
        report.notes.append(f'readback parse failed: {e}')
        report.verified = False
        report.ok = False

    if strict and not report.ok:
        raise CongruenceError(report)
    return new_text, report


def congruence_audit(pro: dict, dsn_text: str) -> dict:
    """Compare a project's effective rules with a DSN, without patching.

    Returns ``{'diff': ..., 'project_model': ..., 'dsn_model': ...}`` —
    the pre-routing verdict that tells you whether the router is even
    solving the same problem as the referee.
    """
    dsn_model = RuleModel.from_dsn(dsn_text)
    project_model = RuleModel.from_project(pro, net_names=sorted(dsn_model.all_nets()))
    diff = project_model.diff(dsn_model)
    return {
        'diff': diff.as_dict(),
        'project_digest': project_model.digest(),
        'dsn_digest': dsn_model.digest(),
        'project_model': project_model,
        'dsn_model': dsn_model,
    }
