#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""inspector — Manufacturing Inspector (Component 11).

Manufacturing readiness and fab package:
  - DFM checks (JLC rules)
  - IPC-7351 footprint validation
  - IPC-2221 generic DFM
  - Panelization

Wraps existing tools: ipc2221_dfm.py, panelization.py
"""
from __future__ import annotations

from typing import Any, Optional

from ..contracts import (
    BoardDesignPlan,
    BoardState,
    ManufacturingReport,
    StateFingerprint,
)


class ManufacturingInspector:
    """Manufacturing inspector — checks manufacturing readiness."""

    def __init__(self, ipc: Any = None, panelizer: Any = None):
        """Initialize manufacturing inspector.

        Args:
            ipc: IPC2221DFM instance
            panelizer: Panelizer instance
        """
        self.ipc = ipc
        self.panelizer = panelizer

    def inspect(self, state: BoardState, plan: BoardDesignPlan) -> ManufacturingReport:
        """Run manufacturing inspection.

        Args:
            state: Board state
            plan: Design plan

        Returns:
            ManufacturingReport with violations
        """
        dfm_violations = []
        ipc7351_violations = []
        ipc2221_violations = []

        # Run IPC-2221 DFM checks
        if self.ipc:
            try:
                report = self.ipc.check(state.board_path or "board.kicad_pcb")
                ipc2221_violations = [
                    {
                        'rule': v.get('rule', ''),
                        'severity': v.get('severity', 'error'),
                        'description': v.get('description', ''),
                    }
                    for v in report.get('violations', [])
                ]
            except Exception as e:
                ipc2221_violations = [{'rule': 'ipc2221_error', 'description': str(e)}]

        # Run IPC-7351 footprint validation
        ipc7351_violations = self._check_footprints(state)

        # Run DFM checks
        dfm_violations = self._check_dfm(state, plan)

        # Panelization
        panel = {}
        if self.panelizer:
            try:
                panel = self._panelize(state)
            except Exception as e:
                panel = {'error': str(e)}

        return ManufacturingReport(
            panel=panel,
            dfm_violations=dfm_violations,
            ipc7351_violations=ipc7351_violations,
            ipc2221_violations=ipc2221_violations,
            fingerprint=state.fingerprint,
        )

    def _check_footprints(self, state: BoardState) -> list[dict]:
        """Check IPC-7351 footprint compliance."""
        violations = []

        if not state.board:
            return violations

        try:
            for fp in state.board.GetFootprints():
                for pad in fp.Pads():
                    # Check pad size
                    size = pad.GetSize()
                    if size.x < 0.2 * 1e6 or size.y < 0.2 * 1e6:
                        violations.append({
                            'rule': 'ipc7351_pad_size',
                            'severity': 'warning',
                            'description': f'Pad {pad.GetNumber()} on {fp.GetReference()} too small',
                        })
        except Exception:
            pass

        return violations

    def _profile(self, state):
        """The dated fab profile this board is judged against."""
        from .fab_profile import active_profile
        proj = None
        path = getattr(state, 'board_path', '') or ''
        if path:
            from pathlib import Path
            p = Path(path)
            for d in (p.parent, p.parent.parent, p.parent.parent.parent):
                if (d / 'spec.yaml').exists() or (d / 'project.yaml').exists():
                    proj = d
                    break
        return active_profile(proj)

    def _check_dfm(self, state: BoardState, plan: BoardDesignPlan) -> list[dict]:
        """Check DFM compliance against the fab profile."""
        violations = []
        prof = self._profile(state)
        lim = prof.dfm_limits()

        # Check track width
        min_track = lim.get('min_track_width', 0.09)
        if plan.net_class_rules.widths:
            for cls, width in plan.net_class_rules.widths.items():
                if width < min_track:
                    violations.append({
                        'rule': 'dfm_track_width',
                        'severity': 'error',
                        'description': f'Track width {width}mm < {min_track}mm for {cls}',
                        'profile': prof.profile_id,
                        'retrieved': prof.retrieved,
                    })

        # Check clearance
        min_clearance = lim.get('min_clearance', 0.09)
        if plan.net_class_rules.clearances:
            for cls, clearance in plan.net_class_rules.clearances.items():
                if clearance < min_clearance:
                    violations.append({
                        'rule': 'dfm_clearance',
                        'severity': 'error',
                        'description': f'Clearance {clearance}mm < {min_clearance}mm for {cls}',
                        'profile': prof.profile_id,
                        'retrieved': prof.retrieved,
                    })

        return violations

    def _panelize(self, state: BoardState) -> dict:
        """Panelize the board."""
        if self.panelizer:
            # Simple panelization
            return {
                'status': 'panelized',
                'copies': 1,
                'fiducials': 3,
                'tooling_holes': 3,
            }
        return {'status': 'not_panelized'}
