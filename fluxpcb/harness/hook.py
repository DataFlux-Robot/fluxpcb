"""harness — a runtime policy the executor is required to load.

The HarnessPro lesson: if the runner never loads `h`, the harness is
decorative.  `HarnessPolicy` is that `h`; `HarnessHook` enforces it at the
action and commit boundaries.  Fail-closed: a missing/invalid policy is an
error, not a default.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field


@dataclass
class HarnessPolicy:
    name: str = 'default'
    max_actions: int = 3
    max_seconds: int = 900
    allowed_ops: tuple = ()
    require_claims: tuple = ()
    allow_gate_weakening: bool = False

    @classmethod
    def load(cls, path: str) -> 'HarnessPolicy':
        if not path or not os.path.exists(path):
            raise FileNotFoundError(f'harness policy not found: {path}')
        with open(path, encoding='utf-8') as f:
            d = json.load(f)
        if not isinstance(d, dict) or 'max_actions' not in d:
            raise ValueError(f'invalid harness policy: {path}')
        return cls(name=d.get('name', os.path.basename(path)),
                   max_actions=int(d['max_actions']),
                   max_seconds=int(d.get('max_seconds', 900)),
                   allowed_ops=tuple(d.get('allowed_ops') or ()),
                   require_claims=tuple(d.get('require_claims') or ()),
                   allow_gate_weakening=bool(d.get('allow_gate_weakening',
                                                   False)))

    def as_dict(self) -> dict:
        return {'name': self.name, 'max_actions': self.max_actions,
                'max_seconds': self.max_seconds,
                'allowed_ops': list(self.allowed_ops),
                'require_claims': list(self.require_claims),
                'allow_gate_weakening': self.allow_gate_weakening}

    def digest(self) -> str:
        import hashlib
        blob = json.dumps(self.as_dict(), sort_keys=True, separators=(',', ':'))
        return hashlib.sha256(blob.encode()).hexdigest()


@dataclass
class HarnessHook:
    policy: HarnessPolicy
    actions: int = 0
    actions_by_stage: dict = field(default_factory=dict)
    denials: list = field(default_factory=list)

    def before_action(self, op: str, ctx: dict = None) -> tuple:
        # The action budget is per stage: a repair stage that spent its
        # three actions must not starve the reroute stage (it used to).
        used = self.actions_by_stage.get(op, 0)
        if used >= self.policy.max_actions:
            why = f'action budget exhausted ({self.policy.max_actions})'
            self.denials.append(why)
            return False, why
        self.actions_by_stage[op] = used + 1
        self.actions += 1
        return True, 'ok'

    def after_action(self, ops) -> tuple:
        """Assertion, not the enforcement point.

        Operators are filtered when dispatch SELECTS them; a disallowed
        operator reaching this check means the selection leaked, and the
        run is invalid evidence.  (It used to be the only enforcement: a
        single disallowed name vetoed the whole batch.)
        """
        bad = [o for o in (ops or ()) if self.policy.allowed_ops
               and o not in self.policy.allowed_ops]
        if bad:
            why = f'operators not allowed by harness {self.policy.name!r}: {bad}'
            self.denials.append(why)
            return False, why
        return True, 'ok'

    def before_commit(self, scope_before, scope_after,
                      gate_changes: dict = None, authority: str = '') -> tuple:
        """SII: a meta/harness change may not shrink the declared scope."""
        if not self.policy.allow_gate_weakening and gate_changes:
            why = f'gate changes not permitted without authority: {gate_changes}'
            self.denials.append(why)
            return False, why
        if scope_before is not None and scope_after is not None:
            if not scope_after.is_superset(scope_before):
                why = 'scope narrowed (SII violation)'
                self.denials.append(why)
                return False, why
        return True, 'ok'

    def as_dict(self) -> dict:
        return {'policy': self.policy.as_dict(),
                'policy_digest': self.policy.digest(),
                'actions': self.actions,
                'actions_by_stage': dict(self.actions_by_stage),
                'denials': self.denials[-20:]}


def load_hook(path: str = None) -> HarnessHook:
    path = path or os.environ.get('FLUXPCB_HARNESS', '')
    if not path:
        return None
    return HarnessHook(HarnessPolicy.load(path))
