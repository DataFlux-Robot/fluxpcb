"""Memory — what the loop has established, persisted across restarts.

The online-autoresearch lesson: a long loop that keeps its constraints,
verified judgements and open hypotheses only in its context loses them at
the first interruption.  This is an append-only log with a hash chain, so
"we already tried that and it did not help" is a fact that can be checked,
not something the loop has to remember.
"""
from __future__ import annotations

import hashlib
import json
import os
import time


def _canon(body: dict) -> str:
    return json.dumps(body, sort_keys=True, separators=(',', ':'),
                      default=str)


def delta_key(arm: str, variable: str, value) -> str:
    """Identity of a one-variable intervention."""
    return f'{arm}|{variable}|{_canon({"v": value})}'


class Memory:
    def __init__(self, path: str):
        self.path = path
        self._entries = []
        if path and os.path.exists(path):
            with open(path, encoding='utf-8') as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        self._entries.append(json.loads(line))
                    except ValueError:
                        continue
        self._chain_ok = self.verify_chain()

    # -- write --------------------------------------------------------
    def append(self, kind: str, **body) -> dict:
        prev = self._entries[-1]['digest'] if self._entries else ''
        digest = hashlib.sha256(
            (prev + _canon(dict(body, kind=kind))).encode()).hexdigest()[:16]
        entry = dict(body, kind=kind, at=round(time.time(), 3),
                     prev=prev, digest=digest)
        if self.path:
            os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
            with open(self.path, 'a', encoding='utf-8') as fh:
                fh.write(json.dumps(entry, ensure_ascii=False, default=str) + '\n')
                fh.flush()
        self._entries.append(entry)
        return entry

    # -- read ---------------------------------------------------------
    def entries(self, kind: str = None) -> list:
        return [e for e in self._entries if kind is None or e.get('kind') == kind]

    def constraints(self) -> list:
        return [e for e in self.entries('constraint')]

    def judgments(self) -> list:
        """Verified conclusions: admitted and rejected, with their evidence."""
        return self.entries('judgment')

    def hypotheses(self) -> list:
        return self.entries('hypothesis')

    def last_plan(self) -> dict:
        plans = self.entries('plan')
        return plans[-1] if plans else {}

    def tried(self) -> dict:
        """delta_key -> outcome of every intervention ever evaluated."""
        out = {}
        for e in self.entries('result'):
            out[e['delta']] = e.get('outcome', 'unknown')
        return out

    def pending(self) -> set:
        return {e['delta'] for e in self.entries('proposal')
                if e['delta'] not in self.tried()}

    def results_for(self, variable: str) -> list:
        return [e for e in self.entries('result') if e.get('variable') == variable]

    def verify_chain(self) -> bool:
        prev = ''
        for e in self._entries:
            if e.get('prev') != prev:
                return False
            body = {k: v for k, v in e.items()
                    if k not in ('prev', 'digest', 'at')}
            if hashlib.sha256((prev + _canon(body)).encode()).hexdigest()[:16] != e.get('digest'):
                return False
            prev = e['digest']
        return True

    @property
    def chain_ok(self) -> bool:
        return self._chain_ok
