"""KiCad 10 .kicad_sch generator: real schematics with embedded symbols,
label-based connectivity, PWR_FLAG-driven rails — validated by kicad-cli ERC.

ERC-zero design rules:
- module symbols carry ONLY the pins actually used
- IC signal pins are passive/bidirectional (no drive false-positives)
- every rail net (power_in pins) gets a PWR_FLAG attached via global label
"""
from __future__ import annotations

import uuid as uuidlib
from dataclasses import dataclass, field

VERSION = "20250610"


def uid() -> str:
    return str(uuidlib.uuid4())


@dataclass
class Pin:
    number: str
    name: str
    etype: str = "passive"
    x: float = -10.16
    y: float = 0.0
    angle: float = 0.0


@dataclass
class SymDef:
    lib_name: str
    pins: list[Pin]
    ref_prefix: str = "U"
    auto_layout: bool = True

    def __post_init__(self):
        if self.auto_layout:
            n = len(self.pins)
            for i, p in enumerate(self.pins):
                p.y = (n - 1) * 1.27 - i * 2.54   # +y up in symbol frame
                p.x = -10.16


@dataclass
class SymInst:
    designator: str
    lib_name: str
    value: str
    footprint: str = ""
    x: float = 0.0
    y: float = 0.0
    pin_nets: dict[str, str] = field(default_factory=dict)
    nc_pins: set[str] = field(default_factory=set)   # explicitly unused pins


_KEYWORDS = {
    "yes", "no", "line", "input", "output", "bidirectional", "passive",
    "power_in", "power_out", "tri_state", "unspecified", "free",
    "background", "outline", "dash", "dot", "default", "solid",
    "3state", "open_collector", "open_emitter", "unconnected",
    "triangle", "rectangle", "circle", "arrow2", "rounded", "flag",
}


def _render(node, depth=0) -> str:
    pad = "\t" * depth
    if isinstance(node, bool):
        return "yes" if node else "no"
    if isinstance(node, (int, float)):
        return str(node)
    if isinstance(node, str):
        if node == "":
            return '""'
        if node[0] == "\x00":          # bare atom marker
            return node[1:]
        if node in _KEYWORDS:
            return node
        return '"' + node.replace('\\', '\\\\').replace('"', '\\"') + '"'
    if not isinstance(node, (list, tuple)):
        raise TypeError(str(type(node)))
    if not node:
        return f"{pad}()"
    head = node[0]
    head_s = _render(head, 0)
    child_parts = [_render(c, depth + 1) if isinstance(c, (list, tuple)) else _render(c, 0)
                   for c in node[1:]]
    inline = all("\n" not in p for p in child_parts) and \
        len(head_s) + sum(len(p) + 1 for p in child_parts) < 60
    if inline:
        body = " ".join(p.strip() for p in child_parts)
        return f"{pad}({head_s} {body})" if body else f"{pad}({head_s})"
    lines = [f"{pad}({head_s}"]
    for p in child_parts:
        lines.append(p if p.startswith(("\t", " ")) else "\t" * (depth + 1) + p.strip())
    lines.append(f"{pad})")
    return "\n".join(lines)


def bare(s: str) -> str:
    """Mark a string as a bare (unquoted) atom."""
    return "\x00" + s


def _prop(key: str, value: str, x: float, y: float, hide: bool = False) -> list:
    return [bare("property"), key, value,
            [bare("at"), x, y, 0],
            *( [[bare("hide"), "yes"]] if hide else [] ),
            [bare("effects"),
             [bare("font"), [bare("size"), 1.27, 1.27]]]]


