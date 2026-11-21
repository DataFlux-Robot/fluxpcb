#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""fluxpcb_task — HarnessPro Task adapter for PCB routing.

Implements HarnessPro's Task protocol so fluxpcb routing can participate
in the diagnose→evolve→select loop.

Usage:
    from fluxpcb_task import PCBTask, PCBTaskSet
    task = PCBTask('rp2040-minimal', 'work/runs/fluxpcb/rp2040-minimal/unrouted.kicad_pcb')
    grade = task.grade(trajectory)
"""
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

MM = 1e6
sys.path.insert(0, str(Path(__file__).parent))
import routegrid as rg


@dataclass
class PCBTrajectory:
    """Minimal trajectory for PCB routing (wraps routing result)."""
    id: str
    task_id: str
    harness_id: str
    kind: str = "solve"
    completion_pct: float = 0.0
    drc_errors: int = 0
    unconnected: int = 0
    vias: int = 0
    time_s: float = 0.0
    routed_board: str = ""  # path to routed .kicad_pcb
    metadata: dict = None

    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}


class PCBTask:
    """HarnessPro Task adapter for PCB routing."""

    def __init__(self, task_id, board_path, budget_s=600):
        self.id = task_id
        self.board_path = str(board_path)
        self.budget_s = budget_s

    @property
    def harness(self):
        """Return the harness (routing strategy)."""
        return PCBHarness(self.id)

    def query(self):
        """Return the routing prompt."""
        return (
            f"Route all nets on PCB board '{self.id}' to achieve 100% completion "
            f"with 0 DRC errors. Use the fluxpcb routing tools "
            f"(gpu_eikonal, geomir, routegrid) to route nets, then verify with "
            f"kicad-cli pcb drc. Save the routed board to the output path."
        )

    def materialize(self, dest):
        """Copy board files to task directory."""
        import shutil
        dest = Path(dest)
        dest.mkdir(parents=True, exist_ok=True)
        shutil.copy2(self.board_path, dest / "board.kicad_pcb")
        # Also copy .kicad_pro if exists
        pro_path = self.board_path.replace('.kicad_pcb', '.kicad_pro')
        if Path(pro_path).exists():
            shutil.copy2(pro_path, dest / "board.kicad_pro")

    def grade(self, trajectory, *, artifacts_dir=None):
        """Grade a routing trajectory."""
        # Measure completion and DRC
        completion_pct = getattr(trajectory, 'completion_pct', 0.0)
        drc_errors = getattr(trajectory, 'drc_errors', 0)
        unconnected = getattr(trajectory, 'unconnected', 0)

        # If trajectory has a routed board, measure it
        routed_board = getattr(trajectory, 'routed_board', '')
        if routed_board and Path(routed_board).exists():
            completion_pct, drc_errors, unconnected = self._measure_board(routed_board)

        # Grade: passed if completion > 50% and DRC < 50
        passed = completion_pct > 50.0 and drc_errors < 50
        score = completion_pct / 100.0 * (1.0 - min(drc_errors / 100.0, 1.0))

        return Grade(
            passed=passed,
            score=score,
            details={
                'completion_pct': completion_pct,
                'drc_errors': drc_errors,
                'unconnected': unconnected,
                'routed_board': routed_board,
            },
        )

    def _measure_board(self, board_path):
        """Measure completion and DRC on a routed board."""
        import pcbnew
        b = pcbnew.LoadBoard(str(board_path))
        b.BuildConnectivity()
        unconnected = b.GetConnectivity().GetUnconnectedCount(True)

        # Count total pairs (from unrouted board)
        unrouted = self.board_path
        b2 = pcbnew.LoadBoard(str(unrouted))
        b2.BuildConnectivity()
        net_pads = {}
        for fp in b2.GetFootprints():
            for pad in fp.Pads():
                nc = pad.GetNetCode()
                if nc == 0:
                    continue
                nn = pad.GetNetname()
                net_pads[nn] = net_pads.get(nn, 0) + 1
        total_pairs = sum(max(0, c-1) for c in net_pads.values())
        routed = max(0, total_pairs - unconnected)
        completion_pct = 100.0 * routed / max(total_pairs, 1)

        # Measure DRC
        drc_errors = 0
        try:
            drc_json = str(board_path).replace('.kicad_pcb', '.drc.json')
            subprocess.run(
                ['kicad-cli', 'pcb', 'drc', '--format', 'json',
                 '--severity-error', '-o', drc_json, str(board_path)],
                capture_output=True, timeout=60,
            )
            if Path(drc_json).exists():
                drc_data = json.loads(Path(drc_json).read_text())
                drc_errors = len(drc_data.get('violations', []))
        except Exception:
            pass

        return completion_pct, drc_errors, unconnected

    @property
    def agent_timeout_s(self):
        return self.budget_s


@dataclass
class Grade:
    """HarnessPro Grade result."""
    passed: bool
    score: float
    details: dict = None

    def __post_init__(self):
        if self.details is None:
            self.details = {}


class PCBHarness:
    """HarnessPro Harness adapter for PCB routing."""

    def __init__(self, harness_id):
        self.id = harness_id

    def materialize(self, dest):
        """Copy harness files to workspace."""
        dest = Path(dest)
        dest.mkdir(parents=True, exist_ok=True)
        # Write routing instructions
        (dest / "README.md").write_text(
            "# PCB Routing Strategy\n\n"
            "1. Load board with pcbnew\n"
            "2. Build GridModel (routegrid.GridModel)\n"
            "3. Route nets with GPU Eikonal (gpu_eikonal)\n"
            "4. Verify with geomir.verify_polyline (commit gate)\n"
            "5. Fill power planes (GND, VCC)\n"
            "6. Run DRC with kicad-cli\n",
        )


class PCBTaskSet:
    """HarnessPro TaskSet adapter."""

    def __init__(self, tasks):
        self.tasks = tasks
        self.split = "test"

    def __iter__(self):
        return iter(self.tasks)

    def __len__(self):
        return len(self.tasks)


def load_pcb_tasks(work_dir, budget_s=600):
    """Load PCB routing tasks from work directory."""
    tasks = []
    work_dir = Path(work_dir)
    for board_dir in sorted(work_dir.iterdir()):
        unrouted = board_dir / "unrouted.kicad_pcb"
        if unrouted.exists():
            tasks.append(PCBTask(board_dir.name, str(unrouted), budget_s))
    return PCBTaskSet(tasks)
