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

    report = RunReport(project=project_dir.name,
                       required_stages=_required_stages(stages_cfg),
                       input_hash=_input_hash(project_dir))
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
    # 发布判定(复审修复): 导出在 run_end 之前, 判定含导出校验;
    # 局部运行(only_stages)缺必需阶段时 released 恒 False
    if report.released:
        report.fab_export = _export_fab(project_dir)
        out = report.save(project_dir)   # 导出结果入档
    check = report.release_check(_input_hash(project_dir))
    emit_event(project_dir, "run_end", released=check["released"],
               release_check=check,
               report=str(out.relative_to(project_dir)))
    print(f"[flux] report -> {out}")
    return report


def _input_hash(project_dir: Path) -> str:
    """运行输入指纹: 板+工程+规则+叠层 —— 证据必须对应当前输入。"""
    import hashlib
    h = hashlib.sha256()
    for pat in ("*.kicad_pcb", "*.kicad_pro", "*.kicad_dru",
                "stackup.yaml", "net_classes.json"):
        for f in sorted(project_dir.rglob(pat)):
            try:
                h.update(f.name.encode())
                h.update(f.read_bytes())
            except OSError:
                pass
    return h.hexdigest()[:16]


def _required_stages(stages_cfg: dict) -> list:
    """必需阶段全集: 流水线定义中未标 optional 的阶段。"""
    return [n for n, cfg in stages_cfg.items()
            if not (cfg or {}).get("optional")]


def _export_fab(project_dir: Path) -> dict:
    """RELEASED -> Gerber/drill/positions fab package (K13: this used to
    be a hand-run command outside the pipeline; the one-sentence goal
    requires the chain to end at manufacturable files).
    复审修复: 逐命令校验返回码 + 产出物存在性 —— 导出失败必须可见,
    不再静默吞(rc 不查=假发布)。"""
    import shutil
    import subprocess
    result = {"ok": False, "files": 0, "errors": []}
    cli = shutil.which("kicad-cli")
    if not cli:
        result["errors"].append("kicad-cli 缺席")
        return result
    pcbs = sorted((project_dir / "pcb").rglob("*.kicad_pcb"))
    if not pcbs:
        result["errors"].append("无板文件")
        return result
    for pcb in pcbs:
        fab = pcb.parent / "fab"
        fab.mkdir(exist_ok=True)
        for args in (
            ["pcb", "export", "gerbers", "-o", str(fab) + "/",
             "--layers", "F.Cu,In1.Cu,In2.Cu,In3.Cu,In4.Cu,B.Cu,"
             "F.SilkS,B.SilkS,F.Mask,B.Mask,Edge.Cuts,F.Paste,B.Paste",
             str(pcb)],
            ["pcb", "export", "drill", "-o", str(fab), str(pcb)],
            ["pcb", "export", "pos", "-o", str(fab / "positions.csv"),
             "--format", "csv", str(pcb)],
        ):
            r = subprocess.run([cli, *args], capture_output=True,
                               timeout=900)
            if r.returncode != 0:
                result["errors"].append(
                    "%s rc=%d: %s" % (args[1:3], r.returncode,
                                      (r.stderr or b"")[-80:]))
        n = len([f for f in fab.glob("*") if f.is_file()
                 and f.stat().st_size > 0])
        result["files"] += n
        if n == 0:
            result["errors"].append("%s: 导出物为空" % pcb.name)
        emit_event(project_dir, "fab_exported", pcb=pcb.name, files=n)
    result["ok"] = not result["errors"]
    return result


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

    report = RunReport(project=project_dir.name,
                       required_stages=_required_stages(stages_cfg),
                       input_hash=_input_hash(project_dir))
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
