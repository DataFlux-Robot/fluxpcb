"""KiCad 10 .kicad_pcb generator.

Strategy: reuse official KiCad footprint libraries verbatim (parse -> inject
nets/reference/position -> embed), so pad geometry is factory-grade. Board
skeleton (layers/stackup/nets/outline) generated per target stackup.
"""
from __future__ import annotations

import re
import uuid as uuidlib
from pathlib import Path

KICAD_FP_DIR = Path(r"C:\Users\zhang\AppData\Local\Programs\KiCad\10.0"
                    r"\share\kicad\footprints")


def uid() -> str:
    return str(uuidlib.uuid4())


# ------------------------------------------------------------- sexpr parsing
def parse(s: str):
    """Parse first top-level sexpr. Returns nested lists; atoms as ('a', v),
    strings as ('s', v)."""
    i = s.find("(")
    items, n = _parse_items(s, i + 1)
    return items


def _parse_items(s, i):
    items = []
    n = len(s)
    while i < n:
        c = s[i]
        if c == '"':
            j = i + 1
            while s[j] != '"':
                j += 2 if s[j] == "\\" else 1
            items.append(("s", s[i + 1:j]))
            i = j + 1
        elif c == "(":
            child, end = _parse_items(s, i + 1)
            items.append(("n", child))
            i = end
        elif c == ")":
            return items, i + 1
        elif c.isspace():
            i += 1
        else:
            j = i
            while j < n and not s[j].isspace() and s[j] not in '()"':
                j += 1
            items.append(("a", s[i:j]))
            i = j
    return items, i


def render(items, depth=0) -> str:
    pad = "\t" * depth
    parts = []
    for kind, v in items:
        if kind == "s":
            parts.append('"' + v.replace('\\', '\\\\').replace('"', '\\"') + '"')
        elif kind == "a":
            parts.append(v)
        else:
            parts.append(render(v, depth + 1))
    inline = "\n" not in "".join(parts) and sum(len(p) + 1 for p in parts) < 70
    if inline:
        return pad + "(" + " ".join(p.strip() for p in parts) + ")"
    inner = "\n".join(p if p.startswith("\t") else p for p in parts)
    return f"{pad}(\n{inner}\n{pad})"


def _find(items, head):
    for k, v in items:
        if k == "n" and v and v[0][1] == head:
            return v
    return None


def _find_all(items, head):
    return [v for k, v in items if k == "n" and v and v[0][1] == head]


