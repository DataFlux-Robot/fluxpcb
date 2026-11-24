#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""grounding_map — Grounding Map (Component 12).

Requirement→gate→artifact→evidence map.
Single pane proving components 1-12 are all present and green.
"""
from __future__ import annotations

import json
import os
from typing import Any, Optional

from ..contracts import (
    ManufacturingReport,
    RouteResult,
    Trajectory,
    VerificationReport,
)


class GroundingMap:
    """Grounding Map — visualizes the full 12-component evidence.

    Consumes all reports and produces a unified view.
    """

    def __init__(self, output_dir: str = "reports/maps"):
        """Initialize grounding map.

        Args:
            output_dir: Directory for output files
        """
        self.output_dir = output_dir
        os.makedirs(output_dir, exist_ok=True)

    def build(
        self,
        route_result: Optional[RouteResult] = None,
        verification: Optional[VerificationReport] = None,
        mfg: Optional[ManufacturingReport] = None,
        trajectory: Optional[Trajectory] = None,
        harness_diffs: int = 0,
        heldout_delta: Optional[float] = None,
        plan: Optional[dict] = None,
        classification: Optional[dict] = None,
        dispatch: Optional[dict] = None,
        stale_count: Optional[int] = None,
    ) -> dict:
        """Build the grounding map.

        Args:
            route_result: Routing result (Component 9)
            verification: Verification report (Component 10)
            mfg: Manufacturing report (Component 11)
            trajectory: Execution trajectory (Component 8)

        Returns:
            dict with 12-component status
        """
        # Component status
        components = {}

        # Component 1: Meta-dev loop
        components['1_meta_dev_loop'] = {
            'status': 'active' if trajectory else 'inactive',
            'evidence': {
                'trajectory_events': len(trajectory.events) if trajectory else 0,
                'grade': trajectory.grade if trajectory else 0,
            },
        }

        # Component 2: Meta-meta loop.  Active only if it produced an
        # evaluated harness change; a layer with no diff has no effect.
        mm_active = bool(harness_diffs) and (heldout_delta is not None
                                             and heldout_delta > 0)
        components['2_meta_meta_loop'] = {
            'status': 'active' if mm_active else 'inactive',
            'evidence': {'harness_diffs': harness_diffs,
                         'heldout_delta': heldout_delta,
                         'reason': None if mm_active else
                         'no evaluated harness change'},
        }

        # Component 3: Dev execution loop
        components['3_dev_loop'] = {
            'status': 'active' if route_result else 'inactive',
            'evidence': {
                'iterations': len(trajectory.events) if trajectory else 0,
                'converged': route_result.opens == 0 if route_result else False,
            },
        }

        # Component 4: PCB design agent.  A plan must exist and carry claims.
        plan_ok = bool(plan) and bool(plan.get('claims'))
        components['4_design_agent'] = {
            'status': 'active' if plan_ok else 'inactive',
            'evidence': {'plan_generated': bool(plan),
                         'claims': len((plan or {}).get('claims') or []),
                         'plan_hash': (plan or {}).get('hash')},
        }

        # Component 5: Communication runtime.  Active if a state was carried
        # and a real stale count was supplied.
        comm_ok = stale_count is not None and trajectory is not None
        components['5_comm_runtime'] = {
            'status': 'active' if comm_ok else 'inactive',
            'evidence': {'stale_count': stale_count},
        }

        # Component 6: Subagents
        components['6_subagents'] = {
            'status': 'active' if classification else 'inactive',
            'evidence': {'classification': bool(classification),
                         'tasks': len((classification or {}).get('tasks') or [])},
        }

        # Component 7: LLM dispatch
        components['7_llm_dispatch'] = {
            'status': 'active' if dispatch else 'inactive',
            'evidence': {'dispatch': bool(dispatch),
                         'planner': (dispatch or {}).get('planner')},
        }

        # Component 8: Feedback runtime
        components['8_feedback'] = {
            'status': 'active' if trajectory else 'inactive',
            'evidence': {'events': len(trajectory.events) if trajectory else 0},
        }

        # Component 9: Router
        components['9_router'] = {
            'status': 'active' if route_result else 'inactive',
            'evidence': {
                'opens': route_result.opens if route_result else 0,
                'drc_errors': route_result.drc_errors if route_result else 0,
                'completion_pct': route_result.completion_pct if route_result else 0,
            },
        }

        # Component 10: Verifiers
        components['10_verifiers'] = {
            'status': 'active' if verification else 'inactive',
            'evidence': {
                'passed': verification.passed if verification else False,
                'results': len(verification.results) if verification else 0,
            },
        }

        # Component 11: Manufacturing
        components['11_manufacturing'] = {
            'status': 'active' if mfg else 'inactive',
            'evidence': {
                'passed': mfg.passed if mfg else False,
                'dfm_violations': len(mfg.dfm_violations) if mfg else 0,
            },
        }

        # Component 12: Grounding map — active because it rendered this map,
        # with the count of inactive components it made visible.
        n_inactive = sum(1 for c in components.values()
                         if c['status'] != 'active')
        components['12_grounding_map'] = {
            'status': 'active',
            'evidence': {'generated': True, 'inactive_components': n_inactive},
        }

        # Overall status
        all_active = all(c['status'] == 'active' for c in components.values())
        all_green = (
            (route_result.opens == 0 if route_result else False) and
            (verification.passed if verification else False) and
            (mfg.passed if mfg else False)
        )

        return {
            'components': components,
            'all_active': all_active,
            'all_green': all_green,
            'release_ready': all_active and all_green,
        }

    def render(self, data: dict, output_file: str = "grounding_map.json") -> str:
        """Render the grounding map to a file.

        Args:
            data: Grounding map data
            output_file: Output filename

        Returns:
            Path to output file
        """
        path = os.path.join(self.output_dir, output_file)
        with open(path, 'w') as f:
            json.dump(data, f, indent=2)
        return path
