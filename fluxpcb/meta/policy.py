"""PolicyArtifact — the text/number object C1 optimizes.

TextGrad's idea is kept in a bounded form: the strategy document is the
text variable, the FailurePacket gradient is the feedback, but every
proposal must compile into a typed delta (operator set, budgets, planning
switch).  Uncompilable text cannot change behaviour.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field, replace

POLICY_VERSION = 1
# The legacy set: new capabilities (maze escape, rip-up) are OPT-IN and
# can only be enabled by an accepted delta with evidence behind it.
DEFAULT_PORTFOLIO = (
    'widen_narrow_tracks', 'clearance_chain', 'endpoint_attach',
    'connect_unconnected',
)
DEFAULT_STRATEGY = (
    "Capacity before search. Prefer exact certificates over new heuristics. "
    "When a corridor is 1-40 um short, rip up the blocking net if its fanout "
    "is small; otherwise report infeasible instead of retrying."
)


def _digest(payload: dict) -> str:
    body = json.dumps(payload, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(body.encode('utf-8')).hexdigest()


@dataclass(frozen=True)
class PolicyArtifact:
    strategy_doc: str = DEFAULT_STRATEGY
    operator_portfolio: tuple = DEFAULT_PORTFOLIO
    plan_template: dict = field(default_factory=lambda: {
        'escape_order': ['maze', 'geomir', 'via_slot'],
        'corridor_widths_mm': [0.5, 1.0, 2.0],
        'via_slot_radii_mm': [0.3, 0.5, 0.8, 1.1, 1.5],
    })
    budgets: dict = field(default_factory=lambda: {
        'actions_per_board': 3, 'ripup_max': 8,
        'replan_max': 4, 'maze_seconds': 30,
    })
    rationale_log: tuple = ()
    enabled_planning: bool = True
    version: int = POLICY_VERSION

    def payload(self) -> dict:
        return {
            'version': self.version,
            'strategy_doc': self.strategy_doc,
            'operator_portfolio': list(self.operator_portfolio),
            'plan_template': self.plan_template,
            'budgets': self.budgets,
            'rationale_log': list(self.rationale_log),
            'enabled_planning': self.enabled_planning,
        }

    def digest(self) -> str:
        return _digest(self.payload())

    def to_json(self, path: str) -> str:
        body = self.payload()
        body['policy_digest'] = self.digest()
        with open(path, 'w', encoding='utf-8') as fh:
            json.dump(body, fh, indent=1, ensure_ascii=False)
        return body['policy_digest']

    @classmethod
    def from_json(cls, path: str) -> "PolicyArtifact":
        b = json.load(open(path, encoding='utf-8'))
        return cls(
            strategy_doc=b.get('strategy_doc', DEFAULT_STRATEGY),
            operator_portfolio=tuple(b.get('operator_portfolio')
                                     or DEFAULT_PORTFOLIO),
            plan_template=b.get('plan_template') or {},
            budgets=b.get('budgets') or {},
            rationale_log=tuple(b.get('rationale_log') or ()),
            enabled_planning=bool(b.get('enabled_planning', True)),
            version=int(b.get('version', POLICY_VERSION)),
        )

    # -- typed delta ---------------------------------------------------
    def with_delta(self, delta: dict, rationale: str = '') -> "PolicyArtifact":
        kind = delta.get('kind')
        if kind == 'enable_op':
            op = delta.get('value')
            if not op:
                raise ValueError('enable_op needs value')
            portfolio = tuple(self.operator_portfolio)
            if op not in portfolio:
                portfolio = portfolio + (op,)
            new = replace(self, operator_portfolio=portfolio)
        elif kind == 'disable_op':
            op = delta.get('value')
            new = replace(self, operator_portfolio=tuple(
                o for o in self.operator_portfolio if o != op))
        elif kind == 'budget':
            value = delta.get('value') or {}
            budgets = dict(self.budgets)
            budgets.update({k: v for k, v in value.items()})
            new = replace(self, budgets=budgets)
        elif kind == 'planning':
            new = replace(self, enabled_planning=bool(delta.get('value')))
        elif kind == 'text':
            text = (delta.get('value') or '').strip()
            if not text:
                raise ValueError('text delta empty')
            new = replace(self, strategy_doc=(self.strategy_doc + ' ' + text))
        else:
            raise ValueError(f'unknown delta kind {kind!r}')
        if rationale:
            new = replace(new, rationale_log=self.rationale_log + (rationale,))
        return new

    def harness_policy(self):
        from ..harness import HarnessPolicy
        # Dispatch capability names differ from the names operators REPORT.
        # A policy that says 'clearance_chain' must permit its three
        # emitted operators, or the harness denies a legitimate repair and
        # the experiment measures the alias bug, not the policy.
        aliases = {
            'clearance_chain': ('width_cascade_surgery',
                                'policy_waiver_probe', 'nudge_clearance'),
            'connect_unconnected': ('connect_unconnected',),
        }
        allowed = {'copper_text_to_silk'}   # always-on cleanup
        for cap in self.operator_portfolio:
            allowed.update(aliases.get(cap, (cap,)))
        return HarnessPolicy(
            name='policy:' + self.digest()[:12],
            max_actions=int(self.budgets.get('actions_per_board', 3)),
            max_seconds=int(self.budgets.get('maze_seconds', 30)) * 60,
            allowed_ops=tuple(sorted(allowed)),
        )


def propose_deltas(packets: list, policy: "PolicyArtifact" = None,
                   max_deltas: int = 3) -> list:
    """Rule-based TextGrad step: morphology counts -> typed deltas.

    Offline-safe (no API key needed).  An LLM proposer can replace this
    function, but its output must still be typed deltas like these.
    """
    from .feedback import gradient
    g = gradient(packets)
    policy = policy or PolicyArtifact()
    port = set(policy.operator_portfolio)
    deltas = []
    axis = g.get('axis') or {}
    feas = g.get('feasibility') or {}
    residue = g.get('residue') or {}
    if feas.get('capacity_infeasible'):
        deltas.append({'kind': 'planning', 'value': True,
                       'why': 'capacity gate must stay in front of search'})
    if residue.get('opens') and 'maze_escape' not in port:
        deltas.append({'kind': 'enable_op', 'value': 'maze_escape',
                       'why': "%d opens survived the polyline connector"
                              % residue['opens']})
    if residue.get('shorts') and 'short_rip_reroute' not in port:
        deltas.append({'kind': 'enable_op', 'value': 'short_rip_reroute',
                       'why': "%d hard shorts on the axis"
                              % residue['shorts']})
    if (not feas.get('capacity_infeasible')
            and feas.get('maze_grid_paths')
            and feas.get('maze_exact_rejected', 0)
            >= feas.get('maze_grid_paths', 0)
            and 'maze_escape' in port):
        deltas.append({'kind': 'budget',
                       'value': {'maze_seconds': 60},
                       'why': 'every grid path was exact-rejected; widen '
                              'the search budget before rip-up'})
    if axis.get('routing', 0) == 0 and (axis.get('silk', 0) +
                                        axis.get('library', 0)) > 0:
        deltas.append({'kind': 'text',
                       'value': 'Boards with only silk/library violations '
                                'are routing-clean; report them on a '
                                'separate axis, never route them.',
                       'why': 'axis attribution'})
    return deltas[:max_deltas]
