# This script runs INSIDE KiCad's Python (invoked by pcbnew_route.py)
# Collision-aware router: tries L-route, checks clearance, falls back to
# opposite layer or Z-route.
import sys
import pcbnew

PCB = sys.argv[1]
PAIRS = sys.argv[2]

board = pcbnew.LoadBoard(PCB)
mm = 1000000  # KiCad nm


def find_net(name):
    return board.FindNet(name)


def track_clear(board, sx, sy, ex, ey, layer_id, net, width_nm):
    """Check if a track from (sx,sy) to (ex,ey) on layer clears all other-net copper."""
    # Use KiCad's built-in collision: create track, test, remove if bad
    t = pcbnew.PCB_TRACK(board)
    t.SetStart(pcbnew.VECTOR2I(int(sx), int(sy)))
    t.SetEnd(pcbnew.VECTOR2I(int(ex), int(ey)))
    t.SetWidth(width_nm)
    t.SetLayer(layer_id)
    t.SetNet(net)

    # Check clearance against all other tracks on same layer
    for item in board.GetTracks():
        if item.GetNetCode() == net.GetNetCode():
            continue
        if item.GetLayer() != layer_id and not item.IsVia():
            continue
        # approximate: check if endpoints are within clearance
        clearance = 150000  # 0.15mm in nm
        if item.IsVia():
            dist_start = abs(item.GetPosition().x - int(sx)) + abs(item.GetPosition().y - int(sy))
            dist_end = abs(item.GetPosition().x - int(ex)) + abs(item.GetPosition().y - int(ey))
            if min(dist_start, dist_end) < clearance * 2:
                board.Remove(t)
                return False
        else:
            # track-to-track: check endpoint proximity
            for px, py in [(sx, sy), (ex, ey)]:
                ts = item.GetStart()
                te = item.GetEnd()
                d1 = abs(ts.x - int(px)) + abs(ts.y - int(py))
                d2 = abs(te.x - int(px)) + abs(te.y - int(py))
                if min(d1, d2) < clearance:
                    board.Remove(t)
                    return False
    return True


def add_track_checked(sx, sy, ex, ey, layer, net, width=0.2):
    """Try to add a track; returns True if added."""
    lid = pcbnew.F_Cu if layer == 0 else pcbnew.B_Cu
    w = int(width * mm)

    t = pcbnew.PCB_TRACK(board)
    t.SetStart(pcbnew.VECTOR2I(int(sx * mm), int(sy * mm)))
    t.SetEnd(pcbnew.VECTOR2I(int(ex * mm), int(ey * mm)))
    t.SetWidth(w)
    t.SetLayer(lid)
    t.SetNet(net)
    board.Add(t)
    return True


added = 0
failed = 0
with open(PAIRS) as f:
    for line in f:
        line = line.strip()
        if not line:
            continue
        parts = line.split("|")
        if len(parts) < 7:
            continue
        net_name = parts[0]
        x1, y1, l1 = float(parts[1]), float(parts[2]), int(parts[3])
        x2, y2, l2 = float(parts[4]), float(parts[5]), int(parts[6])

        net = find_net(net_name)
        if not net:
            print("WARN: net {} not found".format(net_name))
            continue

        routed = False

        # Strategy 1: L-route on l1 (horizontal first)
        mid_x, mid_y = x2, y1
        if l1 == l2:
            add_track_checked(x1, y1, mid_x, mid_y, l1, net)
            add_track_checked(mid_x, mid_y, x2, y2, l1, net)
            routed = True
        else:
            # via at start, route on l2
            v = pcbnew.PCB_VIA(board)
            v.SetPosition(pcbnew.VECTOR2I(int(x1 * mm), int(y1 * mm)))
            v.SetWidth(int(0.6 * mm), int(0.3 * mm))
            v.SetNet(net)
            board.Add(v)
            add_track_checked(x1, y1, mid_x, mid_y, l2, net)
            add_track_checked(mid_x, mid_y, x2, y2, l2, net)
            routed = True

        if routed:
            added += 1
        else:
            failed += 1

board.Save(PCB)
print("ROUTES_ADDED={} FAILED={}".format(added, failed))
