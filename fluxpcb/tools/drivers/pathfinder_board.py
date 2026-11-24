"""pathfinder_board driver: whole-board PathFinder (fluxrouter), captured.

fluxrouter.py runs its full loop at import and prints one JSON line; the
driver redirects stdout, imports it with argv set, and re-emits the line
as the tool envelope.
"""
import io
import json
import sys
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'fluxpcb' / 'kicadgen'))

board_path, args_path = sys.argv[1], sys.argv[2]
args = json.load(open(args_path, encoding='utf-8'))

clsfile = ''
if args.get('cls'):
    clsfile = str(Path(args_path).parent / 'cls.json')
    with open(clsfile, 'w', encoding='utf-8') as fh:
        json.dump({'classes': args['cls']}, fh)
sys.argv = ['fluxrouter.py', str(board_path), clsfile,
            str(args.get('max_iters', 20))]

from fluxpcb.tools.call import emit_line  # noqa: E402

buf = io.StringIO()
try:
    with redirect_stdout(buf):
        import fluxrouter  # noqa: E402,F401   # runs the whole loop
    lines = [l.strip() for l in buf.getvalue().splitlines()
             if l.strip().startswith('{')]
    body = json.loads(lines[-1]) if lines else {}
    emit_line({'ok': True, 'result': body})
except Exception as e:
    emit_line({'ok': False, 'error': f'{type(e).__name__}: {e}',
               'result': {'stdout_tail': buf.getvalue()[-400:]}})
