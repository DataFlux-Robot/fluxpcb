"""maze_route driver: Grid A* between two anchors with exact re-verification.

Reads board + args, plans with kicadgen/maze over a GridModel, verifies
every segment with geomir (exact) and every layer hop with via_ok, and
either emits tracks/vias onto the board or returns the plan.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'kicadgen'))

import pcbnew  # noqa: E402

from fluxpcb.kicadgen import geomir, maze, routegrid as rg  # noqa: E402
from fluxpcb.kernel.rules import load_rules  # noqa: E402
from fluxpcb.tools.call import emit_line  # noqa: E402


def main():
    board_path, args_path = sys.argv[1], sys.argv[2]
    args = json.load(open(args_path, encoding='utf-8'))
    board = pcbnew.LoadBoard(board_path)
    if board is None:
        emit_line({'ok': False, 'error': 'board load failed'})
        return
    layers = rg.signal_layer_ids(board)
    rt = load_rules(Path(board_path))
    via_dia = float(rt.get('via_dia') or rt.get('via_diameter') or 0.8)
    try:
        via_dia = max(via_dia, board.GetDesignSettings().m_ViasMinSize / 1e6)
    except Exception:
        pass
    net = board.FindNet(args['net'])
    if net is None:
        emit_line({'ok': False, 'error': f"net {args['net']} missing"})
        return
    nc = net.GetNetCode()
    gm = rg.GridModel(board, layers)
    width = float(args.get('width', 0.2))
    path = maze.plan_maze(
        gm, (args['ax'], args['ay'], layers[args.get('al', 0)]),
        (args['bx'], args['by'], layers[args.get('bl', 0)]), nc,
        max_expand=int(args.get('max_expand', 300000)))
    if not path:
        emit_line({'ok': False, 'error': 'no grid path',
                   'result': {'grid_paths': 0}})
        return
    pts = maze.merge_path(path)
    segs, rects = geomir.extract_objects(board, layers)
    items, ok = [], True
    for p, q in zip(pts, pts[1:]):
        if p[0] != q[0]:
            vok, blk = geomir.via_ok(p[1], p[2], nc, segs, rects,
                                     via_r=via_dia / 2.0, rules=rt)
            if not vok:
                emit_line({'ok': False, 'error': 'via rejected',
                           'result': {'blocker': blk}})
                return
            if args.get('commit'):
                items.append(rg.add_via(board, p[1], p[2], net))
            continue
        vok, blk = geomir.verify_polyline(
            [(p[1], p[2]), (q[1], q[2])], width, nc,
            segs[p[0]], rects[p[0]], rt)
        if not vok:
            emit_line({'ok': False, 'error': 'segment rejected',
                       'result': {'blocker': blk}})
            return
        if args.get('commit'):
            items.append(rg.add_track(board, p[1], p[2], q[1], q[2],
                                      layers[p[0]], net, width))
    if args.get('commit'):
        pcbnew.SaveBoard(board_path, board)
    emit_line({'ok': True, 'result': {'pts': pts, 'width': width,
                                      'committed': len(
                                          [i for i in items if i])}})


if __name__ == '__main__':
    main()
