#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""freerouting — Freerouting adapter (Component 9 backend).

Integrates Freerouting (Java) as a routing backend. Flow:
    KiCad .kicad_pcb → .dsn (Specctra export) → java -jar freerouting
    → .ses (session) → import back → .kicad_pcb

Requires:
    - java (JDK 25 for freerouting 2.4.1+, class file 69)
    - freerouting.jar
    - KiCad's pcbnew Python for DSN/SES conversion
      (via scripts/kicad_board.py in autorouter-bench, or pcbnew directly)

SKILL.md guidance:
    - "Freerouting 1.9" for per-layer width compliance (2.x ignored by skill)
    - BUT: benchmark evidence shows 2.4.1 gives 100% completion on 2-layer
      boards; use it for 2L and fall back to 1.9/NCER for multi-layer
    - "现有铜设为 protect；非工作集网放进 LOCKED 类；板旁放 .kicad_pro"
    - "任何自动结果都要先重灌铺铜，再用 kicad-cli pcb drc 复核"

Usage:
    from fluxpcb.router.freerouting import FreeroutingBackend
    backend = FreeroutingBackend()
    result = backend.route(board_path, out_path, timeout_s=300)
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Any, Optional


# ──────────────────────────────────────────────────────────────────────
# Default tool locations (searched in order)
# ──────────────────────────────────────────────────────────────────────

JAVA_CANDIDATES = [
    os.environ.get('FLUXPCB_JAVA', ''),
    r'E:/DATA/vscode/ZCODE/flux-neurotransmitter/tools_bin/jdk25/bin/java.exe',
    'java',
]

JAR_CANDIDATES = [
    os.environ.get('FLUXPCB_FREEROUTING_JAR', ''),
    r'E:/DATA/vscode/ZCODE/flux-neurotransmitter/tools_bin/freerouting.jar',
    # autorouter-bench default (ZCODE/autorouter-bench/tools/)
    os.path.join(os.path.dirname(__file__), '..', '..', '..',
                 'autorouter-bench', 'tools', 'freerouting-2.5.0.jar'),
]

KICAD_HELPER_CANDIDATES = [
    os.environ.get('FLUXPCB_KICAD_HELPER', ''),
    # autorouter-bench's kicad_board.py helper
    # (ZCODE/autorouter-bench/scripts/kicad_board.py)
    os.path.join(os.path.dirname(__file__), '..', '..', '..',
                 'autorouter-bench', 'scripts', 'kicad_board.py'),
    r'E:/DATA/vscode/ZCODE/autorouter-bench/scripts/kicad_board.py',
]


def _first_existing(candidates: list[str]) -> Optional[str]:
    for c in candidates:
        if c and os.path.exists(c):
            return c
    # fall back to PATH-resolved bare command
    for c in candidates:
        if c and not os.path.sep in c:
            return c
    return None


@dataclass
class FreeroutingResult:
    """Result of a Freerouting run."""
    routed: bool = False
    output_path: str = ""
    seconds: float = 0.0
    timed_out: bool = False
    passes: int = 0
    log: str = ""
    error: str = ""


