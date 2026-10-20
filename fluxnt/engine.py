"""Pipeline engine: six-stage DAG executor with gate feedback loop and audit."""
from __future__ import annotations

import time
from pathlib import Path

import yaml

from fluxnt import gates as gate_mod
from fluxnt import stages as stage_mod
from fluxnt.models import Artifact, GateStatus, RunReport, StageRun, StageStatus


def load_pipeline(pipeline_path: Path) -> dict:
    return yaml.safe_load(pipeline_path.read_text(encoding="utf-8"))


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

    report = RunReport(project=project_dir.name)
    project_dir.mkdir(parents=True, exist_ok=True)

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
            continue

        feedback: list[str] = []
        for iteration in range(1, max_iter + 1):
            ctx = stage_mod.StageContext(project_dir=project_dir, stage_name=name,
                                         feedback=feedback, config=cfg)
            runner(ctx)
            run.notes.extend(ctx.notes)
            run.iterations = iteration
            # evaluate gates
            run.gates = [gate_mod.run_gate(g, project_dir, cfg.get("outputs", []), cfg)
                         for g in cfg.get("gates", [])]
            failed = [g for g in run.gates if g.status == GateStatus.FAIL]
            if not failed:
                break
            feedback = [f"[{g.name}] {g.summary}" +
                        ("; ".join(str(f.get("issue", "")) for f in g.findings[:5]))
                        for g in failed]
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

    report.finished = time.time()
    out = report.save(project_dir)
    md = project_dir / "reports" / "latest.md"
    md.write_text(report.markdown(), encoding="utf-8")
    print(f"[flux] report -> {out}")
    return report
