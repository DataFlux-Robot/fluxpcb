"""Taking items off a board without corrupting the interpreter.

Measured on KiCad 10.0.6 (probes of 2026-11-25), one track is enough:

    item that came FROM THE FILE   Remove()  wrapper released   -> heap corrupted
    item created in Python         Add, Remove, released         -> fine
    item from the file             Delete()  wrapper released   -> fine
    item from the file             Remove()  wrapper kept alive  -> fine

BOARD::Remove detaches the item and sets ``thisown=1``, so the Python
wrapper deletes the C++ object when it is released — and for an item
the board loaded itself that release corrupts the heap: the next
``pcbnew.LoadBoard`` returns a raw ``SwigPyObject`` and the run dies on
``'SwigPyObject' object has no attribute 'Zones'``.  It was seen for
months as "SWIG wrappers become invalid after a net-level rip-up" and
worked around by doing rip-ups in a subprocess.

``Delete`` lets the board delete the item and detaches the wrapper
(``item.this = None``): a later use of the wrapper raises instead of
reading freed memory.

Rules:
  * an item that is never coming back → ``discard``;
  * an item that will be put back → ``Remove`` and KEEP the wrapper
    alive until ``Add`` (``Add`` sets ``thisown=0``);
  * items created in Python are not affected.
"""
from __future__ import annotations


def discard(board, item):
    """Take `item` off `board` for good.  Do not use `item` afterwards."""
    dele = getattr(board, 'Delete', None)
    if dele is not None:
        dele(item)
    else:
        board.Remove(item)
        _PARKED.append(item)            # never released inside the run


_PARKED: list = []


def lift(board, item):
    """Take `item` off `board` to put it back later; returns a handle
    that must stay alive until ``restore``."""
    board.Remove(item)
    return item


def restore(board, handle):
    """Put a lifted item back."""
    board.Add(handle)
