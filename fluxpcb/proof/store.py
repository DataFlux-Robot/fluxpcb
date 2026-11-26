"""Content-addressed proof store: keep proofs alive across refactors.

A proof is valid for a *statement* on a *scope* under a *context* (rules,
layers, footprints).  The store keys it by the statement hash and records the
per-item hashes of its scope, so it is fresh exactly when every item the
statement talks about is unchanged and the context is unchanged — the same
invariant the Lean frame checks, kept outside the kernel so a refactor only
re-proves the claims whose scope it touched.

Nothing here decides truth: `fresh` only says "this proof may be replayed";
the claim still goes through `gate.verify`, where the Lean kernel checks the
carried scope against `touched` and the frame.
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

VERSION = 'state-2/1'


def statement_hash(claim):
    body = {k: claim.get(k) for k in ('id', 'pred', 'net', 'pads', 'scope',
                                      'expect', 'rule', 'params', 'limit')
            if claim.get(k) is not None}
    return hashlib.sha256(json.dumps(body, sort_keys=True, default=str)
                          .encode('utf-8')).hexdigest()[:16]


def _context(st):
    body = {'rules': st.get('rules'), 'layers': st.get('layers'),
            'footprints': st.get('footprints')}
    return hashlib.sha256(json.dumps(body, sort_keys=True, default=str)
                          .encode('utf-8')).hexdigest()[:16]


def _scope_hash(scope, ih):
    want = sorted((i, ih.get(i, '?')) for i in (scope or []))
    return hashlib.sha256(json.dumps(want).encode('utf-8')).hexdigest()[:16]


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.entries = {}
        if self.path.exists():
            try:
                self.entries = json.loads(self.path.read_text(encoding='utf-8'))
            except (OSError, ValueError):
                self.entries = {}

    def _key(self, claim):
        return f"{claim['id']}@{statement_hash(claim)}"

    def get(self, claim, st, ih=None):
        """The stored proof if fresh for this state, else None."""
        e = self.entries.get(self._key(claim))
        if not e or e.get('version') != VERSION:
            return None
        if st.get('digest') == e.get('state_digest'):
            return e
        ih = ih if ih is not None else _item_hashes(st)
        if e.get('context') != _context(st):
            return None
        if e.get('scope_hash') != _scope_hash(e.get('scope'), ih):
            return None
        return e

    def put(self, claim, status, witness, st, ih=None):
        ih = ih if ih is not None else _item_hashes(st)
        e = {'claim': statement_hash(claim), 'id': claim['id'],
             'status': status, 'witness': witness, 'version': VERSION,
             'scope': list(claim.get('scope') or []),
             'scope_hash': _scope_hash(claim.get('scope'), ih),
             'context': _context(st), 'state_digest': st.get('digest'),
             'board_fp': _board_fp(st), 'ts': int(time.time())}
        self.entries[self._key(claim)] = e
        return e

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix('.json.tmp')
        tmp.write_text(json.dumps(self.entries, indent=1), encoding='utf-8')
        tmp.replace(self.path)

    def stats(self):
        return {'entries': len(self.entries)}


def _item_hashes(st):
    from .state import item_hashes
    return item_hashes(st)


def _board_fp(st):
    from .state import board_fp
    return board_fp(st)