class FreeroutingBackend:
    """Freerouting routing backend (Component 9)."""

    def __init__(self, java: str = None, jar: str = None,
                 kicad_helper: str = None, threads: int = 8,
                 max_passes: int = 15):
        """Initialize the Freerouting backend.

        max_passes bounds the wall clock: Freerouting 2.4.1 keeps iterating
        even after the score plateaus (observed: passes 2-5 stuck at the same
        unrouted count on rp2040), so 100 passes = 30+ minutes with no gain.
        15 passes ≈ 3-5 minutes, enough to reach the plateau on most boards.
        """
        self.java = java or _first_existing(JAVA_CANDIDATES)
        self.jar = jar or _first_existing(JAR_CANDIDATES)
        self.kicad_helper = kicad_helper or _first_existing(KICAD_HELPER_CANDIDATES)
        self.threads = threads
        self.max_passes = max_passes

    def available(self) -> tuple[bool, str]:
        """Check whether the backend can run.

        Returns:
            (available, reason)
        """
        if not self.java:
            return False, 'java not found'
        if not self.jar:
            return False, 'freerouting.jar not found'
        if not self.kicad_helper:
            return False, 'kicad_board.py helper not found'
        return True, 'ok'

    def _kicad(self, *args, timeout: int = 300):
        """Run the kicad_board.py helper (strip/dsn/import-ses)."""
        kicad_py = os.environ.get('KICAD_PYTHON', sys.executable)
        r = subprocess.run(
            [kicad_py, self.kicad_helper, *[str(a) for a in args]],
            capture_output=True, text=True, timeout=timeout,
            encoding='utf-8', errors='replace',
        )
        if r.returncode != 0:
            raise RuntimeError(f'kicad helper {args[0]} failed: {r.stderr[:300]}')
        try:
            return json.loads(r.stdout) if r.stdout.strip() else {}
        except json.JSONDecodeError:
            return {'raw': r.stdout}

    def route(self, board_path: str, out_path: str,
              timeout_s: int = 300, keep_copper_protected: bool = True) -> FreeroutingResult:
        """Route a board with Freerouting.

        Args:
            board_path: Input .kicad_pcb (should be the UNROUTED board)
            out_path: Output .kicad_pcb (routed)
            timeout_s: Freerouting wall-clock cap
            keep_copper_protected: keep existing copper as protect (default)

        Returns:
            FreeroutingResult
        """
        t0 = time.time()
        result = FreeroutingResult(output_path=out_path)

        ok, reason = self.available()
        if not ok:
            result.error = f'backend unavailable: {reason}'
            return result

        work_dir = os.path.dirname(os.path.abspath(out_path)) or '.'
        os.makedirs(work_dir, exist_ok=True)

        # 1. Export DSN (Specctra) via 'strip' (idempotent on already-unrouted
        #    boards; writes <work_dir>/board.dsn + <work_dir>/unrouted.kicad_pcb)
        try:
            info = self._kicad('strip', board_path, work_dir, timeout=120)
            dsn_path = os.path.join(work_dir, 'board.dsn')
            unrouted_path = os.path.join(work_dir, 'unrouted.kicad_pcb')
            if not os.path.exists(dsn_path):
                result.error = f'DSN not produced by strip (got {info})'
                return result
            # Use the freshly stripped board as the import base (matches DSN)
            if os.path.exists(unrouted_path):
                board_path = unrouted_path
        except Exception as e:
            result.error = f'DSN export failed: {e}'
            return result

        # 2. Run Freerouting
        ses_path = os.path.join(work_dir, 'board.ses')
        log_path = os.path.join(work_dir, 'freerouting.log')
        cmd = [
            self.java, '-Djava.awt.headless=true', '-jar', self.jar,
            '-de', dsn_path, '-do', ses_path,
            '-mp', str(self.max_passes), '-mt', str(self.threads),
        ]
        try:
            with open(log_path, 'w', encoding='utf-8', errors='replace') as logf:
                r = subprocess.run(
                    cmd, capture_output=True, text=True,
                    timeout=timeout_s + 60,
                    encoding='utf-8', errors='replace',
                )
                logf.write(r.stdout or '')
                logf.write(r.stderr or '')
        except subprocess.TimeoutExpired:
            result.timed_out = True
            result.seconds = round(time.time() - t0, 1)
            result.error = 'freerouting timeout'
            return result

        result.log = log_path

        # Parse pass count from log
        try:
            with open(log_path, encoding='utf-8', errors='replace') as f:
                for line in f:
                    if 'Auto-routing pass #' in line:
                        import re
                        m = re.search(r'pass #(\d+)', line)
                        if m:
                            result.passes = max(result.passes, int(m.group(1)))
        except Exception:
            pass

        if not os.path.exists(ses_path):
            result.error = 'no .ses produced'
            result.seconds = round(time.time() - t0, 1)
            return result

        # 3. Import SES back into KiCad
        try:
            self._kicad('import-ses', board_path, ses_path, out_path, timeout=180)
        except Exception as e:
            result.error = f'SES import failed: {e}'
            result.seconds = round(time.time() - t0, 1)
            return result

        result.routed = os.path.exists(out_path)
        result.seconds = round(time.time() - t0, 1)
        return result


def route_with_freerouting(board_path: str, out_path: str,
                           timeout_s: int = 300) -> FreeroutingResult:
    """Convenience function."""
    return FreeroutingBackend().route(board_path, out_path, timeout_s)
