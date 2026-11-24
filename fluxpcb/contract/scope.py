"""scope — what a verdict is relative to.

A bare `pass` is ambiguous.  `Pass(scope, evidence)` is not: the scope names
the rule register, the project/DSN bytes, the executor and tool versions,
the claim set, and the assumptions.  Two verdicts are comparable only when
their scope digests match.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
from dataclasses import dataclass, field
from typing import Optional


def _sha256_file(path: str) -> str:
    if not path or not os.path.exists(path):
        return ''
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def _tool_version(cmd: str) -> str:
    try:
        p = subprocess.run([cmd, '--version'], capture_output=True,
                           text=True, timeout=10)
        line = (p.stdout or p.stderr or '').strip().splitlines()
        return line[0][:80] if line else ''
    except Exception:
        return ''


def _pcbnew_version() -> str:
    try:
        import pcbnew
        return str(pcbnew.GetBuildVersion())
    except Exception:
        return ''


@dataclass(frozen=True)
class Executor:
    backend: str = ''
    version: str = ''
    jar: str = ''
    java: str = ''
    timeout_s: int = 0

    def as_dict(self) -> dict:
        return {'backend': self.backend, 'version': self.version,
                'jar': os.path.basename(self.jar) if self.jar else '',
                'java': os.path.basename(self.java) if self.java else '',
                'timeout_s': self.timeout_s}


@dataclass(frozen=True)
class Tools:
    pcbnew: str = ''
    kicad_cli: str = ''

    def as_dict(self) -> dict:
        return {'pcbnew': self.pcbnew, 'kicad_cli': self.kicad_cli}


@dataclass
class Scope:
    board: str
    project_sha: str
    dsn_sha: str
    register_digest: str
    executor: Executor = field(default_factory=Executor)
    tools: Tools = field(default_factory=Tools)
    claim_set: tuple = ()
    assumptions: tuple = ()
    declared_rule_ids: tuple = ()
    represented_rule_ids: tuple = ()
    board_family: str = ''
    phase: str = 'post_route'
    extra: dict = field(default_factory=dict)

    def as_dict(self, include_ids: bool = False) -> dict:
        d = {
            'board': self.board,
            'project_sha': self.project_sha,
            'dsn_sha': self.dsn_sha,
            'register_digest': self.register_digest,
            'executor': self.executor.as_dict(),
            'tools': self.tools.as_dict(),
            'claim_set': list(self.claim_set),
            'assumptions': list(self.assumptions),
            'declared_rule_ids': list(self.declared_rule_ids),
            'represented_rule_ids': list(self.represented_rule_ids),
            'board_family': self.board_family,
            'phase': self.phase,
            'extra': self.extra,
        }
        if include_ids:
            d['scope_id'] = self.scope_id()
        return d

    def is_superset(self, other: 'Scope') -> bool:
        """SII: this scope must not shrink any declared dimension of other."""
        if other is None:
            return True
        for field in ('claim_set', 'assumptions', 'declared_rule_ids'):
            if not set(getattr(other, field, ()) or ()) <= set(
                    getattr(self, field, ()) or ()):
                return False
        return True

    def scope_id(self) -> str:
        payload = json.dumps(self.as_dict(), sort_keys=True,
                             separators=(',', ':'))
        return hashlib.sha256(payload.encode()).hexdigest()


class ScopeBuilder:
    """Build a Scope from the artifacts a routing run actually read."""

    def __init__(self, register_digest: str = '',
                 kicad_cli: str = None, pcbnew_version: str = None):
        self.register_digest = register_digest
        self._kicad_cli = kicad_cli or os.environ.get('KICAD_CLI', 'kicad-cli')
        self._pcbnew = pcbnew_version if pcbnew_version is not None \
            else _pcbnew_version()

    def build(self, board_path: str, pro_path: str = None,
              dsn_path: str = None, executor: Executor = None,
              claim_set=(), assumptions=(), board_family: str = '',
              phase: str = 'post_route', extra: dict = None,
              register=None, model=None) -> Scope:
        pro = pro_path or (os.path.splitext(board_path)[0] + '.kicad_pro')
        return Scope(
            board=os.path.basename(board_path),
            project_sha=_sha256_file(pro),
            dsn_sha=_sha256_file(dsn_path or ''),
            register_digest=self.register_digest,
            executor=executor or Executor(),
            tools=Tools(pcbnew=self._pcbnew,
                        kicad_cli=_tool_version(self._kicad_cli)),
            claim_set=tuple(claim_set),
            assumptions=tuple(assumptions),
            declared_rule_ids=(tuple(sorted(register.declared_ids()))
                               if register is not None else ()),
            represented_rule_ids=(tuple(sorted(register.represented_ids(model)))
                                  if register is not None else ()),
            board_family=board_family,
            phase=phase,
            extra=extra or {},
        )
