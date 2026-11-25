#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""contracts — Shared data contracts for the 12-component fluxpcb architecture.

All cross-component data flows through these dataclasses. Components MUST
use these types (not ad-hoc dicts) to prevent "错位和简化" (misalignment).

Component mapping:
  1. MetaDevLoop       — uses RoutingDiagnosis/RoutingCandidate/RoutingResult
  2. MetaMetaLoop      — uses HarnessDiff/Trajectory
  3. DevRouteLoop      — orchestrates NetTask→DispatchPlan→RouteResult
  4. PCBDesignAgent    — outputs BoardDesignPlan
  5. CommRuntime       — binds StateFingerprint to every measurement
  6. SubagentTeam      — outputs SignalClass + ordered NetTask
  7. DispatchRuntime   — outputs DispatchPlan per NetTask
  8. FeedbackRuntime   — consumes RouteResult→FeedbackEvent→Trajectory
  9. NCERRouter        — consumes NetTask+DispatchPlan→RouteResult
  10. VerifierSuite    — outputs VerificationReport
  11. ManufacturingInspector — outputs ManufacturingReport
  12. GroundingMap     — consumes all reports
"""
from __future__ import annotations

import enum
import hashlib
import json
import time
from dataclasses import dataclass, field
from typing import Any, Optional


# ──────────────────────────────────────────────────────────────────────
# Enumerations
# ──────────────────────────────────────────────────────────────────────

class SignalClass(str, enum.Enum):
    """Signal classification (Component 6 output)."""
    CRITICAL = "critical"       # Must route first (safety-critical)
    DIFFERENTIAL = "differential"  # Diff pairs (CAN, USB, etc.)
    CLOCK = "clock"             # Clock signals
    POWER = "power"             # Power nets (VCC, GND, etc.)
    NORMAL = "normal"           # General signals


class ViaType(str, enum.Enum):
    """Via type (Component 7 dispatch)."""
    THROUGH = "through"
    BLIND = "blind"
    BURIED = "buried"
    MICRO = "micro"


class RoutingType(str, enum.Enum):
    """Routing style (Component 7 dispatch)."""
    NORMAL = "normal"
    DIFF_PAIR = "diff_pair"
    EQUAL_LENGTH = "equal_length"
    POWER = "power"


class BGAFanoutStyle(str, enum.Enum):
    """BGA fanout strategy (Component 7 dispatch)."""
    DOGBONE = "dogbone"
    INLINE = "inline"
    VIA_IN_PAD = "via_in_pad"
    STAGGERED = "staggered"


class Planner(str, enum.Enum):
    """Which planner made a decision (Component 7 dispatch)."""
    LLM = "llm"
    RULE = "rule"
    BASELINE = "baseline"


# ──────────────────────────────────────────────────────────────────────
# Core data contracts
# ──────────────────────────────────────────────────────────────────────

@dataclass
class StateFingerprint:
    """Fingerprint of board state (Component 5 binding)."""
    board_hash: str = ""
    zone_state: str = "unknown"
    track_count: int = 0
    via_count: int = 0
    pad_count: int = 0
    net_count: int = 0
    copper_layers: int = 0
    timestamp: float = 0.0

    def matches(self, other: "StateFingerprint") -> bool:
        return (self.board_hash == other.board_hash and
                self.zone_state == other.zone_state and
                self.track_count == other.track_count and
                self.via_count == other.via_count)

    def to_dict(self) -> dict:
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
class BoardState:
    """Complete board state (Component 5)."""
    board: Any = None  # pcbnew.BOARD
    fingerprint: StateFingerprint = field(default_factory=StateFingerprint)
    net_pads: dict[str, list[tuple[float, float, int]]] = field(default_factory=dict)
    stackup: dict = field(default_factory=dict)
    board_path: str = ""


@dataclass
class Corridor:
    """Routing corridor (Component 4 output)."""
    x1: float = 0.0
    y1: float = 0.0
    x2: float = 0.0
    y2: float = 0.0
    layers: list[int] = field(default_factory=list)


@dataclass
class NetTask:
    """2-pin routing task (Component 6 output)."""
    net: str = ""
    a: tuple[float, float, int] = (0.0, 0.0, 0)  # (x, y, layer_idx)
    b: tuple[float, float, int] = (0.0, 0.0, 0)
    cls: SignalClass = SignalClass.NORMAL
    priority: float = 0.0
    corridor: Optional[Corridor] = None
    # attribution (declared fields: a private attribute is lost on any
    # serialisation round-trip, which is how this was silently dropped)
    specialist: str = ""
    repair_operator: str = ""
    specialist_rationale: str = ""


@dataclass
class StackupPlan:
    """Stackup plan (Component 4 output)."""
    layers: int = 2
    er: float = 4.5
    layer_order: list[str] = field(default_factory=list)
    impedance_targets: list[dict] = field(default_factory=list)
    copper_thickness_mm: list[float] = field(default_factory=list)
    dielectric_thickness_mm: list[float] = field(default_factory=list)


@dataclass
class PlacementPlan:
    """Placement plan (Component 4 output)."""
    placements: list[dict] = field(default_factory=list)
    corridors: list[Corridor] = field(default_factory=list)


@dataclass
class RouteGuidePlan:
    """Route guide (Component 4 output, reused from bga_planner)."""
    connections: list[dict] = field(default_factory=list)
    corridors: list[Corridor] = field(default_factory=list)
    via_regions: list[dict] = field(default_factory=list)
    order: list[str] = field(default_factory=list)


@dataclass
class NetClassRules:
    """Net class rules (Component 4 output)."""
    rules: dict[str, dict] = field(default_factory=dict)
    clearances: dict[str, float] = field(default_factory=dict)
    widths: dict[str, float] = field(default_factory=dict)


@dataclass
class BoardDesignPlan:
    """Complete design plan (Component 4 output)."""
    stackup: StackupPlan = field(default_factory=StackupPlan)
    placement: PlacementPlan = field(default_factory=PlacementPlan)
    route_guide: RouteGuidePlan = field(default_factory=RouteGuidePlan)
    net_class_rules: NetClassRules = field(default_factory=NetClassRules)


@dataclass
class DispatchPlan:
    """Per-net routing params (Component 7 output)."""
    net: str = ""
    via_type: ViaType = ViaType.THROUGH
    via_size: tuple[float, float] = (0.5, 0.3)  # (diameter, drill) mm
    track_width: float = 0.127
    layer_pref: list[int] = field(default_factory=list)
    routing_type: RoutingType = RoutingType.NORMAL
    bga_fanout_style: BGAFanoutStyle = BGAFanoutStyle.DOGBONE
    escape_strategy: str = ""
    planner: Planner = Planner.RULE
    decider: dict = field(default_factory=dict)
    rationale: str = ""


@dataclass
class RouteResult:
    """Routing result (Component 9 output)."""
    routed: list[dict] = field(default_factory=list)
    opens: int = 0
    shorts: int = 0
    vias: int = 0
    drc_errors: int = 0
    completion_pct: float = 0.0
    track_mm: float = 0.0
    seconds: float = 0.0
    fingerprint: StateFingerprint = field(default_factory=StateFingerprint)
    metrics: dict = field(default_factory=dict)


@dataclass
class VerificationResult:
    """Single verification result (Component 10 output).

    `status='unknown'` is a result: the check could not decide, and
    `needed_information` says what is missing.  It is never a pass.
    """
    name: str = ""
    status: str = "unknown"  # "pass", "fail", "warning", "skip", "unknown"
    severity: str = "error"  # "error", "warning", "info"
    findings: list[dict] = field(default_factory=list)
    evidence: dict = field(default_factory=dict)
    needed_information: list[str] = field(default_factory=list)


@dataclass
class VerificationReport:
    """Aggregated verification report (Component 10 output)."""
    results: list[VerificationResult] = field(default_factory=list)
    passed: bool = False
    fingerprint: StateFingerprint = field(default_factory=StateFingerprint)

    def __post_init__(self):
        self.passed = all(r.status in ("pass", "skip") for r in self.results)

    @property
    def needed_information(self) -> list:
        """What the checks could not decide, deduplicated."""
        out = []
        for r in self.results:
            for m in r.needed_information:
                if m and m not in out:
                    out.append(m)
        return out

    @property
    def unknown(self) -> list:
        return [r.name for r in self.results if r.status == "unknown"]


@dataclass
class FeedbackEvent:
    """Feedback event (Component 8 output)."""
    action: dict = field(default_factory=dict)
    result: dict = field(default_factory=dict)
    delta: dict = field(default_factory=dict)
    fingerprint: StateFingerprint = field(default_factory=StateFingerprint)
    timestamp: float = field(default_factory=time.time)


@dataclass
class Trajectory:
    """Execution trajectory (Component 8 output)."""
    events: list[FeedbackEvent] = field(default_factory=list)
    grade: float = 0.0
    board_hash: str = ""


# ──────────────────────────────────────────────────────────────────────
# HarnessPro contracts (Components 1-2)
# ──────────────────────────────────────────────────────────────────────

@dataclass
class RoutingDiagnosis:
    """Diagnosis of routing failures (Component 1)."""
    board_path: str = ""
    completion_pct: float = 0.0
    drc_errors: int = 0
    unconnected: int = 0
    failure_modes: list[str] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)
    fingerprint: StateFingerprint = field(default_factory=StateFingerprint)


@dataclass
class RoutingCandidate:
    """Candidate strategy (Component 1)."""
    name: str = ""
    category: str = ""
    params: dict = field(default_factory=dict)
    expected_improvement: float = 0.0
    risk: float = 0.0


@dataclass
class RoutingResult:
    """Result of evaluating a candidate (Component 1)."""
    candidate: RoutingCandidate = field(default_factory=RoutingCandidate)
    completion_pct: float = 0.0
    drc_errors: int = 0
    unconnected: int = 0
    seconds: float = 0.0
    score: float = 0.0
    fingerprint: StateFingerprint = field(default_factory=StateFingerprint)

    def __post_init__(self):
        self.score = self.completion_pct - self.drc_errors * 0.1


@dataclass
class HarnessDiff:
    """Harness improvement diff (Component 2)."""
    description: str = ""
    changes: dict = field(default_factory=dict)
    git_commit: str = ""
    frozen_test_score: float = 0.0
    heldout_delta: float = None      # measured on a held-out family split
    scope_narrowed: bool = False     # SII: any declared dimension removed
    authority: str = ''              # named human, when a gate changed


# ──────────────────────────────────────────────────────────────────────
# Manufacturing contracts (Component 11)
# ──────────────────────────────────────────────────────────────────────

@dataclass
class ManufacturingReport:
    """Manufacturing readiness report (Component 11)."""
    panel: dict = field(default_factory=dict)
    dfm_violations: list[dict] = field(default_factory=list)
    ipc7351_violations: list[dict] = field(default_factory=list)
    ipc2221_violations: list[dict] = field(default_factory=list)
    fab_files: list[str] = field(default_factory=list)
    passed: bool = False
    fingerprint: StateFingerprint = field(default_factory=StateFingerprint)

    def __post_init__(self):
        self.passed = (len(self.dfm_violations) == 0 and
                       len(self.ipc7351_violations) == 0 and
                       len(self.ipc2221_violations) == 0)


# ──────────────────────────────────────────────────────────────────────
# Serialization helpers
# ──────────────────────────────────────────────────────────────────────

def to_json(obj: Any) -> str:
    """Serialize a contract to JSON."""
    def _default(o):
        if hasattr(o, '__dataclass_fields__'):
            return {k: getattr(o, k) for k in o.__dataclass_fields__}
        if isinstance(o, enum.Enum):
            return o.value
        return str(o)
    return json.dumps(obj, default=_default, ensure_ascii=False)


def from_json(cls: type, data: str | dict) -> Any:
    """Deserialize JSON to a contract."""
    if isinstance(data, str):
        data = json.loads(data)
    return cls(**data)
