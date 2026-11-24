"""ncer_board driver: negotiated-congestion route-all via the NCER engine."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path('E:/DATA/vscode/ZCODE/autorouter-bench/tools')))

import pcbnew  # noqa: E402

from fluxpcb.router.ncer import NCERRouter  # noqa: E402
from fluxpcb.tools.call import emit_line  # noqa: E402


def main():
    board_path, args_path = sys.argv[1], sys.argv[2]
    args = json.load(open(args_path, encoding='utf-8'))
    board = pcbnew.LoadBoard(board_path)
    lids = [int(l) for l in (args.get('layers') or [0, 2])]
    router = NCERRouter(board, lids)
    net_pads = {}
    for fp in board.GetFootprints():
        for pad in fp.Pads():
            name = pad.GetNetname()
            if name:
                net_pads.setdefault(name, []).append(pad)
    try:
        result = router.run(net_pads,
                            timeout_s=int(args.get('timeout_s', 1200)),
                            corridors=args.get('corridors'))
        emit_line({'ok': True, 'result': result if isinstance(result, dict)
                   else {'reported': str(result)[:200]}})
    except Exception as e:
        emit_line({'ok': False, 'error': f'{type(e).__name__}: {e}'})


if __name__ == '__main__':
    main()
