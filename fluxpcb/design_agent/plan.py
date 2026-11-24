"""Typed routing plan — the artifact Component 4 hands to the executor.

A plan is data: capacity verdict, layer/escape decisions, net order and
the nets to pre-route before the global router runs.  Everything is
content-addressed so a bench result can be cached by
(board_sha, plan_digest) and never re-run twice.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field

PLAN_VERSION = 1
CAPACITY_VERDICTS = ("sufficient", "infeasible", "unknown")


def canonical_json(payload: dict) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False)


@dataclass(frozen=True)
class CapacityClaim:
    """Is the tightest footprint escapable at this stackup?"""

    verdict: str = "unknown"
    traces_per_channel: int = 0
    rings: int = 0                    # required ball rings (max_ring + 1)
    signal_layers: int = 0
    margin: int = 0                   # capacity_rings - rings
    evidence: dict = field(default_factory=dict)
    suggestions: tuple = ()

    def as_dict(self) -> dict:
        d = asdict(self)
        d["suggestions"] = list(self.suggestions)
        return d


@dataclass
class RoutingPlan:
    board_sha: str
    rule_digest: str
    capacity: CapacityClaim
    layer_plan: dict = field(default_factory=dict)
    escape_regions: list = field(default_factory=list)
    net_order: list = field(default_factory=list)
    pre_routed_nets: list = field(default_factory=list)
    budgets: dict = field(default_factory=dict)
    claims: list = field(default_factory=list)
    version: int = PLAN_VERSION

    # -- identity -----------------------------------------------------
    def payload(self) -> dict:
        d = {
            "version": self.version,
            "board_sha": self.board_sha,
            "rule_digest": self.rule_digest,
            "capacity": self.capacity.as_dict(),
            "layer_plan": self.layer_plan,
            "escape_regions": self.escape_regions,
            "net_order": self.net_order,
            "pre_routed_nets": self.pre_routed_nets,
            "budgets": self.budgets,
            "claims": self.claims,
        }
        return d

    def digest(self) -> str:
        return hashlib.sha256(
            canonical_json(self.payload()).encode("utf-8")).hexdigest()

    def to_json(self, path: str) -> str:
        body = self.payload()
        body["plan_digest"] = self.digest()
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(body, fh, indent=1, ensure_ascii=False)
        return body["plan_digest"]

    @classmethod
    def from_json(cls, path: str) -> "RoutingPlan":
        body = json.load(open(path, encoding="utf-8"))
        cap = body.get("capacity") or {}
        return cls(
            board_sha=body.get("board_sha", ""),
            rule_digest=body.get("rule_digest", ""),
            capacity=CapacityClaim(
                verdict=cap.get("verdict", "unknown"),
                traces_per_channel=int(cap.get("traces_per_channel", 0)),
                rings=int(cap.get("rings", 0)),
                signal_layers=int(cap.get("signal_layers", 0)),
                margin=int(cap.get("margin", 0)),
                evidence=cap.get("evidence") or {},
                suggestions=tuple(cap.get("suggestions") or ()),
            ),
            layer_plan=body.get("layer_plan") or {},
            escape_regions=body.get("escape_regions") or [],
            net_order=body.get("net_order") or [],
            pre_routed_nets=body.get("pre_routed_nets") or [],
            budgets=body.get("budgets") or {},
            claims=body.get("claims") or [],
            version=int(body.get("version", PLAN_VERSION)),
        )

    # -- contract -----------------------------------------------------
    def validate(self) -> list:
        """Return the list of contract violations (empty == valid)."""
        errs = []
        if not self.board_sha:
            errs.append("board_sha missing")
        if not self.rule_digest:
            errs.append("rule_digest missing")
        if self.capacity.verdict not in CAPACITY_VERDICTS:
            errs.append(f"bad capacity verdict {self.capacity.verdict!r}")
        if self.capacity.verdict == "infeasible" and self.pre_routed_nets:
            errs.append("infeasible plan must not pre-route nets")
        if len(set(self.net_order)) != len(self.net_order):
            errs.append("net_order has duplicates")
        if not set(self.pre_routed_nets) <= set(self.net_order):
            errs.append("pre_routed nets must appear in net_order")
        return errs

    def is_routable(self) -> bool:
        # 'unknown' = no multi-pad footprint to prove; routing may proceed
        # with an explicit assumption.  Only a proven infeasible plan stops.
        return self.capacity.verdict != "infeasible" and not self.validate()
