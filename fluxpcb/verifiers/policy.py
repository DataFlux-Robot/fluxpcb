"""How far the physics checks may go, per run.

The eight physics checks range from a regex over the board text to an
openEMS field solve that can take an hour.  A pipeline run must not
silently choose the expensive end — and must not silently pretend the
cheap end is the expensive one either.  This module is the one place
that decides, and it records the decision on every verdict.

    off       nothing runs; every physics verdict is `unknown` with the
              reason, so the report says what it does not know
    estimate  the proxies run (capacities, closed forms, FDM on declared
              geometry, the susceptibility index); every finding they
              produce is status='estimate' and capped at P1
    execute   real solvers run too (openEMS, CFD); a missing solver is
              `unknown`, never a pass

`execute` needs an explicit confirmation: `PhysicsPolicy.from_env()` only
allows it when FLUXPCB_PHYSICS=execute AND FLUXPCB_PHYSICS_CONFIRM=1.
That is deliberate — before a project like the H7 flight controller is
run with field solves, a person is asked, and the answer is put in the
environment (or passed to the constructor).

The benchmark path never builds a PhysicsPolicy: `run_bench` repairs a
routed board with the DRC and never touches the verifier suite.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

MODES = ('off', 'estimate', 'execute')
# gates whose verdict needs an actual solver run
EXECUTE_ONLY = frozenset({'em_field_analysis', 'thermal_cfd'})


@dataclass(frozen=True)
class PhysicsPolicy:
    mode: str = 'estimate'
    confirmed: bool = False
    budget_s: float = 600.0
    reason: str = ''

    def __post_init__(self):
        if self.mode not in MODES:
            raise ValueError(f'physics mode {self.mode!r} not one of {MODES}')
        if self.mode == 'execute' and not self.confirmed:
            object.__setattr__(self, 'mode', 'estimate')
            object.__setattr__(
                self, 'reason',
                'execute was requested without confirmation; '
                'running estimate instead')

    # -------------------------------------------------------------- read
    @property
    def executes(self) -> bool:
        return self.mode == 'execute'

    def allows(self, gate_name: str) -> bool:
        """May this gate run at all?"""
        if self.mode == 'off':
            return False
        if gate_name in EXECUTE_ONLY:
            return self.executes
        return True

    def why_not(self, gate_name: str) -> list:
        """What a reader needs to know when `allows` is False."""
        if self.mode == 'off':
            return ['physics checks are off for this run (PhysicsPolicy.mode=off)']
        return [f'{gate_name} needs a solver run; physics execution was not '
                f'confirmed for this run (PhysicsPolicy.mode={self.mode})']

    def as_dict(self) -> dict:
        return {'mode': self.mode, 'confirmed': self.confirmed,
                'budget_s': self.budget_s, 'reason': self.reason}

    # ------------------------------------------------------------- build
    @classmethod
    def from_env(cls, **kw) -> 'PhysicsPolicy':
        mode = (os.environ.get('FLUXPCB_PHYSICS') or 'estimate').strip().lower()
        confirm = (os.environ.get('FLUXPCB_PHYSICS_CONFIRM') or '') in (
            '1', 'true', 'yes')
        return cls(mode=mode or 'estimate', confirmed=confirm, **kw)

    @classmethod
    def execute_confirmed(cls, **kw) -> 'PhysicsPolicy':
        """For a caller that has just asked a person and got a yes."""
        return cls(mode='execute', confirmed=True, **kw)


DEFAULT = PhysicsPolicy()
