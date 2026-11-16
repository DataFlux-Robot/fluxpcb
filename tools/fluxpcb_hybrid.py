#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""fluxpcb_hybrid — draw-all + strong congestion rip-up + commit gate reroute."""
import json, math, sys, time
from pathlib import Path
MM = 1e6
sys.path.insert(0, 'E:/DATA/vscode/ZCODE/flux-neurotransmitter/fluxpcb/kicadgen')
import pcbnew
import routegrid as rg
import geomir
from gpu_eikonal import GPUEikonalRouter

def run(input_pcb, output_pcb, timeout_s=500):
    t0 = time.time()
    b = pcbnew.LoadBoard(str(input_pcb))
    b.BuildConnectivity()
    lids = rg.signal_layer_ids(b)
    gm = rg.GridModel(b, lids)
    router = GPUEikonalRouter(b, lids)
    router.gm = gm

    pro_path = str(input_pcb).replace('.kicad_pcb', '.kicad_pro')
    track_w, via_d, via_drill = 0.15, 0.6, 0.35
    if Path(pro_path).exists():
        pro = json.loads(Path(pro_path).read_text())
        for cls in pro.get('net_settings', {}).get('classes', []):
            if cls.get('name') == 'Default':
                tw = cls.get('track_width', 0.15)
                track_w = tw / 1e6 if tw > 100 else tw
                vd = cls.get('via_diameter', 0.6)
                via_d = vd / 1e6 if vd > 100 else vd
                vdr = cls.get('via_drill', 0.35)
                via_drill = vdr / 1e6 if vdr > 100 else vdr
                break

    net_pads = {}
    for fp in b.GetFootprints():
        for pad in fp.Pads():
            nc = pad.GetNetCode()
            if nc == 0: continue
            nn = pad.GetNetname()
            if nn not in net_pads: net_pads[nn] = []
            p = pad.GetPosition()
            net_pads[nn].append((p.x/MM, p.y/MM, str(b.GetLayerName(pad.GetLayer()))))

    def mst_pairs(pads):
        n = len(pads)
        if n <= 2: return [(i, i+1) for i in range(n-1)]
        visited = {0}; edges = []
        while len(visited) < n:
            best = None
            for i in visited:
                for j in range(n):
                    if j in visited: continue
                    d = math.hypot(pads[i][0]-pads[j][0], pads[i][1]-pads[j][1])
                    if best is None or d < best[0]: best = (d, i, j)
            if best is None: break
            _, i, j = best; edges.append((i, j)); visited.add(j)
        return edges

    def draw_path(path, nc, net, x1, y1, x2, y2):
        ox, oy = gm.edge[0], gm.edge[1]
        items = []
        for k in range(len(path)-1):
            li1, gx1, gy1 = path[k]; li2, gx2, gy2 = path[k+1]
            px1, py1 = ox+(gx1+0.5)*router.G, oy+(gy1+0.5)*router.G
            px2, py2 = ox+(gx2+0.5)*router.G, oy+(gy2+0.5)*router.G
            if li1 != li2:
                v = pcbnew.PCB_VIA(b); v.SetPosition(pcbnew.VECTOR2I(int(px1*MM), int(py1*MM)))
                v.SetWidth(int(via_d*MM)); v.SetDrill(int(via_drill*MM)); v.SetNet(net); b.Add(v); items.append(v)
            else:
                t = pcbnew.PCB_TRACK(b); t.SetStart(pcbnew.VECTOR2I(int(px1*MM), int(py1*MM)))
                t.SetEnd(pcbnew.VECTOR2I(int(px2*MM), int(py2*MM))); t.SetWidth(int(track_w*MM))
                t.SetLayer(lids[li1]); t.SetNet(net); b.Add(t); items.append(t)
        first = path[0]; fx, fy = ox+(first[1]+0.5)*router.G, oy+(first[2]+0.5)*router.G
        if math.hypot(fx-x1, fy-y1) > 0.02:
            t = pcbnew.PCB_TRACK(b); t.SetStart(pcbnew.VECTOR2I(int(x1*MM), int(y1*MM)))
            t.SetEnd(pcbnew.VECTOR2I(int(fx*MM), int(fy*MM))); t.SetWidth(int(track_w*MM))
            t.SetLayer(lids[first[0]]); t.SetNet(net); b.Add(t); items.append(t)
        last = path[-1]; lx, ly = ox+(last[1]+0.5)*router.G, oy+(last[2]+0.5)*router.G
        if math.hypot(lx-x2, ly-y2) > 0.02:
            t = pcbnew.PCB_TRACK(b); t.SetStart(pcbnew.VECTOR2I(int(lx*MM), int(ly*MM)))
            t.SetEnd(pcbnew.VECTOR2I(int(x2*MM), int(y2*MM))); t.SetWidth(int(track_w*MM))
            t.SetLayer(lids[last[0]]); t.SetNet(net); b.Add(t); items.append(t)
        return items

    def verify_path(path, nc, x1, y1, x2, y2):
        ox, oy = gm.edge[0], gm.edge[1]
        segs = []; vias = []
        for k in range(len(path)-1):
            li1, gx1, gy1 = path[k]; li2, gx2, gy2 = path[k+1]
            px1, py1 = ox+(gx1+0.5)*router.G, oy+(gy1+0.5)*router.G
            px2, py2 = ox+(gx2+0.5)*router.G, oy+(gy2+0.5)*router.G
            if li1 == li2:
                segs.append((px1, py1, px2, py2, lids[li1], track_w))
            else:
                vias.append((px1, py1))
        first = path[0]; fx, fy = ox+(first[1]+0.5)*router.G, oy+(first[2]+0.5)*router.G
        if math.hypot(fx-x1, fy-y1) > 0.02:
            segs.append((x1, y1, fx, fy, lids[first[0]], track_w))
        last = path[-1]; lx, ly = ox+(last[1]+0.5)*router.G, oy+(last[2]+0.5)*router.G
        if math.hypot(lx-x2, ly-y2) > 0.02:
            segs.append((lx, ly, x2, y2, lids[last[0]], track_w))

        sgs, rgs = geomir.extract_objects(b, lids)
        rules = {'clearance': 0.15, 'clearance_smd': 0.0375}
        for (px1, py1, px2, py2, lid, w) in segs:
            li = lids.index(lid) if lid in lids else 0
            ok, _ = geomir.verify_polyline([(px1,py1),(px2,py2)], w, nc, sgs[li], rgs[li], rules=rules)
            if not ok: return False
        for (vx, vy) in vias:
            for li in range(len(lids)):
                ok, _ = geomir.verify_polyline([(vx,vy),(vx,vy)], via_d/2, nc, sgs[li], rgs[li], rules=rules)
                if not ok: return False
        return True

    def mark_occ(path, nc):
        for (li, gx, gy) in path:
            for dx in range(-2, 3):
                for dy in range(-2, 3):
                    if 0 <= gx+dx < router.nx and 0 <= gy+dy < router.ny:
                        occupancy[(li, gx+dx, gy+dy)] = nc

    # Phase 1: Draw ALL routes (no commit gate)
    occupancy = {}
    pair_items = {}
    pair_cells = {}
    routed = 0
    for nn, pads in sorted(net_pads.items()):
        if len(pads) < 2: continue
        net = b.GetNetInfo().GetNetItem(nn)
        if net is None: continue
        nc = net.GetNetCode()
        for i, j in mst_pairs(pads):
            x1, y1, _ = pads[i]; x2, y2, _ = pads[j]
            pk = (nn, round(x1,2), round(y1,2), round(x2,2), round(y2,2))
            if pk in pair_items: continue
            free = router.build_free(nc, occupancy=occupancy)
            sgx, sgy = int(x1/router.G), int(y1/router.G)
            dgx, dgy = int(x2/router.G), int(y2/router.G)
            if not (0 <= sgx < router.nx and 0 <= sgy < router.ny): continue
            if not (0 <= dgx < router.nx and 0 <= dgy < router.ny): continue
            for li in range(router.nl):
                free[li][sgx][sgy] = True
                free[li][dgx][dgy] = True
            T = router.solve(free, [(0, dgx, dgy)])
            path = router.extract_path(T, free, (0, sgx, sgy))
            if not path or len(path) < 2: continue
            items = draw_path(path, nc, net, x1, y1, x2, y2)
            pair_items[pk] = items; pair_cells[pk] = list(path)
            mark_occ(path, nc)
            routed += 1

    print(f'Phase 1: drawn {routed} pairs')

    # Phase 2: Detect collisions
    sgs, rgs = geomir.extract_objects(b, lids)
    rules = {'clearance': 0.15, 'clearance_smd': 0.0375}
    collisions = []
    all_tracks = [t for t in b.GetTracks() if type(t).__name__ != 'PCB_VIA']
    for t in all_tracks:
        nc = t.GetNetCode(); s, e = t.GetStart(), t.GetEnd()
        x1, y1 = s.x/MM, s.y/MM; x2, y2 = e.x/MM, e.y/MM
        w = t.GetWidth()/MM; lid = t.GetLayer()
        li = lids.index(lid) if lid in lids else 0
        ok, _ = geomir.verify_polyline([(x1,y1),(x2,y2)], w, nc, sgs[li], rgs[li], rules=rules)
        if not ok: collisions.append(t)
    print(f'Phase 2: {len(collisions)} collisions')

    # Phase 3: Rip up colliding pairs + strong congestion
    colliding_data = []
    for t in collisions:
        s, e = t.GetStart(), t.GetEnd()
        colliding_data.append((s.x/MM, s.y/MM, e.x/MM, e.y/MM, t.GetLayer(), t.GetNetCode()))

    pairs_to_ripup = set()
    for pk, items in pair_items.items():
        for item in items:
            try:
                s, e = item.GetStart(), item.GetEnd()
                ix1, iy1 = s.x/MM, s.y/MM; ix2, iy2 = e.x/MM, e.y/MM
                for ct in colliding_data:
                    if (abs(ix1-ct[0])<0.01 and abs(iy1-ct[1])<0.01 and
                        abs(ix2-ct[2])<0.01 and abs(iy2-ct[3])<0.01):
                        pairs_to_ripup.add(pk); break
            except: pass

    # Strong congestion cost
    congestion = {}
    for t in collisions:
        s, e = t.GetStart(), t.GetEnd()
        x1, y1 = s.x/MM, s.y/MM; x2, y2 = e.x/MM, e.y/MM
        li = lids.index(t.GetLayer()) if t.GetLayer() in lids else 0
        n = max(2, int(math.hypot(x2-x1, y2-y1) / rg.GRID))
        for k in range(n+1):
            x = x1+(x2-x1)*k/n; y = y1+(y2-y1)*k/n
            gx, gy = int(x/rg.GRID), int(y/rg.GRID)
            for dx in range(-2, 3):
                for dy in range(-2, 3):
                    key = (li, gx+dx, gy+dy)
                    congestion[key] = congestion.get(key, 0.0) + 10.0

    # Remove colliding pairs
    for pk in pairs_to_ripup:
        for item in pair_items.get(pk, []):
            try: b.Remove(item)
            except: pass
        pair_items.pop(pk, None)
        cells = pair_cells.pop(pk, None)
        if cells:
            for (li, gx, gy) in cells:
                for dx in range(-2, 3):
                    for dy in range(-2, 3):
                        occupancy.pop((li, gx+dx, gy+dy), None)

    print(f'Phase 3: ripped {len(pairs_to_ripup)} pairs')

    # Save + reload for clean SWIG
    tmp = str(input_pcb).replace('.kicad_pcb', '_tmp.kicad_pcb')
    b.Save(tmp)
    b = pcbnew.LoadBoard(tmp)
    b.BuildConnectivity()
    gm = rg.GridModel(b, lids); router.gm = gm; router.b = b

    # Phase 4: Re-route ripped pairs with congestion cost + commit gate
    rerouted = 0
    for pk in list(pairs_to_ripup):
        if time.time() - t0 > timeout_s: break
        nn = pk[0]
        net = b.GetNetInfo().GetNetItem(nn)
        if net is None: continue
        nc = net.GetNetCode()
        x1, y1, x2, y2 = pk[1], pk[2], pk[3], pk[4]

        free = router.build_free(nc, occupancy=occupancy)
        cost = [[[1.0]*router.ny for _ in range(router.nx)] for _ in range(router.nl)]
        for (li, gx, gy), penalty in congestion.items():
            if 0 <= li < router.nl and 0 <= gx < router.nx and 0 <= gy < router.ny:
                cost[li][gx][gy] = 1.0 + penalty

        sgx, sgy = int(x1/router.G), int(y1/router.G)
        dgx, dgy = int(x2/router.G), int(y2/router.G)
        if not (0 <= sgx < router.nx and 0 <= sgy < router.ny): continue
        if not (0 <= dgx < router.nx and 0 <= dgy < router.ny): continue
        for li in range(router.nl):
            free[li][sgx][sgy] = True
            free[li][dgx][dgy] = True

        # Cost-aware solve
        import numpy as np
        try:
            import warp as wp
            from fluxpcb_ncer import _clear_k, _fim_cost_k
            T_np = np.full((router.nl, router.nx, router.ny), 1e18, dtype=np.float32)
            free_np = np.array(free, dtype=np.int32)
            cost_np = np.array(cost, dtype=np.float32)
            T_np[0, dgx, dgy] = 0.0
            T_gpu = wp.array(T_np, dtype=wp.float32)
            free_gpu = wp.array(free_np, dtype=wp.int32)
            cost_gpu = wp.array(cost_np, dtype=wp.float32)
            active_gpu = wp.zeros((router.nl, router.nx, router.ny), dtype=wp.int32)
            new_active_gpu = wp.zeros((router.nl, router.nx, router.ny), dtype=wp.int32)
            active_gpu[0, dgx, dgy] = 1
            for ddx in range(-1, 2):
                for ddy in range(-1, 2):
                    nx2, ny2 = dgx+ddx, dgy+ddy
                    if 0 <= nx2 < router.nx and 0 <= ny2 < router.ny:
                        active_gpu[0, nx2, ny2] = 1
            for iteration in range(500):
                wp.launch(_clear_k, (router.nl, router.nx, router.ny), [new_active_gpu], device=wp.get_device())
                wp.launch(_fim_cost_k, (router.nl, router.nx, router.ny),
                           [T_gpu, free_gpu, cost_gpu, active_gpu, new_active_gpu,
                            router.nl, router.nx, router.ny, router.G, 6.0], device=wp.get_device())
                active_gpu, new_active_gpu = new_active_gpu, active_gpu
                if iteration % 20 == 0:
                    if int(active_gpu.numpy().sum()) == 0: break
            T = T_gpu.numpy()
        except Exception:
            T = router.solve(free, [(0, dgx, dgy)])

        path = router.extract_path(T, free, (0, sgx, sgy))
        if not path or len(path) < 2: continue

        # Commit gate
        if not verify_path(path, nc, x1, y1, x2, y2):
            continue

        items = draw_path(path, nc, net, x1, y1, x2, y2)
        pair_items[pk] = items; pair_cells[pk] = list(path)
        mark_occ(path, nc)
        rerouted += 1

    print(f'Phase 4: rerouted {rerouted} pairs')
    print(f'Total: {len(pair_items)} pairs')
    b.Save(str(output_pcb))
    return {'routed': routed, 'ripped': len(pairs_to_ripup), 'rerouted': rerouted, 'total': len(pair_items)}

if __name__ == '__main__':
    input_pcb = sys.argv[1]
    output_pcb = sys.argv[2]
    timeout_s = int(sys.argv[3]) if len(sys.argv) > 3 else 500
    result = run(input_pcb, output_pcb, timeout_s)
    print(json.dumps(result))
