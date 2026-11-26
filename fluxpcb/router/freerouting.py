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
    # 2.5.0 first: it converges much better than 2.4.1 on fine-pitch
    # (rp2040: 21 unrouted/0 violations vs 39 unrouted/19 violations)
    r'E:/DATA/vscode/ZCODE/flux-neurotransmitter/tools_bin/freerouting-2.5.0.jar',
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
    kept_locked: int = 0       # tracks/vias the strip kept locked (keep/rip)
    passes: int = 0
    log: str = ""
    error: str = ""
    # rule-model congruence (T1-A): did the router receive the referee's table?
    rule_congruence: bool = False
    rule_audit: dict = None
    patch: dict = None
    # certificate of the candidate board (audit only: the router's output is a
    # tactic result, not yet a committed design; the repair steps enforce).
    proof: dict = None


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
        # The helper prefixes its JSON with __RESULT__ (it must flush and
        # exit before pcbnew's teardown); parse that line, not the banner
        # noise that precedes it.
        for line in (r.stdout or '').splitlines():
            if line.startswith('__RESULT__'):
                try:
                    return json.loads(line[len('__RESULT__'):])
                except json.JSONDecodeError:
                    return {'raw': line[:300]}
        return {'raw': (r.stdout or '')[-300:]}

    def _patch_dsn_from_project(self, board_path: str, dsn_path: str):
        """Transfer the project's effective rule table into the DSN.

        Two layers of proof:
          1. before: exhaustive L1 diff (project table vs DSN table);
          2. after: the patch is re-parsed and diffed again; a rewrite that
             does not read back equal is a failure, not a warning.

        Returns True only when the post-patch tables are congruent.
        """
        import json

        from ..contract import CongruenceError, RuleModel, patch_dsn

        pro_path = os.path.splitext(board_path)[0] + '.kicad_pro'
        if not os.path.exists(pro_path):
            self.last_patch = {'ok': False, 'notes': ['no .kicad_pro beside board']}
            return False
        try:
            with open(pro_path, encoding='utf-8') as f:
                pro = json.load(f)
            with open(dsn_path, encoding='utf-8', errors='replace') as f:
                text = f.read()
            dsn_model = RuleModel.from_dsn(text)
            model = RuleModel.from_project(
                pro, net_names=sorted(dsn_model.all_nets()))
            # T2-b A/B switch: a pcb-level default rule constrains objects
            # outside every class (pads with no net).  Whether that helps or
            # hurts is an empirical question; measure both.
            inject = os.environ.get('FLUXPCB_INJECT_DEFAULT', '1') != '0'
            new_text, report = patch_dsn(text, model, strict=True,
                                         inject_default=inject)
        except CongruenceError as e:
            self.last_patch = e.report.as_dict()
            self.last_congruence = False
            return False
        except Exception as e:
            self.last_patch = {'ok': False, 'notes': [f'patch failed: {e}']}
            self.last_congruence = False
            return False

        with open(dsn_path, 'w', encoding='utf-8') as f:
            f.write(new_text)
        self.last_patch = report.as_dict()
        self.last_congruence = report.ok
        return report.ok

    def audit_rules(self, board_path: str, dsn_path: str) -> dict:
        """Compare the project's table with the exported DSN (no rewriting)."""
        import json

        from ..contract import RuleModel

        pro_path = os.path.splitext(board_path)[0] + '.kicad_pro'
        out = {'ok': False, 'notes': []}
        if not os.path.exists(pro_path):
            out['notes'].append('no .kicad_pro beside board')
            return out
        try:
            with open(pro_path, encoding='utf-8') as f:
                pro = json.load(f)
            with open(dsn_path, encoding='utf-8', errors='replace') as f:
                text = f.read()
            dsn_model = RuleModel.from_dsn(text)
            model = RuleModel.from_project(
                pro, net_names=sorted(dsn_model.all_nets()))
            diff = model.diff(dsn_model)
            out.update(diff.as_dict())
            out['project_digest'] = model.digest()
            out['dsn_digest'] = dsn_model.digest()
            return out
        except Exception as e:
            out['notes'].append(f'audit failed: {e}')
            return out

    def route(self, board_path: str, out_path: str,
              timeout_s: int = 300, keep_copper_protected: bool = True,
              use_board_rules: bool = False,
              keep_nets=None, rip_nets=None) -> FreeroutingResult:
        """Route a board with Freerouting.

        Args:
            board_path: Input .kicad_pcb (should be the UNROUTED board)
            out_path: Output .kicad_pcb (routed)
            timeout_s: Freerouting wall-clock cap
            keep_copper_protected: keep existing copper as protect (default)
        keep_nets: nets whose existing copper stays (LOCKED) through the
            strip; the router must route around it.
        rip_nets: nets whose existing copper is removed; every other net's
            copper stays (LOCKED).  Mutually exclusive with keep_nets.
            use_board_rules: patch the DSN with the board's own clearance and
                track width from the paired .kicad_pro. KiCad's Specctra
                export always writes the default 0.2mm because LoadBoard()
                does not load the project — for boards designed with finer
                rules (H7 BGA: 0.09mm) that is 2.2x stricter than intended.
                Default False = benchmark-comparable (historical baselines
                used the unpatched DSN).

        Returns:
            FreeroutingResult
        """
        t0 = time.time()
        result = FreeroutingResult(output_path=out_path)
        self.last_patch = None
        self.last_congruence = False

        ok, reason = self.available()
        if not ok:
            result.error = f'backend unavailable: {reason}'
            return result

        work_dir = os.path.dirname(os.path.abspath(out_path)) or '.'
        os.makedirs(work_dir, exist_ok=True)

        # 1. Export DSN (Specctra) via 'strip' (idempotent on already-unrouted
        #    boards; writes <work_dir>/board.dsn + <work_dir>/unrouted.kicad_pcb)
        #    NOTE: pcbnew Save rewrites the paired .kicad_pro with KiCad
        #    defaults, so snapshot the project first and restore it after.
        try:
            src_pro = os.path.splitext(board_path)[0] + '.kicad_pro'
            pro_snapshot = None
            if os.path.exists(src_pro):
                with open(src_pro, 'rb') as f:
                    pro_snapshot = f.read()

            strip_args = [board_path, work_dir]
            if keep_nets:
                strip_args.append('keep=' + '|'.join(sorted(set(keep_nets))))
            elif rip_nets:
                strip_args.append('rip=' + '|'.join(sorted(set(rip_nets))))
            info = self._kicad('strip', *strip_args, timeout=120)
            result.kept_locked = int(info.get('kept_locked', 0) or 0)
            dsn_path = os.path.join(work_dir, 'board.dsn')
            unrouted_path = os.path.join(work_dir, 'unrouted.kicad_pcb')
            if not os.path.exists(dsn_path):
                result.error = f'DSN not produced by strip (got {info})'
                return result

            # Restore the source project beside the stripped board
            if pro_snapshot is not None:
                dst_pro = os.path.splitext(unrouted_path)[0] + '.kicad_pro'
                with open(dst_pro, 'wb') as f:
                    f.write(pro_snapshot)

            # Use the freshly stripped board as the import base (matches DSN)
            if os.path.exists(unrouted_path):
                board_path = unrouted_path

            # The router must solve the referee's problem.  Audit, then
            # transfer (and prove the transfer) when requested.
            result.rule_audit = self.audit_rules(board_path, dsn_path)
            if use_board_rules:
                self._patch_dsn_from_project(board_path, dsn_path)
                result.rule_congruence = bool(self.last_congruence)
                result.patch = self.last_patch
                if not self.last_congruence:
                    # Keep routing (a wrong table still produces copper),
                    # but nothing downstream may call the result verified.
                    result.log = result.log or ''
            else:
                result.rule_congruence = bool(result.rule_audit.get('ok'))
        except Exception as e:
            result.error = f'DSN export failed: {e}'
            return result

        # 2. Run Freerouting
        ses_path = os.path.join(work_dir, 'board.ses')
        log_path = os.path.join(work_dir, 'freerouting.log')
        # Optional heap cap (FLUXPCB_JAVA_XMX, e.g. "2g"): several router
        # processes run side by side during an arm campaign, and an
        # uncapped heap on each one exhausts the machine.
        xmx = os.environ.get('FLUXPCB_JAVA_XMX', '')
        cmd = [
            self.java, '-Djava.awt.headless=true',
            *(['-Xmx' + xmx] if xmx else []),
            '-jar', self.jar,
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
        #    IMPORTANT: pcbnew rewrites the paired .kicad_pro during save with
        #    KiCad default rules, which silently changes what DRC judges.
        #    Snapshot the source project and restore it after the import.
        try:
            src_pro = os.path.splitext(board_path)[0] + '.kicad_pro'
            pro_snapshot = None
            if os.path.exists(src_pro):
                with open(src_pro, 'rb') as f:
                    pro_snapshot = f.read()

            self._kicad('import-ses', board_path, ses_path, out_path, timeout=180)

            # Restore the project beside the routed board
            if pro_snapshot is not None:
                out_pro = os.path.splitext(out_path)[0] + '.kicad_pro'
                with open(out_pro, 'wb') as f:
                    f.write(pro_snapshot)
        except Exception as e:
            result.error = f'SES import failed: {e}'
            result.seconds = round(time.time() - t0, 1)
            return result

        result.routed = os.path.exists(out_path)
        result.seconds = round(time.time() - t0, 1)
        if result.routed:
            try:
                from fluxpcb.proof import state as _S, step as _step
                _rules = _S.board_rules(out_path)[0]
                _st = _S.extract(out_path, _rules)
                result.proof = _step.audit(_st, _rules, board_path=out_path)
            except Exception as e:
                result.proof = {'ok': None, 'error': str(e)[:200]}
        return result


def route_with_freerouting(board_path: str, out_path: str,
                           timeout_s: int = 300) -> FreeroutingResult:
    """Convenience function."""
    return FreeroutingBackend().route(board_path, out_path, timeout_s)
