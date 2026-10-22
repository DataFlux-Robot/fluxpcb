"""Pipeline engine: six-stage DAG executor with gate feedback loop and audit."""
from __future__ import annotations

import json
import time
from pathlib import Path

import yaml

from fluxpcb import gates as gate_mod
from fluxpcb import stages as stage_mod
from fluxpcb.models import Artifact, GateStatus, RunReport, StageRun, StageStatus


def load_pipeline(pipeline_path: Path) -> dict:
    return yaml.safe_load(pipeline_path.read_text(encoding="utf-8"))


def emit_event(project_dir: Path, ev: str, **fields) -> None:
    """Append a JSON-line event for the live dashboard (reports/events.jsonl)."""
    try:
        p = project_dir / "reports" / "events.jsonl"
        p.parent.mkdir(parents=True, exist_ok=True)
        rec = {"t": round(time.time(), 3), "ev": ev, **fields}
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError:
        pass  # event stream is best-effort; never block the pipeline


def run_pipeline(project_dir: Path, pipeline_path: Path | None = None,
                 only_stages: list[str] | None = None) -> RunReport:
    """Execute the six-stage pipeline over a project directory.

    For each stage: run stage (with feedback from previous iteration),
    evaluate gates, on failure retry with structured feedback (bounded),
    record artifacts + gates in the run report.
    """
    if pipeline_path is None:
        pipeline_path = Path(__file__).resolve().parent.parent / "flux.yaml"
    pipeline = load_pipeline(pipeline_path)
    max_iter = int((pipeline.get("retry") or {}).get("max_iterations", 3))
    stages_cfg = pipeline.get("stages", {})
    optional_gates = set((pipeline.get("gate_policy") or {}).get("optional", []))

    report = RunReport(project=project_dir.name)
    project_dir.mkdir(parents=True, exist_ok=True)
    emit_event(project_dir, "run_start", project=project_dir.name)

    for name, cfg in stages_cfg.items():
        if only_stages and name not in only_stages:
            continue
        t0 = time.time()
        run = StageRun(stage=name, title=cfg.get("title", name))
        runner = stage_mod.get_runner(cfg.get("runner", name))
        if runner is None:
            run.status = StageStatus.BLOCKED
            run.notes.append(f"runner未注册: {cfg.get('runner', name)}")
            report.stages.append(run)
            emit_event(project_dir, "stage_end", stage=name, status="blocked")
            continue

        feedback: list[str] = []
        for iteration in range(1, max_iter + 1):
            emit_event(project_dir, "stage_start", stage=name, iter=iteration)
            ctx = stage_mod.StageContext(project_dir=project_dir, stage_name=name,
                                         feedback=feedback, config=cfg)
            runner(ctx)
            run.notes.extend(ctx.notes)
            run.iterations = iteration
            # evaluate gates
            run.gates = [gate_mod.run_gate(g, project_dir, cfg.get("outputs", []), cfg)
                         for g in cfg.get("gates", [])]
            for g in run.gates:
                if g.status == GateStatus.BLOCKED and g.name in optional_gates:
                    g.status = GateStatus.SKIPPED
                    g.summary += " [可选增强门禁: 求解器缺席, 用例已生成]"
                emit_event(project_dir, "gate", stage=name, gate=g.name,
                           status=g.status.value, summary=g.summary)
            failed = [g for g in run.gates if g.status == GateStatus.FAIL]
            if not failed:
                break
            feedback = [f"[{g.name}] {g.summary}" +
                        ("; ".join(str(f.get("issue", "")) for f in g.findings[:5]))
                        for g in failed]
            # structured route-repair directives when available (K4 knowhow)
            _rr = project_dir / "pcb" / "board" / "route_report.json"
            if _rr.exists():
                try:
                    import json as _json
                    for line in _json.loads(_rr.read_text(encoding="utf-8"))                             .get("structured_feedback", [])[:5]:
                        feedback.append(line)
                except (ValueError, OSError):
                    pass
            if iteration == max_iter:
                run.status = StageStatus.FAILED

        # capture artifacts (files/dirs listed in outputs, existing only)
        for out in cfg.get("outputs", []):
            p = project_dir / out
            if p.is_file():
                run.artifacts.append(Artifact.capture(project_dir, out))
            elif p.is_dir():
                for f in sorted(p.rglob("*")):
                    if f.is_file():
                        run.artifacts.append(
                            Artifact.capture(project_dir, str(f.relative_to(project_dir))))
        run.duration_s = time.time() - t0
        report.stages.append(run)
        emit_event(project_dir, "stage_end", stage=name,
                   status=run.status.value, duration_s=round(run.duration_s, 1),
                   artifacts=len(run.artifacts))

    report.finished = time.time()
    out = report.save(project_dir)
    md = project_dir / "reports" / "latest.md"
    md.write_text(report.markdown(), encoding="utf-8")
    emit_event(project_dir, "run_end", released=report.released,
               report=str(out.relative_to(project_dir)))
    print(f"[flux] report -> {out}")
    return report


def verify_only(project_dir: Path, pipeline_path: Path | None = None) -> RunReport:
    """Re-evaluate all gates against on-disk artifacts WITHOUT regenerating anything.

    Use after manual fixes (e.g. hand routing): produces a truthful latest.json
    from current files, never overwrites board/schematic outputs.
    """
    if pipeline_path is None:
        pipeline_path = Path(__file__).resolve().parent.parent / "flux.yaml"
    pipeline = load_pipeline(pipeline_path)
    stages_cfg = pipeline.get("stages", {})
    optional_gates = set((pipeline.get("gate_policy") or {}).get("optional", []))

    report = RunReport(project=project_dir.name)
    report.started = time.time()
    emit_event(project_dir, "verify_start", project=project_dir.name)

    for name, cfg in stages_cfg.items():
        t0 = time.time()
        run = StageRun(stage=name, title=cfg.get("title", name))
        run.notes.append("verify-only: 门禁对磁盘产物复检, 未重新生成")
        run.gates = [gate_mod.run_gate(g, project_dir, cfg.get("outputs", []), cfg)
                     for g in cfg.get("gates", [])]
        for g in run.gates:
            if g.status == GateStatus.BLOCKED and g.name in optional_gates:
                g.status = GateStatus.SKIPPED
                g.summary += " [可选增强门禁: 求解器缺席, 用例已生成]"
            emit_event(project_dir, "gate", stage=name, gate=g.name,
                       status=g.status.value, summary=g.summary)
        if any(g.status == GateStatus.FAIL for g in run.gates):
            run.status = StageStatus.FAILED
        for out in cfg.get("outputs", []):
            p = project_dir / out
            if p.is_file():
                run.artifacts.append(Artifact.capture(project_dir, out))
            elif p.is_dir():
                for f in sorted(p.rglob("*")):
                    if f.is_file():
                        run.artifacts.append(
                            Artifact.capture(project_dir, str(f.relative_to(project_dir))))
        run.duration_s = time.time() - t0
        report.stages.append(run)
        emit_event(project_dir, "stage_end", stage=name,
                   status=run.status.value, duration_s=round(run.duration_s, 1),
                   artifacts=len(run.artifacts))

    report.finished = time.time()
    out = report.save(project_dir)
    (project_dir / "reports" / "latest.md").write_text(report.markdown(), encoding="utf-8")
    emit_event(project_dir, "run_end", released=report.released,
               report=str(out.relative_to(project_dir)))
    print(f"[flux] verify report -> {out}")
    return report
