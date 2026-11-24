"""explore_route driver: visibility-graph A* between two anchors.

route_explore.py builds its graph at import from sys.argv; the driver sets
argv, imports it, and calls astar_explore for the requested pair.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))                      # for fluxpcb.*
sys.path.insert(0, str(ROOT / 'fluxpcb' / 'kicadgen'))

board_path, args_path = sys.argv[1], sys.argv[2]
args = json.load(open(args_path, encoding='utf-8'))

sys.argv = ['route_explore.py', str(board_path), '', '1']
import route_explore as re  # noqa: E402
from fluxpcb.tools.call import emit_line  # noqa: E402

net_name = args['net']
board = re.board
net = board.FindNet(net_name)
if net is None:
    emit_line({'ok': False, 'error': f'net {net_name} missing'})
    sys.exit(0)
try:
    path = re.astar_explore(
        (args['ax'], args['ay'], int(args.get('al', 0))),
        (args['bx'], args['by'], int(args.get('bl', 0))),
        net.GetNetCode(), float(args.get('cls_weight', 1.0)), net_name)
    emit_line({'ok': path is not None,
               'result': {'path_cells': len(path) if path else 0}})
except Exception as e:
    emit_line({'ok': False, 'error': f'{type(e).__name__}: {e}'})
