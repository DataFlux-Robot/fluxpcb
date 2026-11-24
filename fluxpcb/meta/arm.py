"""Arm — one complete "scheme" for components 3 and 4, as data.

An arm fixes how the design loop plans (C4) and how the development loop
repairs (C3), plus what the router and the repair operators may do.  Two
arms that differ along one axis are an intervention on that axis: run them
on the same boards and any outcome difference is attributable to it.

Everything is content-addressed, so a result can be cached by
(board, arm digest, code fingerprint) and never re-run by accident.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, fields, replace

from ..verifiers.operator_spec import REROUTE_NETS, all_operator_names
from ..verifiers.params import OperatorParams

PLAN_MODES = ('direct', 'gate', 'preroute')
LOOP_MODES = ('route_only', 'batch', 'morph', 'ripup')
ARM_VERSION = 2
# One sweep over every morphology plus one repeat sweep.
MORPH_ROUNDS = 10


@dataclass(frozen=True)
class Arm:
    name: str
    plan: str = 'gate'                # C4: direct | gate | preroute
    loop: str = 'batch'               # C3: route_only | batch | morph | ripup
    router_passes: int = 15
    router_threads: int = 2
    router_timeout_s: int = 240
    board_budget_s: int = 900         # wall-clock cap, enforced by the runner
    repair_rounds: int = 0            # 0 = the loop's default
    rip_nets_max: int = 12            # loop 'ripup': most nets re-routed at once
    allowed_ops: tuple = ()           # emitted operator names; () = all
    params: OperatorParams = field(default_factory=OperatorParams)
    version: int = ARM_VERSION

    # -- contract -----------------------------------------------------
    def validate(self) -> list:
        errs = []
        if self.plan not in PLAN_MODES:
            errs.append(f'unknown plan mode {self.plan!r}')
        if self.loop not in LOOP_MODES:
            errs.append(f'unknown loop mode {self.loop!r}')
        if self.plan == 'preroute' and self.params.preroute_max_nets < 1:
            errs.append('preroute arm needs preroute_max_nets >= 1')
        known = set(all_operator_names()) | {REROUTE_NETS}
        unknown = [o for o in self.allowed_ops if o not in known]
        if unknown:
            errs.append(f'unknown operators {unknown}')
        if self.router_passes < 1 or self.router_timeout_s < 1:
            errs.append('router passes/timeout must be positive')
        if self.router_threads < 1:
            errs.append('router_threads must be >= 1')
        return errs

    def rounds(self) -> int:
        """Transaction rounds for the repair stage."""
        if self.repair_rounds:
            return self.repair_rounds
        return MORPH_ROUNDS if self.loop == 'morph' else 3

    def allowed(self):
        """Operator names the dispatch may select (None = no restriction)."""
        return tuple(self.allowed_ops) or None

    def stages(self) -> list:
        if self.loop == 'route_only':
            return []
        if self.loop == 'batch':
            return ['repair:batch', 'reroute']
        if self.loop == 'morph':
            return ['repair:morph']
        return ['ripup', 'repair:batch']

    def with_param(self, name: str, value) -> 'Arm':
        """A one-variable intervention on this arm."""
        return replace(self, params=self.params.with_delta(name, value))

    def param_deltas(self, other: 'Arm') -> list:
        return self.params.deltas_from(other.params)

    # -- identity -----------------------------------------------------
    def as_dict(self) -> dict:
        return {'name': self.name, 'plan': self.plan, 'loop': self.loop,
                'router_passes': self.router_passes,
                'router_threads': self.router_threads,
                'router_timeout_s': self.router_timeout_s,
                'board_budget_s': self.board_budget_s,
                'repair_rounds': self.repair_rounds,
                'rip_nets_max': self.rip_nets_max,
                'allowed_ops': list(self.allowed_ops),
                'params': self.params.as_dict(), 'version': self.version}

    def digest(self) -> str:
        body = json.dumps(self.as_dict(), sort_keys=True,
                          separators=(',', ':'))
        return hashlib.sha256(body.encode('utf-8')).hexdigest()

    def to_json(self, path: str) -> str:
        body = self.as_dict()
        body['arm_digest'] = self.digest()
        with open(path, 'w', encoding='utf-8') as fh:
            json.dump(body, fh, indent=1, ensure_ascii=False)
        return body['arm_digest']

    @classmethod
    def from_dict(cls, d: dict) -> 'Arm':
        kw = {f.name: d[f.name] for f in fields(cls)
              if f.name in d and f.name != 'params'}
        kw['allowed_ops'] = tuple(kw.get('allowed_ops') or ())
        kw['params'] = OperatorParams.from_dict(d.get('params'))
        return cls(**kw)

    @classmethod
    def from_json(cls, path: str) -> 'Arm':
        body = json.load(open(path, encoding='utf-8'))
        arm = cls.from_dict(body)
        if body.get('arm_digest') and body['arm_digest'] != arm.digest():
            raise ValueError(f'arm digest mismatch in {path}')
        return arm

    # -- harness ------------------------------------------------------
    def harness_policy(self):
        from ..harness import HarnessPolicy
        return HarnessPolicy(
            name='arm:' + self.digest()[:12],
            max_actions=self.rounds() + 2,
            max_seconds=self.board_budget_s,
            allowed_ops=tuple(self.allowed_ops))

    @classmethod
    def from_policy(cls, policy, timeout_s: int = 240) -> 'Arm':
        """Adapt a legacy PolicyArtifact (used by the earlier rounds)."""
        allowed = tuple(policy.harness_policy().allowed_ops)
        return cls(name='policy:' + policy.digest()[:12],
                   plan='gate' if policy.enabled_planning else 'direct',
                   loop='batch',
                   router_timeout_s=int(timeout_s),
                   repair_rounds=int(policy.budgets.get(
                       'actions_per_board', 3)) or 0,
                   allowed_ops=allowed)


def default_arm(name: str = 'default', timeout_s: int = 240,
                enable_planning: bool = True) -> Arm:
    """The pipeline as it behaved before arms existed."""
    return Arm(name=name, plan='gate' if enable_planning else 'direct',
               loop='batch', router_timeout_s=int(timeout_s))


def factorial_arms(threads: int = 2, timeout_s: int = 240) -> list:
    """Every plan x loop combination: 3 x 4 = 12 maximally different arms."""
    out = []
    for plan in PLAN_MODES:
        for loop in LOOP_MODES:
            out.append(Arm(name=f'{plan}+{loop}', plan=plan, loop=loop,
                           router_threads=threads,
                           router_timeout_s=timeout_s))
    return out


def axes(arms: list) -> dict:
    """Axis -> sorted values, for the factorial read of a run."""
    out = {}
    for a in arms:
        for axis in ('plan', 'loop'):
            out.setdefault(axis, set()).add(getattr(a, axis))
    return {k: sorted(v) for k, v in out.items()}
