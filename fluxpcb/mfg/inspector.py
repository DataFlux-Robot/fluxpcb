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
        """IPC-7351: land pattern against the nominal, plus the pad floor."""
        violations = []

        if not state.board:
            return violations

        try:
            for fp in state.board.GetFootprints():
                for pad in fp.Pads():
                    size = pad.GetSize()
                    if size.x < 0.2 * 1e6 or size.y < 0.2 * 1e6:
                        violations.append({
                            'rule': 'ipc7351_pad_size',
                            'severity': 'warning',
                            'description': f'Pad {pad.GetNumber()} on {fp.GetReference()} too small',
                        })
        except Exception:
            pass

        try:
            from . import ipc7351
            r = ipc7351.check_board(state.board)
            for f in r['findings']:
                violations.append({
                    'rule': f['rule'], 'severity': f['severity'],
                    'description': f['description'],
                    'ref': f['ref'], 'package': f['package'],
                })
            self._ipc7351_checked = r['checked']
            self._ipc7351_skipped = r['skipped']
        except Exception as e:
            violations.append({'rule': 'ipc7351_error',
                               'severity': 'warning',
                               'description': str(e)})

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
        """Panelize for real: a .kicad_pcb with N copies, frame and fiducials.

        `panel_copies` defaults to 1 (no panel): a one-up "panel" is not a
        manufacturing artefact.  The copies are placed by the existing
        guillotine packer and written next to the board as `panel.kicad_pcb`.
        """
        copies = int(getattr(self, 'panel_copies', 1) or 1)
        if not self.panelizer:
            return {'status': 'not_panelized'}
        if copies <= 1:
            return {'status': 'not_requested', 'copies': 1,
                    'why': 'panel_copies <= 1'}
        path = getattr(state, 'board_path', '') or ''
        if not path:
            return {'status': 'error', 'error': 'no board path'}
        try:
            import pcbnew
            b = state.board or pcbnew.LoadBoard(str(path))
            bb = b.GetBoardEdgesBoundingBox()
            w, h = bb.GetWidth() / 1e6, bb.GetHeight() / 1e6
            panel = self.panelizer.panelize([(w, h, path)] * copies)
            out = str(path).replace('.kicad_pcb', '_panel.kicad_pcb')
            r = self.panelizer.generate_kicad_panel(panel, out, src_path=path)
            r['copies'] = len(panel.boards)
            return r
        except Exception as e:
            return {'status': 'error', 'error': f'{type(e).__name__}: {e}'}
