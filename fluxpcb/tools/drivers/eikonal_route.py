"""eikonal_route driver: wavefront route between anchors (CPU fallback)."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'fluxpcb' / 'kicadgen'))

import pcbnew  # noqa: E402

from fluxpcb.kicadgen import routegrid as rg  # noqa: E402
from fluxpcb.kicadgen.gpu_eikonal import (GPUEikonalRouter,  # noqa: E402
                                          route_and_draw)
from fluxpcb.tools.call import emit_line  # noqa: E402


def main():
    board_path, args_path = sys.argv[1], sys.argv[2]
    args = json.load(open(args_path, encoding='utf-8'))
    board = pcbnew.LoadBoard(board_path)
    net = board.FindNet(args['net'])
    if net is None:
        emit_line({'ok': False, 'error': f"net {args['net']} missing"})
        return
    layers = rg.all_copper_ids(board)
    try:
        router = GPUEikonalRouter(board, layers)
        path = router.route_net(
            net.GetNetCode(), (args['ax'], args['ay']),
            (args['bx'], args['by']),
            src_layer=args.get('src_layer', 'F.Cu'),
            dst_layer=args.get('dst_layer', 'F.Cu'))
    except Exception as e:
        emit_line({'ok': False, 'error': f'{type(e).__name__}: {e}'})
        return
    if path is None:
        emit_line({'ok': False, 'result': {'path': None}})
        return
    if args.get('commit'):
        route_and_draw(board, layers, net.GetNetCode(), args['net'],
                       (args['ax'], args['ay']), (args['bx'], args['by']),
                       router)
        pcbnew.SaveBoard(board_path, board)
    emit_line({'ok': True, 'result': {'points': len(path)}})


if __name__ == '__main__':
    main()
