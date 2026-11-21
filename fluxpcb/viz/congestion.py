#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""congestion — Congestion Renderer (Component 12).

Renders congestion heatmaps and layer views.
"""
from __future__ import annotations

import os
from typing import Any, Optional


class CongestionRenderer:
    """Congestion renderer — visualizes routing congestion."""

    def __init__(self, output_dir: str = "reports/maps"):
        """Initialize congestion renderer.

        Args:
            output_dir: Directory for output files
        """
        self.output_dir = output_dir
        os.makedirs(output_dir, exist_ok=True)

    def render_congestion(
        self,
        congestion: dict[tuple, float],
        board_size: tuple[float, float] = (50, 50),
        output_file: str = "congestion.svg",
    ) -> str:
        """Render congestion heatmap.

        Args:
            congestion: {(layer, gx, gy): cost} dict
            board_size: (width, height) in mm
            output_file: Output filename

        Returns:
            Path to output file
        """
        path = os.path.join(self.output_dir, output_file)

        # Simple SVG rendering
        width, height = board_size
        svg = f'''<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" width="{width*10}" height="{height*10}" viewBox="0 0 {width} {height}">
  <rect width="{width}" height="{height}" fill="white"/>
'''

        # Render congestion cells
        for (li, gx, gy), cost in congestion.items():
            if cost > 0:
                intensity = min(1.0, cost / 10.0)
                color = f"rgb({int(255*intensity)}, {int(255*(1-intensity))}, 0)"
                svg += f'  <rect x="{gx}" y="{gy}" width="1" height="1" fill="{color}" opacity="0.5"/>\n'

        svg += '</svg>'

        with open(path, 'w') as f:
            f.write(svg)

        return path

    def render_layer(
        self,
        tracks: list[dict],
        layer: int = 0,
        board_size: tuple[float, float] = (50, 50),
        output_file: str = "layer.svg",
    ) -> str:
        """Render a single layer view.

        Args:
            tracks: List of track dicts with x1,y1,x2,y2,width
            layer: Layer index
            board_size: (width, height) in mm
            output_file: Output filename

        Returns:
            Path to output file
        """
        path = os.path.join(self.output_dir, output_file)

        width, height = board_size
        svg = f'''<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" width="{width*10}" height="{height*10}" viewBox="0 0 {width} {height}">
  <rect width="{width}" height="{height}" fill="white"/>
'''

        # Render tracks
        for track in tracks:
            if track.get('layer') == layer:
                svg += f'  <line x1="{track["x1"]}" y1="{track["y1"]}" x2="{track["x2"]}" y2="{track["y2"]}" stroke="red" stroke-width="{track.get("width", 0.1)}"/>\n'

        svg += '</svg>'

        with open(path, 'w') as f:
            f.write(svg)

        return path
