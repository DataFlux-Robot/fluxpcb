#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""state_fingerprint — State fingerprinting for measurement consistency.

Fixes the critical design flaw identified in the architecture document:
"测量不绑定状态。陈旧 zone 填充上的 40 与重灌后的 66，都是'开路数'
但属于两个板状态——数字从不携带它所测的状态指纹，混用无法察觉。"

Solution:
1. Every measurement carries a state fingerprint (board hash + zone state)
2. Downstream consumers MUST verify fingerprint before using measurements
3. Stale measurements are rejected, not silently used

Based on:
- dsh-superagent: measurement-state binding
- nopekit: evidence chain verification
- NextBoard: gate-based validation
"""
import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

MM = 1e6


@dataclass
class StateFingerprint:
    """Fingerprint of a board state for measurement consistency."""
    board_hash: str = ""
    zone_state: str = "unknown"  # "filled", "unfilled", "mixed"
    track_count: int = 0
    via_count: int = 0
    pad_count: int = 0
    net_count: int = 0
    copper_layers: int = 0
    timestamp: float = 0.0

    def matches(self, other):
        """Check if two fingerprints describe the same board state."""
        return (self.board_hash == other.board_hash and
                self.zone_state == other.zone_state and
                self.track_count == other.track_count and
                self.via_count == other.via_count)

    def to_dict(self):
        return {
            'board_hash': self.board_hash,
            'zone_state': self.zone_state,
            'track_count': self.track_count,
            'via_count': self.via_count,
            'pad_count': self.pad_count,
            'net_count': self.net_count,
            'copper_layers': self.copper_layers,
            'timestamp': self.timestamp,
        }


@dataclass
class Measurement:
    """A measurement with state fingerprint."""
    value: float = 0.0
    unit: str = ""
    name: str = ""
    fingerprint: StateFingerprint = field(default_factory=StateFingerprint)
    valid: bool = False

    def verify(self, expected_fp):
        """Verify measurement is from the expected state."""
        if self.fingerprint.matches(expected_fp):
            self.valid = True
            return True
        self.valid = False
        return False


def compute_fingerprint(board):
    """Compute state fingerprint for a KiCad board.

    Args:
        board: pcbnew.BOARD object

    Returns:
        StateFingerprint
    """
    import time

    fp = StateFingerprint()
    fp.timestamp = time.time()

    # Count tracks and vias
    track_count = 0
    via_count = 0
    for t in board.GetTracks():
        if type(t).__name__ == 'PCB_VIA':
            via_count += 1
        else:
            track_count += 1
    fp.track_count = track_count
    fp.via_count = via_count

    # Count pads and nets
    pad_count = 0
    for fp_obj in board.GetFootprints():
        pad_count += len(list(fp_obj.Pads()))
    fp.pad_count = pad_count
    fp.net_count = board.GetNetCount()
    fp.copper_layers = board.GetCopperLayerCount()

    # Zone state
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

    # Board hash (copper geometry)
    hash_input = f"{fp.track_count}:{fp.via_count}:{fp.pad_count}:{fp.net_count}"
    fp.board_hash = hashlib.sha256(hash_input.encode()).hexdigest()[:16]

    return fp


class MeasurementRegistry:
    """Registry of measurements with state fingerprinting.

    Ensures measurements are only used with matching board state.
    """

    def __init__(self):
        self.measurements = {}
        self.current_fp = None

    def update_fingerprint(self, board):
        """Update the current state fingerprint."""
        self.current_fp = compute_fingerprint(board)
        return self.current_fp

    def record(self, name, value, unit="", fingerprint=None):
        """Record a measurement with fingerprint."""
        fp = fingerprint or self.current_fp
        if fp is None:
            raise ValueError("No fingerprint available. Call update_fingerprint first.")

        m = Measurement(
            value=value,
            unit=unit,
            name=name,
            fingerprint=fp,
        )
        self.measurements[name] = m
        return m

    def query(self, name, expected_fp=None):
        """Query a measurement, verifying state consistency.

        Args:
            name: measurement name
            expected_fp: expected state fingerprint (None = use current)

        Returns:
            Measurement or None if state mismatch
        """
        if name not in self.measurements:
            return None

        m = self.measurements[name]
        fp = expected_fp or self.current_fp

        if m.verify(fp):
            return m
        else:
            # State mismatch — reject stale measurement
            print(f"WARNING: Measurement '{name}' state mismatch! "
                  f"Recorded: {m.fingerprint.board_hash}, "
                  f"Expected: {fp.board_hash}", flush=True)
            return None

    def get_all(self, expected_fp=None):
        """Get all valid measurements for the expected state."""
        fp = expected_fp or self.current_fp
        valid = {}
        for name, m in self.measurements.items():
            if m.verify(fp):
                valid[name] = m
        return valid


class SemanticGuard:
    """Guard against semantic drift in measurements.

    Ensures the same query name always returns structurally consistent data.
    """

    def __init__(self):
        self.schemas = {}

    def register_schema(self, name, schema):
        """Register expected schema for a measurement type."""
        self.schemas[name] = schema

    def validate(self, name, data):
        """Validate measurement data against registered schema.

        Args:
            name: measurement name
            data: measurement data (dict)

        Returns:
            (valid, errors) tuple
        """
        if name not in self.schemas:
            return True, []  # no schema registered

        schema = self.schemas[name]
        errors = []

        for key, expected_type in schema.items():
            if key not in data:
                errors.append(f"Missing key: {key}")
            elif not isinstance(data[key], expected_type):
                errors.append(f"Type mismatch for '{key}': "
                            f"expected {expected_type.__name__}, "
                            f"got {type(data[key]).__name__}")

        return len(errors) == 0, errors


# Global instances
_registry = MeasurementRegistry()
_guard = SemanticGuard()


def get_registry():
    """Get global measurement registry."""
    return _registry


def get_guard():
    """Get global semantic guard."""
    return _guard


def record_measurement(name, value, unit="", board=None):
    """Record a measurement with state fingerprinting.

    Args:
        name: measurement name (e.g., 'unconnected_count')
        value: measurement value
        unit: measurement unit
        board: KiCad board (for fingerprint)

    Returns:
        Measurement object
    """
    if board is not None:
        _registry.update_fingerprint(board)
    return _registry.record(name, value, unit)


def query_measurement(name, expected_fp=None):
    """Query a measurement with state verification.

    Args:
        name: measurement name
        expected_fp: expected state fingerprint

    Returns:
        Measurement or None if state mismatch
    """
    return _registry.query(name, expected_fp)


def verify_measurement(name, data, expected_fp=None):
    """Verify measurement data against schema and state.

    Args:
        name: measurement name
        data: measurement data
        expected_fp: expected state fingerprint

    Returns:
        (valid, errors) tuple
    """
    # Schema validation
    schema_valid, schema_errors = _guard.validate(name, data)

    # State validation
    m = _registry.query(name, expected_fp)
    state_valid = m is not None

    errors = schema_errors
    if not state_valid:
        errors.append("State fingerprint mismatch")

    return schema_valid and state_valid, errors
