#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ipc7095_fanout — IPC-7095 Section 5.2 BGA escape fanout calculator.

Implements the industry-standard BGA fanout geometry:
- Dogbone (diagonal 45°) fanout
- Escape channel width/spacing
- Minimum signal layers calculation
- HDI microvia requirements

Based on:
- IPC-7095 Section 5.2 (BGA design guidelines)
- circuit-agent-client calculate_bga_escape_routing
- KiCad MCP Pro pcb_bga_fanout
- JLC BGA design guidelines

Usage:
    from ipc7095_fanout import BGAFanout
    fanout = BGAFanout(n_balls=256, pitch=0.8, via_drill=0.2, ...)
    plan = fanout.calculate()
"""
import math
from dataclasses import dataclass, field


@dataclass
class FanoutVia:
    """A single BGA fanout via."""
    ball_id: str          # e.g. "A1", "B2"
    ball_x: float         # ball center x (mm)
    ball_y: float         # ball center y (mm)
    via_x: float          # via center x (mm)
    via_y: float          # via center y (mm)
    net: str = ""
    layer: str = "In1.Cu"  # target escape layer


@dataclass
class EscapeChannel:
    """An escape channel between BGA rows."""
    row: int              # row index
    width_mm: float       # channel width
    spacing_mm: float     # track spacing
    track_width_mm: float # recommended track width
    capacity: int         # max tracks in channel


@dataclass
class FanoutPlan:
    """Complete BGA fanout plan."""
    vias: list = field(default_factory=list)
    channels: list = field(default_factory=list)
    min_signal_layers: int = 0
    hdi_required: bool = False
    microvia_drill_mm: float = 0.1
    track_width_mm: float = 0.1
    via_pad_mm: float = 0.45
    total_balls: int = 0
    signal_balls: int = 0
    power_balls: int = 0
    unused_balls: int = 0


class BGAFanout:
    """IPC-7095 Section 5.2 BGA escape fanout calculator."""

    def __init__(self, n_balls=256, pitch=0.8, via_drill=0.2,
                 via_pad=0.45, ball_pad=0.4, track_width=0.1,
                 escape_layer="In1.Cu", strategy="dog_ear"):
        self.n_balls = n_balls
        self.pitch = pitch
        self.via_drill = via_drill
        self.via_pad = via_pad
        self.ball_pad = ball_pad
        self.track_width = track_width
        self.escape_layer = escape_layer
        self.strategy = strategy  # "dog_ear" or "inline"

        # Derived geometry (IPC-7095)
        self._calc_geometry()

    def _calc_geometry(self):
        """Calculate fanout geometry from IPC-7095 formulas."""
        # Diagonal dogbone offset (45°)
        # Via is placed diagonally between 4 balls
        self.dogbone_offset = self.pitch / math.sqrt(2)

        # Escape channel width (between rows)
        self.channel_width = self.pitch - self.via_pad

        # Track spacing in channel (IPC-7095: 2x track width)
        self.channel_spacing = self.track_width * 2

        # Max tracks per channel
        self.tracks_per_channel = int(self.channel_width / self.channel_spacing)

        # Minimum signal layers (IPC-7095 formula)
        # Each layer can escape ~2 rows (top + bottom)
        self.min_signal_layers = self._calc_min_layers()

        # HDI required if pitch < 0.65mm or layers > 4
        self.hdi_required = (self.pitch < 0.65 or self.min_signal_layers > 4)

        # Microvia drill (IPC-7095 for HDI)
        self.microvia_drill = 0.1 if self.hdi_required else self.via_drill

    def _calc_min_layers(self):
        """Calculate minimum signal layers needed (IPC-7095)."""
        # Number of BGA rows
        n_rows = int(math.sqrt(self.n_balls))

        # Each signal layer can escape ~2 rows (one from top, one from bottom)
        # But inner rows need more layers
        # IPC-7095: layers = ceil((n_rows - 2) / 2) + 1
        layers = math.ceil((n_rows - 2) / 2) + 1

        # Minimum 2 layers (top + bottom)
        return max(2, layers)

    def calculate(self, balls=None):
        """Calculate complete fanout plan.

        Args:
            balls: list of dicts with {id, x, y, net, kind} where kind is
                   'signal', 'power', 'unused'

        Returns:
            FanoutPlan
        """
        plan = FanoutPlan()
        plan.total_balls = self.n_balls
        plan.min_signal_layers = self.min_signal_layers
        plan.hdi_required = self.hdi_required
        plan.microvia_drill_mm = self.microvia_drill
        plan.track_width_mm = self.track_width
        plan.via_pad_mm = self.via_pad

        if balls is None:
            # Generate synthetic BGA ball positions
            balls = self._gen_balls()

        # Classify balls
        for b in balls:
            kind = b.get('kind', 'signal')
            if kind == 'power':
                plan.power_balls += 1
            elif kind == 'unused':
                plan.unused_balls += 1
            else:
                plan.signal_balls += 1

        # Generate vias for signal and power balls (adaptive placement)
        existing_vias = []
        for b in balls:
            kind = b.get('kind', 'signal')
            if kind == 'unused':
                continue

            via = self._calc_via_position(b, existing_vias)
            plan.vias.append(via)
            existing_vias.append((via.via_x, via.via_y))

        # Generate escape channels
        n_rows = int(math.sqrt(self.n_balls))
        for row in range(n_rows - 1):
            channel = EscapeChannel(
                row=row,
                width_mm=self.channel_width,
                spacing_mm=self.channel_spacing,
                track_width_mm=self.track_width,
                capacity=self.tracks_per_channel,
            )
            plan.channels.append(channel)

        return plan

    def _gen_balls(self):
        """Generate synthetic BGA ball grid."""
        balls = []
        n_rows = int(math.sqrt(self.n_balls))
        for i in range(n_rows):
            for j in range(n_rows):
                ball_id = f"{chr(65+i)}{j+1}"
                x = j * self.pitch
                y = i * self.pitch
                # Classify: inner balls = power, outer = signal
                if i == 0 or i == n_rows-1 or j == 0 or j == n_rows-1:
                    kind = 'signal'
                elif i == 1 or i == n_rows-2 or j == 1 or j == n_rows-2:
                    kind = 'power'
                else:
                    kind = 'unused'
                balls.append({'id': ball_id, 'x': x, 'y': y, 'net': ball_id, 'kind': kind})
        return balls

    def _calc_via_position(self, ball, existing_vias=None):
        """Calculate dogbone via position (IPC-7095 + adaptive congestion avoidance).

        Inspired by FanoutNet (AAAI 2023): adapt via placement to avoid congestion.
        """
        x, y = ball['x'], ball['y']

        if self.strategy == 'dog_ear':
            dx = self.dogbone_offset
            dy = self.dogbone_offset
        else:
            dx = self.pitch / 2
            dy = 0

        # Determine direction based on ball position in grid
        n_rows = int(math.sqrt(self.n_balls))
        row = int(round(y / self.pitch))
        col = int(round(x / self.pitch))

        # Edge balls escape outward
        if row == 0:
            via_x, via_y = x, y - dy
        elif row == n_rows - 1:
            via_x, via_y = x, y + dy
        elif col == 0:
            via_x, via_y = x - dx, y
        elif col == n_rows - 1:
            via_x, via_y = x + dx, y
        else:
            # Inner balls: diagonal dogbone
            via_x, via_y = x + dx, y + dy

        # Adaptive: if via collides with existing, try alternative positions
        if existing_vias:
            min_dist = self.via_pad + self.via_drill + 0.1  # clearance
            for (ex, ey) in existing_vias:
                if math.hypot(via_x - ex, via_y - ey) < min_dist:
                    # Try 4 diagonal alternatives
                    for alt_dx, alt_dy in [(dx, dy), (-dx, dy), (dx, -dy), (-dx, -dy)]:
                        alt_x, alt_y = x + alt_dx, y + alt_dy
                        collision = False
                        for (ex2, ey2) in existing_vias:
                            if math.hypot(alt_x - ex2, alt_y - ey2) < min_dist:
                                collision = True
                                break
                        if not collision:
                            via_x, via_y = alt_x, alt_y
                            break
                    break

        return FanoutVia(
            ball_id=ball['id'],
            ball_x=x,
            ball_y=y,
            via_x=via_x,
            via_y=via_y,
            net=ball.get('net', ''),
            layer=self.escape_layer,
        )

    def to_json(self):
        """Export plan as JSON (compatible with MCP tools)."""
        plan = self.calculate()
        return {
            'strategy': self.strategy,
            'pitch_mm': self.pitch,
            'via_drill_mm': self.via_drill,
            'via_pad_mm': self.via_pad,
            'ball_pad_mm': self.ball_pad,
            'track_width_mm': self.track_width,
            'escape_layer': self.escape_layer,
            'total_balls': plan.total_balls,
            'signal_balls': plan.signal_balls,
            'power_balls': plan.power_balls,
            'unused_balls': plan.unused_balls,
            'min_signal_layers': plan.min_signal_layers,
            'hdi_required': plan.hdi_required,
            'microvia_drill_mm': plan.microvia_drill_mm,
            'vias': [
                {
                    'ball_id': v.ball_id,
                    'ball_x': round(v.ball_x, 3),
                    'ball_y': round(v.ball_y, 3),
                    'via_x': round(v.via_x, 3),
                    'via_y': round(v.via_y, 3),
                    'net': v.net,
                    'layer': v.layer,
                }
                for v in plan.vias
            ],
            'channels': [
                {
                    'row': c.row,
                    'width_mm': round(c.width_mm, 3),
                    'spacing_mm': round(c.spacing_mm, 3),
                    'track_width_mm': round(c.track_width_mm, 3),
                    'capacity': c.capacity,
                }
                for c in plan.channels
            ],
        }


def calc_bga_escape_routing(n_balls=256, pitch=0.8, via_drill=0.2,
                             via_pad=0.45, ball_pad=0.4, track_width=0.1):
    """MCP-compatible BGA escape routing calculator (IPC-7095 Section 5.2).

    This is the same interface as circuit-agent-client's
    calculate_bga_escape_routing and KiCad MCP Pro's pcb_bga_fanout.

    Returns:
        dict: fanout plan with vias, channels, and layer requirements
    """
    fanout = BGAFanout(
        n_balls=n_balls,
        pitch=pitch,
        via_drill=via_drill,
        via_pad=via_pad,
        ball_pad=ball_pad,
        track_width=track_width,
    )
    return fanout.to_json()


if __name__ == '__main__':
    import json
    # Test: STM32H743 TFBGA240 (256 balls, 0.8mm pitch)
    result = calc_bga_escape_routing(
        n_balls=256,
        pitch=0.8,
        via_drill=0.2,
        via_pad=0.45,
        ball_pad=0.4,
        track_width=0.1,
    )
    print(json.dumps(result, indent=2))
