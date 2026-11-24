"""Tool registry — the action side of the loop.

A tool is a named, schema-typed capability the meta loop can call (and
later EVOLVE).  The same registry feeds the in-process caller, the MCP
server and the error-model capability matrix, so "which tools exist" has
one source of truth.

    source  'builtin'  — shipped, reviewed code
            'library'  — passed the admission ladder (tools/library/)
            'sandbox'  — candidate under evaluation (sandbox/<id>/)
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field

CATEGORIES = ('route', 'repair', 'plan', 'verify', 'knowledge')
SOURCES = ('builtin', 'library', 'sandbox')


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    category: str
    input_schema: dict             # JSON schema for arguments
    source: str = 'builtin'
    fn: str = ''                   # dotted path for builtins; '' = subprocess
    driver: str = ''               # driver script for subprocess tools
    emits: tuple = ()              # what it changes on the board
    cost_band: str = 'light'       # light | medium | heavy
    version: str = '1'

    def validate(self) -> list:
        errs = []
        if self.category not in CATEGORIES:
            errs.append(f'bad category {self.category!r}')
        if self.source not in SOURCES:
            errs.append(f'bad source {self.source!r}')
        if not isinstance(self.input_schema, dict):
            errs.append('input_schema must be a dict')
        if not self.description.strip():
            errs.append('no description')
        if self.source != 'builtin' and not self.driver and not self.fn:
            errs.append('non-builtin needs driver or fn')
        return errs

    def as_dict(self) -> dict:
        return asdict(self)

    def digest(self) -> str:
        body = json.dumps(self.as_dict(), sort_keys=True,
                          separators=(',', ':'))
        return hashlib.sha256(body.encode('utf-8')).hexdigest()[:16]

    def mcp_dict(self) -> dict:
        return {'name': self.name, 'description': self.description,
                'inputSchema': self.input_schema}


class ToolRegistry:
    def __init__(self, specs: list = None):
        self._specs = {}
        for s in (specs or []):
            self.register(s)

    def register(self, spec: ToolSpec) -> None:
        errs = spec.validate()
        if errs:
            raise ValueError(f'{spec.name}: ' + '; '.join(errs))
        if spec.name in self._specs:
            raise ValueError(f'duplicate tool {spec.name!r}')
        self._specs[spec.name] = spec

    def get(self, name: str):
        return self._specs.get(name)

    def names(self) -> list:
        return sorted(self._specs)

    def list(self) -> list:
        return [self._specs[n] for n in self.names()]

    def mcp_tools(self) -> list:
        return [s.mcp_dict() for s in self.list()]

    def digest(self) -> str:
        parts = [f'{n}:{s.digest()}' for n, s in sorted(self._specs.items())]
        return hashlib.sha256('|'.join(parts).encode('utf-8')).hexdigest()[:16]

    def categories(self) -> dict:
        out = {}
        for s in self.list():
            out.setdefault(s.category, []).append(s.name)
        return out
