"""Which board files a gate or a stage should judge.

`pcb/` accumulates the outputs of earlier runs: `*_p4_routed`, `placed`,
`unrouted`, panels, `.ckpt` snapshots.  They are inputs of nothing, but a
plain `rglob("*.kicad_pcb")` makes every gate audit them too — on the H743
project that turned one board into "4 块板未全连通" and "4 块板网表不符",
and made the pcb stage re-route four boards (≈4x the wall clock).

    is_backup(path) -> bool
    gate_boards(project_dir, subdir='pcb') -> [Path]   canonical boards
    canonical(project_dir, project_name) -> ([Path], [Path])  (kept, skipped)
"""
from __future__ import annotations

from pathlib import Path

BACKUP_TAGS = ('_p4_routed', '_panel', '.ckpt', '_routed_')
BACKUP_PREFIXES = ('placed', 'unrouted')


def is_backup(path) -> bool:
    name = Path(path).name
    if any(t in name for t in BACKUP_TAGS):
        return True
    return name.startswith(BACKUP_PREFIXES)


def gate_boards(project_dir, subdir: str = 'pcb') -> list:
    """Boards a gate judges: everything except earlier runs' artefacts."""
    root = Path(project_dir) / subdir
    if not root.exists():
        return []
    boards = sorted(root.rglob('*.kicad_pcb'))
    keep = [p for p in boards if not is_backup(p)]
    return keep or boards


def canonical(project_dir, project_name: str) -> tuple[list, list]:
    """(the board this run owns, the backups it must not touch)."""
    root = Path(project_dir) / 'pcb'
    boards = sorted(root.rglob('*.kicad_pcb')) if root.exists() else []
    kept = [p for p in boards if not is_backup(p)]
    skipped = [p for p in boards if is_backup(p)]
    named = [p for p in kept if p.stem == project_name]
    if named:
        return named, skipped + [p for p in kept if p not in named]
    return kept[:1], skipped + kept[1:]