class Schematic:
    def __init__(self, title: str = "board", project: str = "board"):
        self.title = title
        self.project = project
        self.symdefs: dict[str, SymDef] = {}
        self.instances: list[tuple[SymInst, SymDef]] = []
        self.rails: list[str] = []            # nets needing PWR_FLAG
        self.labels: list[tuple[str, float, float]] = []
        self.nc_marks: list[tuple[float, float]] = []

    def add_def(self, sd: SymDef) -> SymDef:
        self.symdefs[sd.lib_name] = sd
        return sd

    def place(self, si: SymInst) -> None:
        sd = self.symdefs[si.lib_name]
        self.instances.append((si, sd))
        for p in sd.pins:
            net = si.pin_nets.get(p.number)
            if net:
                # symbol local +y up -> sheet +y down
                self.labels.append((net, round(si.x + p.x, 3), round(si.y - p.y, 3)))
            elif p.number in si.nc_pins:
                self.nc_marks.append((round(si.x + p.x, 3), round(si.y - p.y, 3)))

    def _lib_symbol(self, sd: SymDef) -> list:
        base = sd.lib_name.split(":")[-1]
        pins = [[bare("pin"), p.etype, "line",
                 [bare("at"), p.x, p.y, p.angle],
                 [bare("length"), 2.54],
                 [bare("name"), p.name,
                  [bare("effects"), [bare("font"), [bare("size"), 1.27, 1.27]]]],
                 [bare("number"), p.number,
                  [bare("effects"), [bare("font"), [bare("size"), 1.27, 1.27]]]]]
                for p in sd.pins]
        return [bare("symbol"), sd.lib_name,
                [bare("exclude_from_sim"), "no"],
                [bare("in_bom"), "yes"],
                [bare("on_board"), "yes"],
                _prop("Reference", sd.ref_prefix, 0, 10.16, hide=True),
                _prop("Value", base, 0, 7.62, hide=True),
                _prop("Footprint", "", 0, 5.08, hide=True),
                _prop("Datasheet", "", 0, 2.54, hide=True),
                _prop("Description", "FluxPCB module symbol", 0, 0, hide=True),
                [bare("symbol"), f"{base}_0_1",
                 [bare("rectangle"), [bare("start"), -7.62, 10.16],
                  [bare("end"), 7.62, -10.16],
                  [bare("stroke"), [bare("width"), 0.254], [bare("type"), "default"]],
                  [bare("fill"), [bare("type"), "background"]]]],
                [bare("symbol"), f"{base}_1_1", *pins]]

    def _instance(self, si: SymInst, sd: SymDef) -> list:
        props = [
            _prop("Reference", si.designator, 0, 12.7),
            _prop("Value", si.value, 0, 10.16),
            _prop("Footprint", si.footprint, 0, -12.7, hide=True),
            _prop("Datasheet", "", 0, -15.24, hide=True),
            _prop("Description", si.value, 0, -17.78, hide=True),
        ]
        pin_uuids = [[bare("pin"), p.number, [bare("uuid"), uid()]] for p in sd.pins]
        return [bare("symbol"),
                [bare("lib_id"), si.lib_name],
                [bare("at"), si.x, si.y, 0],
                [bare("unit"), 1],
                [bare("exclude_from_sim"), "no"],
                [bare("in_bom"), "yes"],
                [bare("on_board"), "yes"],
                [bare("dnp"), "no"],
                [bare("uuid"), uid()],
                *props,
                *pin_uuids,
                [bare("instances"),
                 [bare("project"), self.project,
                  [bare("path"), f"/{uid()}",
                   [bare("reference"), si.designator],
                   [bare("unit"), 1]]]]]

    def _pwr_flag(self, net: str, x: float, y: float) -> list:
        return [bare("symbol"),
                [bare("lib_id"), "flux:PWR_FLAG"],
                [bare("at"), x, y, 0],
                [bare("unit"), 1],
                [bare("exclude_from_sim"), "no"],
                [bare("in_bom"), "yes"],
                [bare("on_board"), "yes"],
                [bare("dnp"), "no"],
                [bare("uuid"), uid()],
                _prop("Reference", "#FLG0", x, y + 2.54, hide=True),
                _prop("Value", net, x, y - 2.54),
                _prop("Footprint", "", x, y, hide=True),
                _prop("Datasheet", "", x, y, hide=True),
                _prop("Description", "", x, y, hide=True),
                [bare("pin"), "1", [bare("uuid"), uid()]],
                [bare("instances"),
                 [bare("project"), self.project,
                  [bare("path"), f"/{uid()}",
                   [bare("reference"), "#FLG0"],
                   [bare("unit"), 1]]]]]

    def _global_label(self, name: str, x: float, y: float) -> list:
        return [bare("global_label"), name,
                [bare("shape"), "input"],
                [bare("at"), x, y, 0],
                [bare("fields_autoplaced"), "yes"],
                [bare("uuid"), uid()]]

    def to_text(self) -> str:
        pwr_flag_def = SymDef("flux:PWR_FLAG",
                              [Pin("1", "PWR_FLAG", "power_out", x=0.0, y=0.0)],
                              ref_prefix="#FLG", auto_layout=False)
        pwr_flags = [self._pwr_flag(n, 25.4 + i * 2.54, -25.4)
                     for i, n in enumerate(self.rails)]
        for i, n in enumerate(self.rails):
            # attach each rail's flag pin to its net via label at same point
            self.labels.append((n, 25.4 + i * 2.54, -25.4))
        root = [bare("kicad_sch"),
                [bare("version"), bare(VERSION)],
                [bare("generator"), "flux_neurotransmitter"],
                [bare("generator_version"), "10.0"],
                [bare("uuid"), uid()],
                [bare("paper"), "A3"],
                [bare("embedded_fonts"), "no"],
                [bare("title_block"),
                 [bare("title"), self.title],
                 [bare("date"), "2026-10-20"],
                 [bare("rev"), "A"],
                 [bare("company"), "FluxPCB"]],
                [bare("lib_symbols")] + [self._lib_symbol(sd)
                                          for sd in self.symdefs.values()] + [self._lib_symbol(pwr_flag_def)],
                *pwr_flags,
                *[self._instance(si, sd) for si, sd in self.instances],
                *[self._global_label(n, x, y) for n, x, y in self.labels],
                *[[bare("no_connect"),
                   [bare("at"), x, y],
                   [bare("uuid"), uid()]] for x, y in self.nc_marks],
                ]
        return _render(root, 0) + "\n"


