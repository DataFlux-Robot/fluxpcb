# -*- coding: utf-8 -*-
"""Board State Kernel — state.py: the single truth interface.

THE core problem this kernel exists to solve (K21, generalized from
K7-K20): the board was a typeless, invariant-free, transaction-free,
semantics-free shared mutable file. Every consumer derived its own
meaning from it (four divergent connectivity counters, each gameable),
every engine mutated it in place (shorts, half-saved crash states,
mutual destruction), and no lesson ever became an enforced property.

This module is facet 1 (semantic unification): ALL board queries go
through query() here, which guarantees:
  - the measurement runs on a CONSISTENT state (refill+save first,
    identical to the gate's DRC invocation — the 40-vs-66 swing was a
    stale-fill misread), and
  - every returned number carries a StateFingerprint (board sha256 +
    fill epoch). Consumers literally cannot obtain a bare number, so
    a measurement can always be traced to the exact state it measured.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import dataclass, asdict
from pathlib import Path


@dataclass
class StateFingerprint:
    board_sha256: str
    fill_epoch: str          # mtime of board at refill+save; "" = unfilled
    source: str              = "kernel"

    def short(self) -> str:
        return f"{self.board_sha256[:10]}/{self.fill_epoch}"


class UnboundMeasurement(Exception):
    """A measurement number escaped without its state fingerprint.

    The laughable-error class (K22): the 40-vs-66 swing and the severity
    illusion both came from bare numbers floating across boundaries.
    Invariants have NO lenient mode — a consumer that receives a raw
    int where a KernelQuery is expected gets this exception, never a
    warning (an invariant written as a warning is itself a bug)."""


# Semantic contract registry (K22): every query name declares what it
# MEANS and under which preconditions. Changing semantics REQUIRES a
# semantic_version bump; the test lock in test_kernel.py fails otherwise.
# Silent semantic drift — 'unconnected' meaning different things at
# different times — is structurally impossible from here.
SEMANTICS = {
    "opens": {
        "semantic_version": 1,
        "preconditions": ["post-refill", "conn_audit-ratsnest"],
        "meaning": "unconnected ratsnest pairs on the consolidated board",
    },
    "shorts": {
        "semantic_version": 1,
        "preconditions": ["post-refill", "kicad-cli-drc"],
        "meaning": "[shorting_items] count in the DRC report",
    },
    "clearance": {
        "semantic_version": 1,
        "preconditions": ["post-refill", "kicad-cli-drc"],
        "meaning": "[clearance] count in the DRC report",
    },
}


def require_fp(value, context: str = ""):
    """Consumer-side hard contract: accept ONLY a fingerprint-bound
    KernelQuery. Raw numbers raise UnboundMeasurement immediately —
    the point-12 lesson: invariants have no warning mode."""
    if not isinstance(value, KernelQuery):
        raise UnboundMeasurement(
            f"unbound {type(value).__name__} in {context or 'unknown site'}"
            f" — cross-boundary numbers must be KernelQuery")
    if not value.fp or not value.fp.board_sha256:
        raise UnboundMeasurement(
            f"KernelQuery({value.kind}) missing fingerprint in "
            f"{context or 'unknown site'}")
    return value


def fingerprint(pcb: Path) -> StateFingerprint:
    return StateFingerprint(
        board_sha256=hashlib.sha256(
            pcb.read_bytes()).hexdigest(),
        fill_epoch=str(int(pcb.stat().st_mtime)),
    )


class KernelQuery:
    """One measured truth, bound to its state. Refuses to expose the
    number without the fingerprint (I4: no unbound measurements)."""

    def __init__(self, kind: str, value, fp: StateFingerprint,
                 detail: dict | None = None):
        self.kind = kind
        self.value = value
        self.fp = fp
        self.detail = detail or {}

    @property
    def v(self):
        return self.value

    def to_dict(self) -> dict:
        return {"kind": self.kind, "value": self.value,
                "fp": asdict(self.fp), "detail": self.detail}

    def __repr__(self):
        return f"<KQ {self.kind}={self.value} @{self.fp.short()}>"


def count_drc(txt: str, kind: str) -> int:
    """THE single derivation of DRC report counts (C0c): every consumer
    (run_drc, query, gates) counts through here — the point-9 defect was
    the same question derived in several places with divergent rules."""
    key = {"shorts": "[shorting_items]",
           "clearance": "[clearance]",
           "unconnected": "[unconnected_items]"}[kind]
    return txt.count(key)


def _kipy_by_kicad_cli(kicad_cli: str) -> str:
    py = Path(kicad_cli).parent / "python.exe"
    return str(py) if py.exists() else ""


def _conn_audit(kipy: str, pcb: Path) -> dict:
    """Fresh ratsnest truth via conn_audit subprocess."""
    script = Path(__file__).resolve().parent.parent / "kicadgen" \
        / "conn_audit.py"
    pairs_file = pcb.parent / "kernel_pairs.tmp.txt"
    try:
        p = subprocess.run([kipy, str(script), "--pairs",
                            str(pcb), str(pairs_file)],
                           capture_output=True, text=True, timeout=300,
                           encoding="utf-8", errors="replace")
        data = json.loads([l for l in (p.stdout or "").splitlines()
                           if l.strip().startswith("{")][0])
        pairs = []
        if pairs_file.exists():
            pairs = [l for l in
                     pairs_file.read_text(encoding="utf-8").splitlines()
                     if l.strip()]
        return {"audit": data, "pairs": pairs}
    except (IndexError, ValueError, subprocess.TimeoutExpired, OSError):
        return {"audit": {}, "pairs": []}
    finally:
        try:
            pairs_file.unlink()
        except OSError:
            pass    # ok-silent: 临时文件清理, 读不到就当无 pairs


def refill_and_save(kicad_cli: str, pcb: Path, rpt: Path | None = None):
    """The ONE canonical state-consolidation step (identical to the
    gate's): refill all zones and save the board. Every measurement in
    the chain must run AFTER this.

    C0m: the report is BOUND to the state it measured — a sibling
    .fp file records the board fingerprint; a reader that finds a
    mismatched fp must RE-DERIVE, never reuse (the rolled-back
    action's shorts were once read as the restored board's truth)."""
    rpt = rpt or pcb.parent / "kernel_drc.rpt"
    subprocess.run([kicad_cli, "pcb", "drc", "--exit-code-violations",
                    "--refill-zones", "--save-board", "-o", str(rpt),
                    str(pcb)],
                   capture_output=True, text=True, timeout=900,
                   encoding="utf-8", errors="replace")
    try:
        (pcb.parent / (rpt.name + ".fp")).write_text(
            fingerprint(pcb).short(), encoding="utf-8")
    except OSError:
        pass    # ok-silent: fp写失败=失效安全(无fp->读者自动重测)


def query(kicad_cli: str, kipy: str, pcb: Path, kind: str,
          consolidate: bool = True) -> KernelQuery:
    """The single truth interface.

    kind: 'opens'  — conn_audit unconnected_ratsnest (+pairs in detail)
         'shorts' — DRC report shorting count
         'clearance' — DRC report clearance count
    consolidate=True runs refill+save first (the truth about zone-
    dependent nets like GND/3V3 only exists post-refill — the stale-
    fill misread measured 40 when the truth was 66).
    """
    if consolidate:
        refill_and_save(kicad_cli, pcb)
    if kind == "opens":
        fp = fingerprint(pcb)
        r = _conn_audit(kipy, pcb)
        val = r["audit"].get("unconnected_ratsnest", -1)
        return KernelQuery("opens", val, fp,
                           {"pairs": r["pairs"],
                            "fragmented": r["audit"].get(
                                "fragmented_nets", {})})
    # DRC-text kinds
    rpt = pcb.parent / "kernel_drc.rpt"
    fp = fingerprint(pcb)
    # C0m state-binding: the report is reusable ONLY if its companion
    # fp matches the board being asked about — a report written for
    # another state (e.g. mid-rollback) is not a measurement of THIS
    # board. Mismatch or doubt -> fresh in-memory-refill DRC.
    bpf = pcb.parent / (rpt.name + ".fp")
    bound = False
    if rpt.exists() and bpf.exists():
        try:
            bound = (bpf.read_text(encoding="utf-8").strip()
                     == fp.short())
        except OSError:
            bound = False
    if not bound:
        subprocess.run([kicad_cli, "pcb", "drc", "--exit-code-violations",
                        "--refill-zones", "-o", str(rpt), str(pcb)],
                       capture_output=True, text=True, timeout=900,
                       encoding="utf-8", errors="replace")
        try:
            bpf.write_text(fp.short(), encoding="utf-8")
        except OSError:
            pass    # ok-silent: fp写失败=失效安全(无fp->读者自动重测)
    txt = rpt.read_text(encoding="utf-8", errors="replace")
    return KernelQuery(kind, count_drc(txt, kind), fp, {})
