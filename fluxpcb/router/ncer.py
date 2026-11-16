#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ncer — NCER Router wrapper (Component 9).

Wraps the NCERRouter from autorouter-bench with a clean interface.
Removes the cross-repo hardcoded sys.path dependency.

The actual NCERRouter implementation lives at:
  E:/DATA/vscode/ZCODE/autorouter-bench/tools/fluxpcb_ncer.py

This wrapper provides:
  1. Clean import mechanism (no hardcoded paths)
  2. Unified interface with the contracts
  3. Fallback to local vendored copy if available
"""
from __future__ import annotations

import os
import sys
from typing import Any, Optional


def _find_ncer_module():
    """Find the NCERRouter module, trying multiple locations."""
    # 1. Try local vendored copy
    local_path = os.path.join(os.path.dirname(__file__), 'ncer_impl.py')
    if os.path.exists(local_path):
        return local_path

    # 2. Try autorouter-bench location (env override or default)
    ncer_path = os.environ.get('FLUXPCB_NCER_PATH')
    if ncer_path and os.path.exists(ncer_path):
        return ncer_path

    # 3. Default autorouter-bench location
    default_path = os.path.join(
        os.path.dirname(__file__), '..', '..', '..', '..',
        'autorouter-bench', 'tools', 'fluxpcb_ncer.py'
    )
    if os.path.exists(default_path):
        return default_path

    # 4. Try absolute path as last resort
    abs_path = 'E:/DATA/vscode/ZCODE/autorouter-bench/tools/fluxpcb_ncer.py'
    if os.path.exists(abs_path):
        return abs_path

    raise ImportError(
        "Cannot find NCERRouter implementation. "
        "Set FLUXPCB_NCER_PATH or place ncer_impl.py in the router directory."
    )


def _load_ncer_class():
    """Load the NCERRouter class from the found module."""
    ncer_path = _find_ncer_module()
    ncer_dir = os.path.dirname(os.path.abspath(ncer_path))

    # Add to sys.path temporarily
    if ncer_dir not in sys.path:
        sys.path.insert(0, ncer_dir)

    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("fluxpcb_ncer", ncer_path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.NCERRouter
    finally:
        if ncer_dir in sys.path:
            sys.path.remove(ncer_dir)


# Lazy-loaded NCERRouter class
_NCEER_CLASS = None


def NCERRouter(board: Any, lids: list[int]):
    """Create an NCERRouter instance.

    Args:
        board: pcbnew.BOARD
        lids: List of copper layer IDs

    Returns:
        NCERRouter instance
    """
    global _NCEER_CLASS
    if _NCEER_CLASS is None:
        _NCEER_CLASS = _load_ncer_class()
    return _NCEER_CLASS(board, lids)
