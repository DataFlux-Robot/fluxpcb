"""True connectivity audit + ratsnest pair extraction.

Runs inside KiCad's Python (same pattern as route_embedded/shorts_cleanup).
Two modes:

  conn_audit.py PCB              -> gate JSON (ratsnest count, net stats)
  conn_audit.py --pairs PCB OUT  -> same JSON + writes route pairs file

Closes the "0 unconnected pads" illusion: KiCad DRC excludes ignored
severities from the report, so `unconnected_items=ignore` made boards with
dozens of open nets report zero. The ratsnest API (GetUnconnectedCount)
cannot be calibrated away.

Pair extraction replaces DRC-report parsing (which saw nothing once the
severity was ignored). Per net, pads are grouped into connectivity islands
with union-find over stable POSITION keys: pcbnew's GetConnectedItems
returns fresh SWIG wrappers on every call (id()/m_Uuid are NOT stable —
naive BFS loops forever), so each result item contributes only its anchor
positions (pad pos, track endpoints, via pos) as union-find nodes.
Islands are then chained Prim-style: k-1 edges per net, matching the
ratsnest MST topology. Each edge becomes `net|x1|y1|l1|x2|y2|l2` — the
route_embedded.py pair format (l: 0=F.Cu, 1=B.Cu).
"""
import json
import sys
import pcbnew

MM = 1000000


def _anchors_of(item):
    """Stable position keys for any connectivity result item."""
    t = type(item).__name__
    out = []
    if t == "PAD":
        p = item.GetPosition()
        out.append((p.x, p.y))
    elif t == "PCB_VIA":
        p = item.GetPosition()
        out.append((p.x, p.y))
    elif t == "PCB_TRACK":
        s, e = item.GetStart(), item.GetEnd()
        out.append((s.x, s.y))
        out.append((e.x, e.y))
    return out          # ZONE: region, not a point — its pads come directly


class _UF:
    def __init__(self):
        self.p = {}

    def find(self, x):
        self.p.setdefault(x, x)
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[ra] = rb


def _pad_anchor(pad):
    p = pad.GetPosition()
    # route_embedded convention: 0=F.Cu, 1=B.Cu; THT spans both -> 0
    lay = 1 if (pad.IsOnLayer(pcbnew.B_Cu) and
                not pad.IsOnLayer(pcbnew.F_Cu)) else 0
    return (p.x / MM, p.y / MM, lay)


def net_island_pads(conn, pads):
    """Group a net's pads into islands -> list of pad lists."""
    uf = _UF()
    pad_keys = set()
    for pad in pads:
        k = _anchors_of(pad)[0]
        pad_keys.add(k)
        uf.find(k)
    for pad in pads:
        k = _anchors_of(pad)[0]
        try:
            results = conn.GetConnectedItems(pad)
        except (AttributeError, TypeError):
            continue
        for it in results:
            try:
                if it.GetNetCode() != pad.GetNetCode():
                    continue
            except AttributeError:
                continue
            for a in _anchors_of(it):
                uf.union(k, a)
    islands = {}
    for pad in pads:
        k = _anchors_of(pad)[0]
        islands.setdefault(uf.find(k), []).append(pad)
    return list(islands.values())


def island_pairs(net_name, islands):
    """Prim-chain islands: k-1 edges, each the shortest pad pair bridging two
    islands — same topology count as the ratsnest MST."""
    k = len(islands)
    if k < 2:
        return []
    anchors = [[_pad_anchor(p) for p in isl] for isl in islands]
    pairs = []
    inside = {0}
    while len(inside) < k:
        best = None   # (dist2, ia, ib, pa, pb)
        for ia in inside:
            for ib in range(k):
                if ib in inside:
                    continue
                for pa in anchors[ia]:
                    for pb in anchors[ib]:
                        d = (pa[0] - pb[0]) ** 2 + (pa[1] - pb[1]) ** 2
                        if best is None or d < best[0]:
                            best = (d, ia, ib, pa, pb)
        _, ia, ib, pa, pb = best
        pairs.append(f"{net_name}|{pa[0]:.4f}|{pa[1]:.4f}|{pa[2]}"
                     f"|{pb[0]:.4f}|{pb[1]:.4f}|{pb[2]}")
        inside.add(ib)
    return pairs


def main():
    argv = sys.argv[1:]
    pairs_mode = "--pairs" in argv
    args = [a for a in argv if not a.startswith("--")]
    if pairs_mode:
        pcb, out_file = (args + [None, None])[:2]
    else:
        pcb = args[0]

    b = pcbnew.LoadBoard(pcb)
    b.BuildConnectivity()
    c = b.GetConnectivity()

    ratsnest = c.GetUnconnectedCount(True)   # visible-only, uncalibratable

    tracks_nets = set(t.GetNetname() for t in b.GetTracks())
    pads_without_tracks = set()
    pads_by_net = {}
    for fp in b.GetFootprints():
        for pad in fp.Pads():
            nn = pad.GetNetname()
            if not nn:
                continue
            nc = pad.GetNetCode()
            if nc:
                pads_by_net.setdefault(nc, []).append(pad)
            if nn not in tracks_nets:
                pads_without_tracks.add(nn)

    all_pairs = []
    islands_summary = {}
    for nc, pads in pads_by_net.items():
        if nc == 0:
            continue
        name = pads[0].GetNetname()
        islands = net_island_pads(c, pads)
        if len(islands) > 1:
            islands_summary[name] = len(islands)
            all_pairs.extend(island_pairs(name, islands))

    total = sum(1 for n in b.GetNetInfo().NetsByNetcode().keys() if n != 0)

    result = {
        "unconnected_ratsnest": ratsnest,
        "nets_without_tracks": len(pads_without_tracks),
        "missing_list": sorted(pads_without_tracks)[:15],
        "total_nets": total,
        "tracks": len(list(b.GetTracks())),
        "fragmented_nets": islands_summary,
        "derived_pairs": len(all_pairs),
    }
    if pairs_mode and out_file:
        with open(out_file, "w") as f:
            f.write("\n".join(all_pairs))
    print(json.dumps(result))


if __name__ == "__main__":
    main()
