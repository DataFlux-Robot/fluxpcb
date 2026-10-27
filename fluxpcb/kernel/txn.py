# -*- coding: utf-8 -*-
"""Board State Kernel — txn.py: transactions + invariants + manifest.

Facets 2+3 of the kernel. An engine invocation is no longer "run a
subprocess that mutates the board file" — it is a Txn:

    with Txn(kicad_cli, kipy, pcb, "fanout-session", level="light") as t:
        _run_session(...)          # the mutation (any code)

The Txn snapshots the board, runs the body, then checks the invariant
set for its level against the PRE state. A violation rolls the board
back atomically (the half-saved-crash and cleanup-regression classes
die here) and records the attempt in chain_manifest.json — the chain's
replayable, auditable mutation log (checkpoint/resume ride on it).

Invariants (kernel/invariants.py) are the ONLY home for lessons: a
generic lesson becomes a new invariant, never prose alone.
"""
from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

from . import state
from .invariants import check_level, INVARIANT_LEVELS


class Rollback(Exception):
    """Raised at Txn exit when invariants failed and the board was
    restored to the pre-transaction snapshot."""

    def __init__(self, name: str, violations: list):
        self.name = name
        self.violations = violations
        super().__init__(
            f"txn {name} rolled back: {'; '.join(violations)}")


class Txn:
    """One board mutation under contract.

    level: 'light'  — I3 only (opens must not regress >tolerance);
                      ~30s overhead (one conn_audit), for every boundary
          'full'    — + I1 cross-net overlap sweep + I2 clearance floor
                      (one DRC), for the four critical boundaries
    """

    def __init__(self, kicad_cli: str, kipy: str, pcb: Path, name: str,
                 level: str = "light", opens_tolerance: int = 5,
                 log=print):
        self.kicad_cli, self.kipy = kicad_cli, kipy
        self.pcb, self.name = pcb, name
        self.level = level if level in INVARIANT_LEVELS else "light"
        self.tol = opens_tolerance
        self.log = log
        self._snap: Path | None = None
        self.pre: dict = {}
        self.violations: list = []      # outcome flow-back (C2): what
                                        # the invariants caught

    # -- invariant measurement needs consolidated state, but full-level
    # I3 uses the DRC the critical boundaries already produce
    def _measure(self):
        if self.level == "light":
            q = state.query(self.kicad_cli, self.kipy, self.pcb, "opens")
            return {"opens": q.value, "shorts": None}
        qs = state.query(self.kicad_cli, self.kipy, self.pcb, "shorts")
        qo = state.query(self.kicad_cli, self.kipy, self.pcb, "opens",
                         consolidate=False)
        qc = state.query(self.kicad_cli, self.kipy, self.pcb, "clearance",
                         consolidate=False)
        return {"opens": qo.value, "shorts": qs.value,
                "clearance": qc.value}

    def __enter__(self):
        ckpt = self.pcb.parent / ".ckpt"
        ckpt.mkdir(exist_ok=True)
        self._snap = ckpt / f"{int(time.time())}-{self.name}.kicad_pcb"
        shutil.copy2(self.pcb, self._snap)
        self.pre = self._measure()
        self._pre_fp = state.fingerprint(self.pcb).short()
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type is not None:
            self._restore()
            self.violations = [f"engine-exception:{exc_type.__name__}"]
            self._record(False, self.violations)
            return False                       # propagate the error
        post = self._measure()
        violations = check_level(self.level, self.pre, post,
                                 tolerance=self.tol)
        if violations:
            self._restore()
            self.violations = violations
            self._record(False, violations)
            self.log(f"  [kernel] TXN {self.name} 回滚: "
                     f"{'; '.join(violations)[:120]}")
            return True                        # swallow: chain continues
        self._record(True, [])
        # prune snapshots, keep the newest 5
        ckpt = self.pcb.parent / ".ckpt"
        snaps = sorted(ckpt.glob("*.kicad_pcb"))
        for old in snaps[:-5]:
            try:
                old.unlink()
            except OSError:
                pass
        return False

    def _restore(self):
        if self._snap and self._snap.exists():
            shutil.copy2(self._snap, self.pcb)

    def _record(self, ok: bool, violations: list):
        mf = self.pcb.parent / "chain_manifest.json"
        try:
            entries = json.loads(mf.read_text(encoding="utf-8")) \
                if mf.exists() else []
        except (ValueError, OSError):
            entries = []
        post_fp = state.fingerprint(self.pcb)
        entries.append({
            "stage": self.name, "ts": int(time.time()),
            "ok": ok,
            "board_sha256": post_fp.board_sha256,
            "pre_fp": self._pre_fp, "post_fp": post_fp.short(),
            "level": self.level, "violations": violations[:5],
        })
        try:
            mf.write_text(json.dumps(entries, ensure_ascii=False,
                                     indent=1), encoding="utf-8")
        except OSError:
            pass
