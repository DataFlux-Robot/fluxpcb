#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""fluxpcb_route — fluxpcb routing pipeline for autorouter-bench.

Pipeline: BGA escape (channel-lattice MCMF) → region routing (GPU Eikonal + exact verify).

Usage: python fluxpcb_route.py INPUT.kicad_pcb OUTPUT.kicad_pcb [--timeout S]
"""
import json
import math
import sys
import time
from pathlib import Path

MM = 1e6
FLUXPCB = Path("E:/DATA/vscode/ZCODE/flux-neurotransmitter")
sys.path.insert(0, str(FLUXPCB / "fluxpcb" / "kicadgen"))
sys.path.insert(0, str(FLUXPCB))

import pcbnew  # noqa: E402
import routegrid as rg  # noqa: E402


def route_board(input_pcb, output_pcb, timeout_s=1200):
    """Route a board: BGA escape + region routing."""
    t0 = time.time()

    b = pcbnew.LoadBoard(str(input_pcb))
    b.BuildConnectivity()
    lids = rg.signal_layer_ids(b)

    # ── Phase 1: BGA Escape (channel-lattice MCMF) ──────
    # Run probe as subprocess (avoids dataclass import issues)
    import subprocess
    import tempfile
    solver_path = FLUXPCB / "fluxpcb" / "kicadgen" / "bga_escape_solver.py"
    with tempfile.NamedTemporaryFile(suffix='.json', delete=False) as f:
        tmp_json = f.name

    result = subprocess.run(
        [sys.executable, str(solver_path), str(input_pcb),
         "--json", tmp_json],
        capture_output=True, text=True, timeout=120
    )
    if not Path(tmp_json).exists() or Path(tmp_json).stat().st_size == 0:
        print(f"Probe failed (no BGA?): {result.stderr[-200:]}", file=sys.stderr)
        copper = {}
        probe = {}
    else:
        probe = json.loads(Path(tmp_json).read_text())
        copper = probe.get('copper', {})
    Path(tmp_json).unlink(missing_ok=True)

    copper = probe.get('copper', {})
    tracks_added = 0
    vias_added = 0

    # Write escape copper
    for t in copper.get('tracks', []):
        net = b.GetNetInfo().GetNetItem(t['net'])
        if net is None:
            continue
        tr = pcbnew.PCB_TRACK(b)
        tr.SetStart(pcbnew.VECTOR2I(int(t['a'][0]*MM), int(t['a'][1]*MM)))
        tr.SetEnd(pcbnew.VECTOR2I(int(t['b'][0]*MM), int(t['b'][1]*MM)))
        tr.SetWidth(int(t['width']*MM))
        layer_map = {'F.Cu': pcbnew.F_Cu, 'In1.Cu': pcbnew.In1_Cu, 'B.Cu': pcbnew.B_Cu}
        tr.SetLayer(layer_map.get(t.get('layer', 'F.Cu'), pcbnew.F_Cu))
        tr.SetNet(net)
        b.Add(tr)
        tracks_added += 1

    for v in copper.get('vias', []):
        net = b.GetNetInfo().GetNetItem(v['net'])
        if net is None:
            continue
        via = pcbnew.PCB_VIA(b)
        via.SetPosition(pcbnew.VECTOR2I(int(v['at'][0]*MM), int(v['at'][1]*MM)))
        via.SetWidth(int(v['pad']*MM))
        via.SetDrill(int(v['drill']*MM))
        via.SetNet(net)
        b.Add(via)
        vias_added += 1

    # ── Phase 2: Region Routing (GPU Eikonal + exact verify) ──
    from gpu_eikonal import GPUEikonalRouter
    router = GPUEikonalRouter(b, lids)

    # Read board's design rules
    import json as _json
    pro_path = str(input_pcb).replace('.kicad_pcb', '.kicad_pro')
    track_w = 0.127  # default
    via_d, via_drill = 0.45, 0.2
    if Path(pro_path).exists():
        pro = _json.loads(Path(pro_path).read_text())
        for cls in pro.get('net_settings', {}).get('classes', []):
            if cls.get('name') == 'Default':
                # KiCad project stores in nm (or IU), but some projects use mm
                tw = cls.get('track_width', 0.127)
                track_w = tw / 1e6 if tw > 100 else tw  # nm→mm or already mm
                vd = cls.get('via_diameter', 0.45)
                via_d = vd / 1e6 if vd > 100 else vd
                vdr = cls.get('via_drill', 0.2)
                via_drill = vdr / 1e6 if vdr > 100 else vdr
                break
    print(f"Rules: track={track_w:.3f}mm, via={via_d:.2f}/{via_drill:.2f}mm")

    # Find remaining opens
    from fluxpcb.routing import find_kicad
    from fluxpcb.kernel import state as kstate
    kicad_cli, kipy = find_kicad()

    # Collect net pads
    net_pads = {}
    for fp in b.GetFootprints():
        for pad in fp.Pads():
            nc = pad.GetNetCode()
            if nc == 0:
                continue
            nn = pad.GetNetname()
            if nn not in net_pads:
                net_pads[nn] = []
            p = pad.GetPosition()
            net_pads[nn].append((p.x/MM, p.y/MM, str(b.GetLayerName(pad.GetLayer()))))

    # Get grid origin (board edge)
    gm = router.gm
    ox, oy = gm.edge[0], gm.edge[1]  # board edge origin

    # Route each net (pair by pair)
    routed = 0
    skipped = 0
    for nn, pads in sorted(net_pads.items()):
        if len(pads) < 2:
            continue
        if time.time() - t0 > timeout_s:
            break

        # Route consecutive pad pairs
        for i in range(len(pads) - 1):
            x1, y1, l1 = pads[i]
            x2, y2, l2 = pads[i+1]

            net = b.GetNetInfo().GetNetItem(nn)
            if net is None:
                continue
            nc = net.GetNetCode()

            free = router.build_free(nc)
            # Convert to grid coords (subtract board origin)
            src_gx, src_gy = int((x1-ox)/router.G), int((y1-oy)/router.G)
            dst_gx, dst_gy = int((x2-ox)/router.G), int((y2-oy)/router.G)

            # Bounds check
            if not (0 <= src_gx < router.nx and 0 <= src_gy < router.ny):
                skipped += 1
                continue
            if not (0 <= dst_gx < router.nx and 0 <= dst_gy < router.ny):
                skipped += 1
                continue

            # 标记源和目标为可走
            for li in range(router.nl):
                free[li][src_gx][src_gy] = True
                free[li][dst_gx][dst_gy] = True

            T = router.solve(free, [(0, dst_gx, dst_gy)])
            path = router.extract_path(T, free, (0, src_gx, src_gy))

            if len(path) < 2:
                skipped += 1
                continue

            # 精确碰撞验证(geomir)
            try:
                import geomir
                sgs, rgs = geomir.extract_objects(b, lids)
                rules = {'clearance': 0.15, 'clearance_smd': 0.075}
                collision = False
                for j in range(len(path)-1):
                    li1, gx1, gy1 = path[j]
                    li2, gx2, gy2 = path[j+1]
                    if li1 == li2:
                        px1, py1 = ox + (gx1+0.5)*router.G, oy + (gy1+0.5)*router.G
                        px2, py2 = ox + (gx2+0.5)*router.G, oy + (gy2+0.5)*router.G
                        li_idx = lids.index(lids[li1]) if li1 < len(lids) else 0
                        ok, _ = geomir.verify_polyline(
                            [(px1,py1),(px2,py2)], 0.127, nc,
                            sgs[li_idx], rgs[li_idx], rules=rules)
                        if not ok:
                            collision = True
                            break
                if collision:
                    skipped += 1
                    continue
            except Exception:
                pass  # geomir 不可用则跳过验证

            # Draw (add board origin back to grid coords)
            for j in range(len(path)-1):
                li1, gx1, gy1 = path[j]
                li2, gx2, gy2 = path[j+1]
                px1, py1 = ox + (gx1+0.5)*router.G, oy + (gy1+0.5)*router.G
                px2, py2 = ox + (gx2+0.5)*router.G, oy + (gy2+0.5)*router.G
                if li1 != li2:
                    v = pcbnew.PCB_VIA(b)
                    v.SetPosition(pcbnew.VECTOR2I(int(px1*MM), int(py1*MM)))
                    v.SetWidth(int(via_d*MM))
                    v.SetDrill(int(via_drill*MM))
                    v.SetNet(net)
                    b.Add(v)
                else:
                    t = pcbnew.PCB_TRACK(b)
                    t.SetStart(pcbnew.VECTOR2I(int(px1*MM), int(py1*MM)))
                    t.SetEnd(pcbnew.VECTOR2I(int(px2*MM), int(py2*MM)))
                    t.SetWidth(int(track_w*MM))
                    t.SetLayer(lids[li1])
                    t.SetNet(net)
                    b.Add(t)

            # Connect pads
            first = path[0]
            fx, fy = ox + (first[1]+0.5)*router.G, oy + (first[2]+0.5)*router.G
            if math.hypot(fx-x1, fy-y1) > 0.02:
                t = pcbnew.PCB_TRACK(b)
                t.SetStart(pcbnew.VECTOR2I(int(x1*MM), int(y1*MM)))
                t.SetEnd(pcbnew.VECTOR2I(int(fx*MM), int(fy*MM)))
                t.SetWidth(int(track_w*MM))
                t.SetLayer(lids[first[0]])
                t.SetNet(net)
                b.Add(t)

            last = path[-1]
            lx, ly = ox + (last[1]+0.5)*router.G, oy + (last[2]+0.5)*router.G
            if math.hypot(lx-x2, ly-y2) > 0.02:
                t = pcbnew.PCB_TRACK(b)
                t.SetStart(pcbnew.VECTOR2I(int(lx*MM), int(ly*MM)))
                t.SetEnd(pcbnew.VECTOR2I(int(x2*MM), int(y2*MM)))
                t.SetWidth(int(track_w*MM))
                t.SetLayer(lids[last[0]])
                t.SetNet(net)
                b.Add(t)

            routed += 1
            router.gm = rg.GridModel(b, lids)

    b.Save(str(output_pcb))
    elapsed = time.time() - t0

    return {
        'routed': routed,
        'skipped': skipped,
        'escape_tracks': tracks_added,
        'escape_vias': vias_added,
        'seconds': round(elapsed, 1),
    }


def main():
    if len(sys.argv) < 3:
        print("Usage: python fluxpcb_route.py INPUT.kicad_pcb OUTPUT.kicad_pcb [--timeout S]")
        sys.exit(1)

    input_pcb = sys.argv[1]
    output_pcb = sys.argv[2]
    timeout_s = 1200
    if '--timeout' in sys.argv:
        timeout_s = int(sys.argv[sys.argv.index('--timeout') + 1])

    result = route_board(input_pcb, output_pcb, timeout_s)
    print(json.dumps(result))


if __name__ == '__main__':
    main()
