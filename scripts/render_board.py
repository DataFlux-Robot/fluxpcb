#!/usr/bin/env python3
"""Render a routed board as WebP images: all copper layers together, then one image per copper
layer. Tracks and vias in each layer's colour, pours filled faintly, pads grey, the outline on a
dark board. Zones are refilled in memory first, so pours show as fabricated.

    KICAD_PYTHON=/usr/bin/python3 python3 scripts/render_board.py <board.kicad_pcb> <out dir> [width]

Writes <out dir>/all.webp and <out dir>/<layer>.webp (F.Cu, In1.Cu, ..., B.Cu)."""
import math
import os
import sys

import pcbnew
from PIL import Image, ImageDraw

COLOURS = {"F.Cu": (224, 96, 58), "In1.Cu": (217, 178, 59), "In2.Cu": (59, 191, 122), "In3.Cu": (181, 106, 217),
           "In4.Cu": (217, 106, 168), "In5.Cu": (106, 200, 217), "In6.Cu": (170, 217, 106), "B.Cu": (59, 143, 217)}
BOARD = (7, 37, 32, 255)
OUTLINE = (201, 209, 217, 255)
PAD = (154, 164, 174, 235)
VIA = (232, 232, 232, 255)
HOLE = (7, 37, 32, 255)
SS = 2  # supersampling


def polys(ps):
    """Outlines and holes of a SHAPE_POLY_SET as point lists (nm)."""
    out = []
    for i in range(ps.OutlineCount()):
        o = ps.Outline(i)
        outline = [(o.CPoint(j).x, o.CPoint(j).y) for j in range(o.PointCount())]
        holes = []
        for h in range(ps.HoleCount(i)):
            hc = ps.Hole(i, h)
            holes.append([(hc.CPoint(j).x, hc.CPoint(j).y) for j in range(hc.PointCount())])
        out.append((outline, holes))
    return out


def main():
    pcb, out = sys.argv[1], sys.argv[2]
    width = int(sys.argv[3]) if len(sys.argv) > 3 else 900
    os.makedirs(out, exist_ok=True)
    board = pcbnew.LoadBoard(pcb)
    pcbnew.ZONE_FILLER(board).Fill(board.Zones())
    ids = [l for l in board.GetEnabledLayers().CuStack()]
    layers = [(board.GetLayerName(l) if board.GetLayerName(l) in COLOURS else pcbnew.BOARD.GetStandardLayerName(l), l) for l in ids]
    edge = pcbnew.SHAPE_POLY_SET()
    board.GetBoardPolygonOutlines(edge, True)
    bb = board.GetBoardEdgesBoundingBox()
    x0, y0, w, h = bb.GetX(), bb.GetY(), bb.GetWidth(), bb.GetHeight()
    # landscape boards at `width`, tall ones capped so a column of them stays readable
    scale = min(width / w, width * 1.4 / h) * SS
    margin = 6 * SS
    W, H = int(w * scale) + 2 * margin, int(h * scale) + 2 * margin

    def P(pt):
        return (margin + (pt[0] - x0) * scale, margin + (pt[1] - y0) * scale)

    def wpx(nm):
        return max(1, int(round(nm * scale)))

    def layer_image(name, lid, alpha, pour_alpha):
        img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        c = COLOURS.get(name, (200, 200, 200))
        for z in board.Zones():
            if z.GetIsRuleArea() or not z.IsOnLayer(lid):
                continue
            for outline, holes in polys(z.GetFilledPolysList(lid)):
                if len(outline) > 2:
                    d.polygon([P(p) for p in outline], fill=c + (pour_alpha,))
                for hole in holes:
                    if len(hole) > 2:
                        d.polygon([P(p) for p in hole], fill=(0, 0, 0, 0))
        for t in board.GetTracks():
            if t.GetLayer() != lid or t.Type() == pcbnew.PCB_VIA_T:
                continue
            pts = [t.GetStart(), t.GetEnd()]
            if t.Type() == pcbnew.PCB_ARC_T:
                pts = [t.GetStart(), t.GetMid(), t.GetEnd()]
            pts = [P((p.x, p.y)) for p in pts]
            r = wpx(t.GetWidth()) / 2
            d.line(pts, fill=c + (alpha,), width=wpx(t.GetWidth()))
            for p in (pts[0], pts[-1]):
                d.ellipse([p[0] - r, p[1] - r, p[0] + r, p[1] + r], fill=c + (alpha,))
        return img

    def base():
        img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        for outline, holes in polys(edge):
            d.polygon([P(p) for p in outline], fill=BOARD, outline=OUTLINE, width=SS)
            for hole in holes:
                d.polygon([P(p) for p in hole], fill=(0, 0, 0, 0), outline=OUTLINE, width=SS)
        return img

    def pads_vias(img, only=None):
        d = ImageDraw.Draw(img)
        for fp in board.GetFootprints():
            for pad in fp.Pads():
                on = [lid for _, lid in layers if pad.IsOnLayer(lid)]
                if not on or (only is not None and only not in on):
                    continue
                ps = pcbnew.SHAPE_POLY_SET()
                pad.TransformShapeToPolygon(ps, only if only is not None else on[0], 0, pcbnew.FromMM(0.005), pcbnew.ERROR_INSIDE)
                for outline, _ in polys(ps):
                    if len(outline) > 2:
                        d.polygon([P(p) for p in outline], fill=PAD)
                if pad.HasHole():
                    c = P((pad.GetPosition().x, pad.GetPosition().y))
                    r = wpx(min(pad.GetDrillSize().x, pad.GetDrillSize().y)) / 2
                    d.ellipse([c[0] - r, c[1] - r, c[0] + r, c[1] + r], fill=HOLE)
        for t in board.GetTracks():
            if t.Type() != pcbnew.PCB_VIA_T or (only is not None and not t.IsOnLayer(only)):
                continue
            c = P((t.GetPosition().x, t.GetPosition().y))
            r, rh = wpx(t.GetWidth(t.TopLayer())) / 2, wpx(t.GetDrillValue()) / 2
            d.ellipse([c[0] - r, c[1] - r, c[0] + r, c[1] + r], fill=VIA)
            d.ellipse([c[0] - rh, c[1] - rh, c[0] + rh, c[1] + rh], fill=HOLE)

    def save(img, name):
        flat = Image.new("RGBA", img.size, (0, 0, 0, 0))
        flat.alpha_composite(img)
        flat = flat.resize((W // SS, H // SS), Image.LANCZOS)
        flat.save(os.path.join(out, f"{name}.webp"), "WEBP", quality=74, method=6)

    # all layers: bottom first, top last
    img = base()
    for name, lid in reversed(layers):
        img.alpha_composite(layer_image(name, lid, 210, 28))
    pads_vias(img)
    save(img, "all")
    for name, lid in layers:
        img = base()
        img.alpha_composite(layer_image(name, lid, 255, 60))
        pads_vias(img, lid)
        save(img, name)
    print(os.path.basename(pcb), [n for n, _ in layers], f"{W // SS}x{H // SS}")


main()
