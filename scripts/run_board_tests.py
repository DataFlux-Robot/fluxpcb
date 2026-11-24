"""The board-level tests: real fixtures, pcbnew and kicad-cli.

    python scripts/run_board_tests.py [pytest arguments]

Runs ``pytest -m board`` under the interpreter it was started with.  On
Windows that has to be KiCad's own Python: it is the one with pcbnew,
and kicad-cli is looked up beside it.  The board fixtures are not in
git (``fluxpcb/tests/boards_local.py`` says where they live); when they
are not installed every one of these tests is SKIPPED, never passed.
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main(argv=None):
    try:
        import pcbnew  # noqa: F401
    except ImportError:
        print('this interpreter has no pcbnew; start the script with '
              'KiCad\'s python.exe', file=sys.stderr)
        return 2
    cmd = [sys.executable, '-m', 'pytest', os.path.join(ROOT, 'fluxpcb', 'tests'),
           '-m', 'board', '-q', '-p', 'no:cacheprovider', *(argv or sys.argv[1:])]
    print(' '.join(cmd), flush=True)
    return subprocess.call(cmd, cwd=ROOT)


if __name__ == '__main__':
    sys.exit(main())