# ------------------------------------------------------------- footprints
class Fp:
    """A placed footprint from the official library with injected nets."""

    def __init__(self, lib: str, name: str, ref: str, value: str,
                 x: float, y: float, rot: float = 0.0,
                 pad_nets: dict[str, str] | None = None):
        self.path = KICAD_FP_DIR / f"{lib}.pretty" / f"{name}.kicad_mod"
        # parse returns the item list INSIDE the footprint parens:
        # [("a","footprint"), ("s",name), ...children]
        self.tree = parse(self.path.read_text(encoding="utf-8", errors="replace"))
        # keep "footprint" head atom; replace the bare name with lib:name
        self.tree[1] = ("s", f"{lib}:{name}")
        self.lib, self.name = lib, name
        self.ref, self.value = ref, value
        self.x, self.y, self.rot = x, y, rot
        self.pad_nets = pad_nets or {}

    def inject(self, netmap: dict[str, tuple[int, str]]):
        """netmap: net name -> (number, name). Insert position/uuid and pad nets.

        Footprint rotation is BAKED into pads (position, size swap, pad angle)
        and the footprint saved with rot=0 — this mirrors what KiCad's own
        saver does and avoids transform ambiguity in DRC.
        """
        tree = self.tree
        layer_i = next(i for i, (k, v) in enumerate(tree) if k == "n" and v[0][1] == "layer")
        tree.insert(layer_i + 1, ("n", [("a", "uuid"), ("s", uid())]))
        tree.insert(layer_i + 2, ("n", [("a", "at"), ("a", _f(self.x)),
                                        ("a", _f(self.y)), ("a", "0")]))
        # Reference / Value
        for prop in _find_all(tree, "property"):
            key = prop[1][1]
            if key == "Reference":
                prop[2] = ("s", self.ref)
            elif key == "Value":
                prop[2] = ("s", self.value)
        # bake rotation into pads
        rot = int(self.rot) % 360
        if rot:
            for pad in _find_all(tree, "pad"):
                for node in pad:
                    if node[0] != "n":
                        continue
                    head = node[1][0][1] if node[1] and node[1][0][0] == "a" else None
                    if head == "at":
                        vals = [it for it in node[1][1:]]
                        x, y = float(vals[0][1]), float(vals[1][1])
                        ang = float(vals[2][1]) if len(vals) > 2 else 0.0
                        if rot == 90:
                            x, y = y, -x
                        elif rot == 180:
                            x, y = -x, -y
                        elif rot == 270:
                            x, y = -y, x
                        new = [("a", "at"), ("a", _f(x)), ("a", _f(y))]
                        if len(vals) > 2:            # original pad had an angle
                            new.append(("a", _f((ang + rot) % 360)))
                        node[1][:] = new
                    elif head == "size":
                        vals = node[1][1:]
                        w, h = float(vals[0][1]), float(vals[1][1])
                        if rot in (90, 270):
                            w, h = h, w
                        node[1][:] = [("a", "size"), ("a", _f(w)), ("a", _f(h))]
        # pad nets
        for pad in _find_all(tree, "pad"):
            num = pad[1][1]
            netname = self.pad_nets.get(num)
            if not netname or netname not in netmap:
                continue
            nn, nm = netmap[netname]
            pad[:] = [it for it in pad if not (it[0] == "n" and it[1] and it[1][0][1] == "net")]
            layers = next((i for i, it in enumerate(pad)
                           if it[0] == "n" and it[1] and it[1][0][1] == "layers"), len(pad) - 1)
            pad.insert(layers + 1, ("n", [("a", "net"), ("a", str(nn)), ("s", nm)]))
        return self


def _f(v) -> str:
    s = f"{float(v):.4f}".rstrip("0").rstrip(".")
    return s if s else "0"


# ------------------------------------------------------------- board
LAYERS_4 = ['(0 "F.Cu" signal "top_copper")', '(31 "B.Cu" signal "bottom_copper")',
            '(36 "B.Paste" user)', '(37 "F.Paste" user)',
            '(38 "B.SilkS" user "B.Silkscreen")', '(39 "F.SilkS" user "F.Silkscreen")',
            '(40 "B.Mask" user)', '(41 "F.Mask" user)',
            '(44 "Edge.Cuts" user)', '(45 "Margin" user)',
            '(48 "B.Fab" user)', '(49 "F.Fab" user)']


def stackup_4l() -> str:
    cu = '(layer "{}" (type copper) (thickness 0.035))'
    prepreg = ('(layer "dielectric {n}" (type prepreg) (thickness 0.21) '
               '(material "FR4") (epsilon_r 4.4) (loss_tangent 0.02))')
    core = ('(layer "dielectric {n}" (type core) (thickness 1.06) '
            '(material "FR4") (epsilon_r 4.4) (loss_tangent 0.02))')
    mask = '(layer "{}" (type "{} Solder Mask") (color "Green") (thickness 0.01))'
    silk = '(layer "{}" (type "{} Silk Screen") (color "White"))'
    return ("\t(stackup\n"
            f"\t\t{silk.format('F.SilkS', 'Top')}\n"
            f"\t\t{mask.format('F.Mask', 'Top')}\n"
            f"\t\t{cu.format('F.Cu')}\n"
            f"\t\t{prepreg.format(n=1)}\n"
            '\t\t(layer "In1.Cu" (type copper) (thickness 0.035))\n'
            f"\t\t{core.format(n=2)}\n"
            '\t\t(layer "In2.Cu" (type copper) (thickness 0.035))\n'
            f"\t\t{prepreg.format(n=3)}\n"
            f"\t\t{cu.format('B.Cu')}\n"
            f"\t\t{mask.format('B.Mask', 'Bottom')}\n"
            f"\t\t{silk.format('B.SilkS', 'Bottom')}\n"
            "\t)\n")