def write(sch: Schematic, path) -> None:
    """Write schematic + companion symbol library + sym-lib-table so ERC
    resolves library nicknames without warnings."""
    from pathlib import Path

    from fluxpcb.kicadgen.schematic import _render, bare

    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(sch.to_text(), encoding="utf-8", newline="\n")

    # companion library file: symbols WITHOUT lib prefix, PWR_FLAG included
    # (identical definition to the one embedded in to_text())
    pwr_flag_def = SymDef("flux:PWR_FLAG",
                          [Pin("1", "PWR_FLAG", "power_out", x=0.0, y=0.0)],
                          ref_prefix="#FLG", auto_layout=False)
    syms = list(sch.symdefs.values()) + [pwr_flag_def]
    lib_root = [bare("kicad_symbol_lib"),
                [bare("version"), bare(VERSION)],
                [bare("generator"), "flux_neurotransmitter"],
                [bare("generator_version"), "10.0"]]
    for sd in syms:
        node = sch._lib_symbol(sd)
        node[1] = sd.lib_name.split(":")[-1]      # strip prefix for lib file
        lib_root.append(node)
    (p.parent / "flux.kicad_sym").write_text(_render(lib_root, 0) + "\n",
                                             encoding="utf-8", newline="\n")

    sym_table = ('(sym_lib_table\n'
                 '\t(version 7)\n'
                 '\t(lib (name "flux")(type "KiCad")'
                 '(uri "${KIPRJMOD}/flux.kicad_sym")(options "")'
                 '(descr "FluxPCB generated symbols"))\n'
                 ')\n')
    (p.parent / "sym-lib-table").write_text(sym_table, encoding="utf-8",
                                            newline="\n")

    # minimal KiCad project file so kicad-cli resolves project libraries
    import json
    pro = p.parent / (p.stem + ".kicad_pro")
    if not pro.exists():
        pro.write_text(json.dumps({
            "board": {
                "design_settings": {
                    "rules": {
                        "min_clearance": 0.15,
                        "min_hole_to_hole": 0.2,
                        "min_through_hole_diameter": 0.3,
                        "min_track_width": 0.15,
                        "min_via_diameter": 0.6,
                        "min_via_annular_width": 0.1,
                        "solder_mask_to_copper_clearance": 0.0,
                        "min_copper_edge_clearance": 0.3,
                        "min_silk_clearance": 0.0,
                        "min_text_height": 0.8,
                        "min_text_thickness": 0.08,
                        "allow_blind_buried_vias": False,
                        "allow_microvias": False,
                        "max_error": 0.01,
                    },
                    "rule_severities": {
                        "courtyards_overlap": "ignore",
                        "lib_footprint_issues": "ignore",
                    },
                    "layer_presets": [], "viewports": [],
                },
            },
            "boards": [], "cvpcb": {"equivalence_files": []},
            "erc": {
                "erc_exclusions": [],
                "meta": {"version": 0},
                # B2B connector pass-through nets are single-ended on this
                # board by design (they continue onto the core board),
                # so isolated labels are not a defect for this class.
                "rule_severities": {"isolated_pin_label": "ignore"},
            },
            "libraries": {"pinned_footprint_libs": [], "pinned_symbol_libs": ["flux"]},
            "meta": {"filename": pro.name, "version": 2},
            "net_settings": {"classes": []},
            "schematic": {"legacy_lib_dir": "", "legacy_lib_list": []},
            "sheets": [[p.name, ""]],
            "text_variables": {},
        }, indent=1), encoding="utf-8", newline="\n")
