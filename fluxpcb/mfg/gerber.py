#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""gerber — Gerber Exporter (Component 11).

Exports fab package: Gerber + drill + pos + STEP.
Wraps kicad-cli commands.
"""
from __future__ import annotations

import os
import subprocess
from typing import Any, Optional


class GerberExporter:
    """Gerber exporter — produces fab package."""

    def __init__(self, kicad_cli: str = "kicad-cli"):
        """Initialize Gerber exporter.

        Args:
            kicad_cli: Path to kicad-cli executable
        """
        self.kicad_cli = kicad_cli

    def export(self, board_path: str, out_dir: str) -> dict:
        """Export fab package.

        Args:
            board_path: Path to .kicad_pcb file
            out_dir: Output directory

        Returns:
            dict with exported file paths
        """
        os.makedirs(out_dir, exist_ok=True)

        result = {
            'gerbers': [],
            'drill': [],
            'pos': None,
            'step': None,
        }

        # Export Gerbers
        result['gerbers'] = self._export_gerbers(board_path, out_dir)

        # Export drill files
        result['drill'] = self._export_drill(board_path, out_dir)

        # Export position file
        result['pos'] = self._export_pos(board_path, out_dir)

        # Export STEP
        result['step'] = self._export_step(board_path, out_dir)

        return result

    def _export_gerbers(self, board_path: str, out_dir: str) -> list[str]:
        """Export Gerber files."""
        gerber_dir = os.path.join(out_dir, 'gerbers')
        os.makedirs(gerber_dir, exist_ok=True)

        try:
            subprocess.run([
                self.kicad_cli, 'pcb', 'export', 'gerbers',
                '--output', gerber_dir,
                board_path,
            ], capture_output=True, timeout=60)

            return [
                os.path.join(gerber_dir, f)
                for f in os.listdir(gerber_dir)
                if f.endswith(('.gbr', '.gbl', '.gtl', '.gbs', '.gts'))
            ]
        except Exception:
            return []

    def _export_drill(self, board_path: str, out_dir: str) -> list[str]:
        """Export drill files."""
        drill_dir = os.path.join(out_dir, 'drill')
        os.makedirs(drill_dir, exist_ok=True)

        try:
            subprocess.run([
                self.kicad_cli, 'pcb', 'export', 'drill',
                '--output', drill_dir,
                board_path,
            ], capture_output=True, timeout=60)

            return [
                os.path.join(drill_dir, f)
                for f in os.listdir(drill_dir)
                if f.endswith(('.drl', '.xln'))
            ]
        except Exception:
            return []

    def _export_pos(self, board_path: str, out_dir: str) -> Optional[str]:
        """Export position file."""
        try:
            pos_path = os.path.join(out_dir, 'positions.csv')
            subprocess.run([
                self.kicad_cli, 'pcb', 'export', 'pos',
                '--output', pos_path,
                '--format', 'csv',
                board_path,
            ], capture_output=True, timeout=60)

            return pos_path if os.path.exists(pos_path) else None
        except Exception:
            return None

    def _export_step(self, board_path: str, out_dir: str) -> Optional[str]:
        """Export STEP file."""
        try:
            step_path = os.path.join(out_dir, 'board.step')
            subprocess.run([
                self.kicad_cli, 'pcb', 'export', 'step',
                '--output', step_path,
                board_path,
            ], capture_output=True, timeout=120)

            return step_path if os.path.exists(step_path) else None
        except Exception:
            return None