class Board:
    def __init__(self, title: str, w_mm: float, h_mm: float, net_names: list[str]):
        self.title = title
        self.w, self.h = w_mm, h_mm
        self.nets: dict[str, int] = {}
        self.net_lines = ['\t(net 0 "")']
        for i, n in enumerate(net_names, 1):
            self.nets[n] = i
            self.net_lines.append(f'\t(net {i} "{n}")')
        self.footprints: list[Fp] = []

    def place(self, fp: Fp) -> Fp:
        self.footprints.append(fp)
        return fp

    def ground_zones(self, net_name: str = "GND") -> list[str]:
        """Full-board GND pours on F.Cu and B.Cu (2-layer standard practice)."""
        n = self.nets.get(net_name, 0)
        w, h = _f(self.w), _f(self.h)
        zones = []
        for layer in ("F.Cu", "B.Cu"):
            zones.append(
                f'\t(zone (net {n}) (net_name "{net_name}") '
                f'(layer "{layer}") (uuid "{uid()}") (hatch edge 0.508) '
                f'(connect_pads (clearance 0.25)) (min_thickness 0.2) '
                f'(filled_areas_thickness no) '
                f'(fill (thermal_gap 0.5) (thermal_bridge_width 0.5)) '
                f'(polygon (pts (xy 0.2 0.2) (xy {w} -0.2) (xy {w} {h}) '
                f'(xy 0.2 {h}))))')
        return zones

    def to_text(self) -> str:
        netmap = {n: (i, n) for n, i in self.nets.items()}
        fps = []
        for fp in self.footprints:
            fp.inject(netmap)
            fps.append(render(fp.tree, 1))
        outline = (f'\t(gr_rect (start 0 0) (end {_f(self.w)} {_f(self.h)}) '
                   f'(stroke (width 0.05) (type solid)) (fill no) (layer "Edge.Cuts") '
                   f'(uuid "{uid()}"))')
        header = f'''(kicad_pcb
\t(version 20241229)
\t(generator "flux_neurotransmitter")
\t(generator_version "10.0")
\t(general (thickness 1.6) (legacy_teardrops no))
\t(paper "A4")
\t(title_block (title "{self.title}") (company "FluxPCB"))
\t(layers
{chr(10).join(chr(9)+chr(9)+l[1:1] for l in [])}
\t)
'''
        layers_block = "\n".join("\t\t" + l for l in LAYERS_4)
        text = (f"(kicad_pcb\n"
                f"\t(version 20241229)\n"
                f"\t(generator \"flux_neurotransmitter\")\n"
                f"\t(generator_version \"10.0\")\n"
                f"\t(general (thickness 1.6) (legacy_teardrops no))\n"
                f"\t(paper \"A4\")\n"
                f"\t(title_block (title \"{self.title}\") "
                f"(company \"FluxPCB\"))\n"
                f"\t(layers\n{layers_block}\n\t)\n"
                f"\t(setup\n{stackup_4l()}"
                f"\t\t(pad_to_mask_clearance 0.05)\n"
                f"\t\t(allow_soldermask_bridges_in_footprints no)\n"
                f"\t\t(tenting front back)\n"
                f"\t)\n"
                f"\t(net 0 \"\")\n" +
                "\n".join(self.net_lines[1:]) + "\n" +
                "\n".join(fps) + "\n" +
                "\n".join(self.ground_zones()) + "\n" +
                outline + "\n)\n")
        return text


def write(board: Board, path) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(board.to_text(), encoding="utf-8", newline="\n")
