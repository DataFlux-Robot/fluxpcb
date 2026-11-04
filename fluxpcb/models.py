"""Core data models: stages, gates, artifacts, run reports."""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Optional


class GateStatus(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    BLOCKED = "blocked"   # external tool missing / input artifact missing
    SKIPPED = "skipped"


class StageStatus(str, Enum):
    OK = "ok"
    FAILED = "failed"     # gates failed after max_iterations
    BLOCKED = "blocked"


@dataclass
class GateResult:
    name: str
    status: GateStatus
    summary: str = ""
    details: dict[str, Any] = field(default_factory=dict)
    findings: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "name": self.name, "status": self.status.value,
            "summary": self.summary, "details": self.details, "findings": self.findings,
        }


@dataclass
class Artifact:
    """A versioned, hash-audited output file."""
    path: Path            # relative to project dir
    sha256: str
    bytes: int
    mtime: float

    @classmethod
    def capture(cls, project_dir: Path, rel_path: str) -> "Artifact":
        p = project_dir / rel_path
        data = p.read_bytes()
        return cls(path=Path(rel_path), sha256=hashlib.sha256(data).hexdigest(),
                   bytes=len(data), mtime=p.stat().st_mtime)

    def to_dict(self) -> dict:
        return {"path": str(self.path), "sha256": self.sha256,
                "bytes": self.bytes, "mtime": self.mtime}


@dataclass
class StageRun:
    stage: str
    title: str
    status: StageStatus = StageStatus.OK
    iterations: int = 0
    artifacts: list[Artifact] = field(default_factory=list)
    gates: list[GateResult] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    duration_s: float = 0.0

    @property
    def all_gates_pass_or_ok(self) -> bool:
        return all(g.status in (GateStatus.PASS, GateStatus.SKIPPED) for g in self.gates)

    def to_dict(self) -> dict:
        return {
            "stage": self.stage, "title": self.title, "status": self.status.value,
            "iterations": self.iterations, "artifacts": [a.to_dict() for a in self.artifacts],
            "gates": [g.to_dict() for g in self.gates], "notes": self.notes,
            "duration_s": round(self.duration_s, 3),
        }


@dataclass
class RunReport:
    project: str
    started: float = field(default_factory=time.time)
    finished: Optional[float] = None
    stages: list[StageRun] = field(default_factory=list)
    # 发布语义(复审修复): required_stages = 流水线定义的必需阶段全集;
    # input_hash = 运行时输入指纹(证据必须对应当前输入);
    # fab_export = 加工导出结果(成功+校验) —— 三者齐备才可发布
    required_stages: list = field(default_factory=list)
    input_hash: str = ""
    fab_export: dict = field(default_factory=dict)

    @property
    def released(self) -> bool:
        # 复现案: 仅跑 requirements 阶段 2 门禁通过即 released=true ——
        # 旧版只查本次执行过的 stages, 局部通过被误判为发布。
        # 现: 必需阶段全集齐全且通过 + 加工导出(已跑则必须 ok)。
        if not self.stages:
            return False
        by = {s.stage: s for s in self.stages}
        if self.required_stages:
            if any(n not in by for n in self.required_stages):
                return False
        if not all(s.status == StageStatus.OK and s.all_gates_pass_or_ok
                   for s in self.stages):
            return False
        if self.fab_export and not self.fab_export.get("ok"):
            return False
        return True

    def release_check(self, current_input_hash: str = "") -> dict:
        """独立发布检查(判定报告, 不只布尔): 必需阶段/实际阶段/
        证据哈希/导出校验四问齐答 —— 局部运行只能说明局部阶段通过。"""
        by = {s.stage: s for s in self.stages}
        missing = [n for n in self.required_stages if n not in by]
        stale = (bool(self.input_hash) and bool(current_input_hash)
                 and self.input_hash != current_input_hash)
        reasons = []
        if missing:
            reasons.append("缺必需阶段: %s" % ",".join(missing))
        if stale:
            reasons.append("证据哈希与当前输入不符(陈旧运行)")
        if self.fab_export and not self.fab_export.get("ok"):
            reasons.append("加工导出未通过: %s"
                           % (self.fab_export.get("errors") or ["?"]))
        return {"released": self.released and not stale,
                "required": list(self.required_stages),
                "ran": sorted(by),
                "missing_stages": missing,
                "input_hash_ok": not stale,
                "fab_export": self.fab_export,
                "reasons": reasons}

    def to_dict(self) -> dict:
        return {
            "project": self.project,
            "started": self.started,
            "finished": self.finished,
            "released": self.released,
            "required_stages": self.required_stages,
            "input_hash": self.input_hash,
            "fab_export": self.fab_export,
            "stages": [s.to_dict() for s in self.stages],
        }

    def save(self, out_dir: Path) -> Path:
        out = out_dir / "reports" / f"run_{time.strftime('%Y%m%d_%H%M%S')}.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=1),
                       encoding="utf-8")
        latest = out_dir / "reports" / "latest.json"
        latest.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=1),
                          encoding="utf-8")
        return out

    def markdown(self) -> str:
        lines = [f"# FluxPCB 运行报告 — {self.project}", ""]
        verdict = "🟢 RELEASED(全门禁通过)" if self.released else "🔴 NOT RELEASED"
        lines.append(f"**结论: {verdict}**")
        lines.append("")
        lines.append("| 环节 | 状态 | 迭代 | 产出物 | 门禁 |")
        lines.append("|---|---|---|---|---|")
        for s in self.stages:
            gates = ", ".join(
                f"{g.name}:{'✅' if g.status == GateStatus.PASS else '⛔' if g.status == GateStatus.FAIL else '⏸️' if g.status == GateStatus.BLOCKED else '⏭️'}"
                for g in s.gates) or "—"
            lines.append(f"| {s.title} | {s.status.value} | {s.iterations} | "
                         f"{len(s.artifacts)} | {gates} |")
        lines.append("")
        for s in self.stages:
            for g in s.gates:
                if g.status in (GateStatus.FAIL, GateStatus.BLOCKED) and g.summary:
                    lines.append(f"- **{s.title}/{g.name}**: {g.summary}")
        return "\n".join(lines)
