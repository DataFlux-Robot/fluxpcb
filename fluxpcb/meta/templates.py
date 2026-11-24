"""Editable templates — the meta process's own prompts, versioned.

Hyperagents' point, kept at depth one: the text that drives induce /
retrieve / abstract is itself a versioned artifact.  Component 2 may
propose an edit; the edit takes effect only for the next generation and
its own value is judged by the generations that follow.  A template
without a digest entry is not used.
"""
from __future__ import annotations

import hashlib
import json
import os

TEMPLATES = ('induce', 'retrieve', 'abstract')

DEFAULTS = {
    'induce': ('From these failure certificates {certs}, group them into '
               'error models by their conditions {conditions}. Each model '
               'gets a one-line mechanism citing example ids.'),
    'retrieve': ('For error model {model}, query {keywords} and cite every '
                 'claim with a source.'),
    'abstract': ('Summarise what changed for model {model_id}: the field '
                 '{field} was predicted to move {direction}; it moved '
                 '{before} -> {after}.'),
}


class Templates:
    def __init__(self, path: str):
        self.path = path
        self._load()

    def _load(self):
        self.items = {}
        if path_exists := os.path.exists(self.path):
            with open(self.path, encoding='utf-8') as fh:
                self.items = json.load(fh)
        for name in TEMPLATES:
            if name not in self.items:
                self.items[name] = {'text': DEFAULTS[name],
                                    'version': 1, 'history': []}
            if not self.items[name].get('digest'):
                self.items[name]['digest'] = _digest(
                    self.items[name]['text'])

    def render(self, name: str, **ctx) -> str:
        item = self.items.get(name)
        if not item or not item.get('digest'):
            raise ValueError(f'template {name!r} has no digest')
        try:
            return item['text'].format(**ctx)
        except (KeyError, IndexError, ValueError) as e:
            raise ValueError(f'template {name!r} render failed: {e}')

    def propose(self, name: str, text: str, why: str) -> dict:
        """A component-2 edit: recorded, digest-bound, not yet current."""
        if name not in TEMPLATES:
            raise ValueError(f'unknown template {name!r}')
        item = self.items[name]
        entry = {'text': text, 'why': why,
                 'version': item['version'] + 1,
                 'prev_digest': item.get('digest'),
                 'digest': _digest(text)}
        self.items[name]['history'].append(entry)
        return entry

    def apply(self, name: str, digest: str) -> bool:
        """Make a proposed edit current (component 2 decides when)."""
        item = self.items.get(name)
        for e in reversed(item.get('history') or []):
            if e['digest'] == digest:
                item['text'], item['version'] = e['text'], e['version']
                item['digest'] = e['digest']
                return True
        return False

    def save(self):
        os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        with open(self.path, 'w', encoding='utf-8') as fh:
            json.dump(self.items, fh, indent=1, ensure_ascii=False)

    def current(self, name: str) -> str:
        item = self.items.get(name) or {}
        return item.get('digest', '')[:12]


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode('utf-8')).hexdigest()[:16]
