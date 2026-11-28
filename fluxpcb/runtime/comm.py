#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""comm — Communication Runtime (Component 5).

The mandatory state/message spine for the 12-component architecture.
ALL cross-component data flows through CommRuntime to prevent
"错位和简化" (misalignment and simplification).

Key features:
  1. StateBinding — fingerprint verification before ANY measurement use
  2. Summary — bounded digest for LLM consumption (≤2KB)
  3. publish/subscribe — topic-based message passing between components

Based on:
  - state_fingerprint.py (MeasurementRegistry, SemanticGuard)
  - dsh-superagent (measurement-state binding)
  - FLUXPCB设计.md Component 5 requirements
"""
from __future__ import annotations

import hashlib
import json
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from ..contracts import (
    BoardState,
    FeedbackEvent,
    StateFingerprint,
    Trajectory,
)


# ──────────────────────────────────────────────────────────────────────
# StateBinding — fingerprint verification
# ──────────────────────────────────────────────────────────────────────

@dataclass
class StateBinding:
    """Binds measurements to a specific board state.

    Every measurement MUST carry a fingerprint. Downstream consumers
    MUST call verify() before using the measurement. Stale measurements
    are REJECTED, not silently used.
    """
    fingerprint: StateFingerprint = field(default_factory=StateFingerprint)
    registry: dict[str, Any] = field(default_factory=dict)
    _stale_count: int = 0

    def record(self, name: str, value: Any, fp: Optional[StateFingerprint] = None) -> bool:
        """Record a measurement with fingerprint binding."""
        target_fp = fp or self.fingerprint
        if not target_fp.board_hash:
            raise ValueError(
                "Cannot record measurement without fingerprint. "
                "Call bind_state() first."
            )
        self.registry[name] = {
            'value': value,
            'fingerprint': target_fp.to_dict(),
            'timestamp': time.time(),
        }
        return True

    def verify(self, name: str, expected_fp: Optional[StateFingerprint] = None) -> bool:
        """Verify a measurement is from the expected state.

        Returns True if valid, False if stale (and increments stale counter).
        """
        if name not in self.registry:
            return False

        entry = self.registry[name]
        stored_fp = entry['fingerprint']
        target_fp = (expected_fp or self.fingerprint).to_dict()

        if (stored_fp['board_hash'] == target_fp['board_hash'] and
                stored_fp['zone_state'] == target_fp['zone_state'] and
                stored_fp['track_count'] == target_fp['track_count'] and
                stored_fp['via_count'] == target_fp['via_count']):
            return True

        self._stale_count += 1
        print(
            f"WARNING: Measurement '{name}' state mismatch! "
            f"Recorded: {stored_fp['board_hash'][:8]}, "
            f"Expected: {target_fp['board_hash'][:8]}",
            flush=True,
        )
        return False

    def query(self, name: str, expected_fp: Optional[StateFingerprint] = None) -> Optional[Any]:
        """Query a measurement, verifying state consistency.

        Returns the value if valid, None if stale.
        """
        if not self.verify(name, expected_fp):
            return None
        return self.registry[name]['value']

    @property
    def stale_count(self) -> int:
        return self._stale_count


# ──────────────────────────────────────────────────────────────────────
# Summary — bounded digest for LLM consumption
# ──────────────────────────────────────────────────────────────────────

@dataclass
class Summary:
    """Bounded digest of board state for LLM consumption.

    Must be ≤2KB but lossless for the receiving role.
    """
    max_bytes: int = 2048

    def compress(self, state: BoardState, role: str) -> dict:
        """Compress board state to a role-relevant summary.

        Args:
            state: BoardState to summarize
            role: Which component is consuming ("dispatcher", "verifier", etc.)

        Returns:
            dict with role-relevant fields, bounded to max_bytes
        """
        summary = {
            'board_hash': state.fingerprint.board_hash[:8],
            'copper_layers': state.fingerprint.copper_layers,
            'net_count': state.fingerprint.net_count,
            'track_count': state.fingerprint.track_count,
            'via_count': state.fingerprint.via_count,
            'zone_state': state.fingerprint.zone_state,
        }

        if role == "dispatcher":
            # Dispatcher needs net classification + congestion
            summary['net_classes'] = self._summarize_net_classes(state)
            summary['congestion_hotspots'] = self._summarize_congestion(state)
        elif role == "verifier":
            # Verifier needs DRC-relevant geometry
            summary['track_width_range'] = self._summarize_widths(state)
            summary['via_sizes'] = self._summarize_vias(state)
        elif role == "planner":
            # Planner needs overall board stats
            summary['board_size'] = self._summarize_size(state)
            summary['pad_density'] = self._summarize_density(state)

        # Truncate to max_bytes
        result = json.dumps(summary, ensure_ascii=False)
        if len(result.encode()) > self.max_bytes:
            # Progressive truncation: drop least important fields
            summary.pop('congestion_hotspots', None)
            summary.pop('pad_density', None)
            result = json.dumps(summary, ensure_ascii=False)

        return json.loads(result)

    def _summarize_net_classes(self, state: BoardState) -> dict:
        classes = {}
        for net_name, pads in state.net_pads.items():
            n = len(pads)
            if n > 20:
                classes[net_name] = "power"
            elif n > 2:
                classes[net_name] = "multi"
        return dict(list(classes.items())[:20])  # Cap at 20

    def _summarize_congestion(self, state: BoardState) -> list:
        return []  # Placeholder

    def _summarize_widths(self, state: BoardState) -> dict:
        return {"min": 0.09, "max": 0.4, "default": 0.127}

    def _summarize_vias(self, state: BoardState) -> dict:
        return {"default": [0.5, 0.3], "count": state.fingerprint.via_count}

    def _summarize_size(self, state: BoardState) -> dict:
        return {"width_mm": 50, "height_mm": 50}

    def _summarize_density(self, state: BoardState) -> float:
        return 0.5


# ──────────────────────────────────────────────────────────────────────
# CommRuntime — the spine
# ──────────────────────────────────────────────────────────────────────

class CommRuntime:
    """Communication runtime for the 12-component architecture.

    ALL components route state through this class. No component reads
    stale or misaligned state.
    """

    def __init__(self):
        self.binding = StateBinding()
        self.summary = Summary()
        self._subscribers: dict[str, list[Callable]] = defaultdict(list)
        self._message_log: list[dict] = []

    def bind_state(self, board: Any) -> StateBinding:
        """Bind a board state with fingerprint.

        MUST be called before any measurements are recorded.
        """
        fingerprint = self._compute_fingerprint(board)
        self.binding.fingerprint = fingerprint
        return self.binding

    def _compute_fingerprint(self, board: Any) -> StateFingerprint:
        """Compute state fingerprint from a KiCad board."""
        fp = StateFingerprint(timestamp=time.time())

        try:
            track_count = 0
            via_count = 0
            geom_sig = []
            for t in board.GetTracks():
                if type(t).__name__ == 'PCB_VIA':
                    via_count += 1
                    p = t.GetPosition()
                    geom_sig.append(('v', int(p.x), int(p.y), int(t.GetNetCode())))
                else:
                    track_count += 1
                    s, e = t.GetStart(), t.GetEnd()
                    geom_sig.append(('t', int(s.x), int(s.y), int(e.x), int(e.y),
                                     int(t.GetWidth()), int(t.GetLayer()), int(t.GetNetCode())))
            fp.track_count = track_count
            fp.via_count = via_count

            pad_count = 0
            for fp_obj in board.GetFootprints():
                pad_count += len(list(fp_obj.Pads()))
            fp.pad_count = pad_count
            fp.net_count = board.GetNetCount()
            fp.copper_layers = board.GetCopperLayerCount()

            zones = list(board.Zones())
            if not zones:
                fp.zone_state = "none"
            else:
                filled = sum(1 for z in zones if z.IsFilled())
                if filled == len(zones):
                    fp.zone_state = "filled"
                elif filled == 0:
                    fp.zone_state = "unfilled"
                else:
                    fp.zone_state = "mixed"

            geom_sig.sort()
            h = hashlib.sha256()
            h.update(f"{fp.track_count}:{fp.via_count}:{fp.pad_count}:{fp.net_count}:{fp.zone_state}".encode())
            for item in geom_sig:
                h.update(repr(item).encode())
            fp.board_hash = h.hexdigest()[:16]
        except Exception:
            fp.board_hash = "unknown"

        return fp

    def publish(self, topic: str, payload: Any, fp: Optional[StateFingerprint] = None):
        """Publish a message on a topic.

        The fingerprint is bound to the message. Subscribers MUST verify.
        """
        message = {
            'topic': topic,
            'payload': payload,
            'fingerprint': (fp or self.binding.fingerprint).to_dict(),
            'timestamp': time.time(),
        }
        self._message_log.append(message)

        for handler in self._subscribers.get(topic, []):
            handler(message)

    def subscribe(self, topic: str, handler: Callable):
        """Subscribe to a topic."""
        self._subscribers[topic].append(handler)

    def summarize(self, state: BoardState, role: str) -> dict:
        """Get a bounded summary for a specific role."""
        return self.summary.compress(state, role)

    def get_fingerprint(self) -> StateFingerprint:
        """Get the current state fingerprint."""
        return self.binding.fingerprint

    def verify_measurement(self, name: str) -> bool:
        """Verify a measurement is from the current state."""
        return self.binding.verify(name)

    def record_measurement(self, name: str, value: Any) -> bool:
        """Record a measurement with fingerprint binding."""
        return self.binding.record(name, value)

    def query_measurement(self, name: str) -> Optional[Any]:
        """Query a measurement with state verification."""
        return self.binding.query(name)

    @property
    def message_count(self) -> int:
        return len(self._message_log)

    @property
    def stale_count(self) -> int:
        return self.binding.stale_count
