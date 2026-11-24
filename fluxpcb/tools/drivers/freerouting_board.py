"""freerouting_board driver: the full router, every option exposed."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from fluxpcb.router.freerouting import FreeroutingBackend  # noqa: E402
from fluxpcb.tools.call import emit_line  # noqa: E402


def main():
    board_path, args_path = sys.argv[1], sys.argv[2]
    args = json.load(open(args_path, encoding='utf-8'))
    out = args.get('out') or (str(board_path) + '.routed.kicad_pcb')
    backend = FreeroutingBackend(
        threads=int(args.get('threads', 2)),
        max_passes=int(args.get('max_passes', 15)))
    if args.get('jar'):
        backend.jar = args['jar']
    fr = backend.route(
        board_path, out,
        timeout_s=int(args.get('timeout_s', 300)),
        use_board_rules=bool(args.get('use_board_rules', True)),
        keep_nets=args.get('keep_nets') or None,
        rip_nets=args.get('rip_nets') or None)
    emit_line({'ok': bool(fr.routed), 'result': {
        'routed': fr.routed, 'seconds': fr.seconds,
        'passes': fr.passes, 'error': fr.error,
        'timed_out': fr.timed_out, 'kept_locked': fr.kept_locked,
        'rule_congruence': fr.rule_congruence, 'out': out}})


if __name__ == '__main__':
    main()
