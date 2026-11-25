"""Local knowledge index — BM25 over the corpus the loop already owns.

The corpus is prose and JSON the project has accumulated (KNOWHOW, research
notes, skills, literature extracts).  The index is pure Python, no
dependencies: the meta loop queries it for a failing error model and gets
back sections with scores, never the whole file.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re

# ---------------------------------------------------------------- tokenise
_SPLIT = re.compile(r'[A-Za-z0-9_./+-]+|[\u4e00-\u9fff]')
_STOP = frozenset((
    'the a an of and or to in on for with is are was be as at by from that '
    'this it its not no we you they he she can will if then than'
).split())


def tokens(text: str) -> list:
    return [t.lower() for t in _SPLIT.findall(text or '') if t.lower() not in _STOP]


def _sections(path: str):
    """Split a markdown file into (heading-chain, body) sections.

    A section starts at a '#' heading; the heading text (normalised) is
    part of the section's text so a query can match headings.
    """
    try:
        text = open(path, encoding='utf-8', errors='replace').read()
    except OSError:
        return []
    if not text.strip():
        return []
    lines = text.splitlines()
    out = []
    chain, body = [], []
    head = []
    for line in lines:
        m = re.match(r'^(#{1,4})\s+(.*)$', line)
        if m:
            if head or body:
                out.append((tuple(head), '\n'.join(body)))
            level = len(m.group(1))
            title = m.group(2).strip()
            head = head[:level - 1] + [title]
            body = [title]
        else:
            body.append(line)
    if head or body:
        out.append((tuple(head), '\n'.join(body)))
    return out


def _chunks(items, max_chars=3000):
    """Long bodies become overlapping chunks so a paragraph is addressable."""
    for head, body in items:
        if len(body) <= max_chars:
            yield head, body
            continue
        step = max_chars // 2
        for i in range(0, len(body), step):
            yield head, body[i:i + max_chars]


class BM25Index:
    def __init__(self, paths: list, k1: float = 1.5, b: float = 0.75):
        self.k1, self.b = k1, b
        self.docs = []          # [{path, head, body, tokens, key}]
        self.df = {}
        self.avgdl = 0.0
        for p in sorted(set(paths)):
            if not os.path.exists(p):
                continue
            if p.endswith('.json'):
                for head, body in self._json_sections(p):
                    self._add(p, head, body)
            else:
                for head, body in _chunks(_sections(p)):
                    self._add(p, head, body)
        if self.docs:
            self.avgdl = sum(len(d['tokens']) for d in self.docs) / len(self.docs)

    def _add(self, path, head, body):
        toks = tokens(' '.join(head) + ' ' + body)
        if not toks:
            return
        key = hashlib.sha256(f'{path}|{"#".join(head)}'.encode()).hexdigest()[:12]
        self.docs.append({'path': path, 'head': list(head), 'body': body,
                          'tokens': toks, 'key': key})
        for t in set(toks):
            self.df[t] = self.df.get(t, 0) + 1

    def _json_sections(self, path):
        try:
            data = json.load(open(path, encoding='utf-8', errors='replace'))
        except (OSError, ValueError):
            return
        text = json.dumps(data, ensure_ascii=False, indent=1)
        yield (), text

    def _score(self, doc, query):
        n = len(self.docs)
        s = 0.0
        dl = len(doc['tokens'])
        for t in set(query):
            df = self.df.get(t)
            if not df:
                continue
            tf = doc['tokens'].count(t)
            idf = math.log(1 + (n - df + 0.5) / (df + 0.5))
            s += idf * tf * (self.k1 + 1) / (
                tf + self.k1 * (1 - self.b + self.b * dl / max(1.0, self.avgdl)))
        return s

    def search(self, query: str, k: int = 8) -> list:
        q = tokens(query)
        if not q or not self.docs:
            return []
        scored = [(self._score(d, q), d) for d in self.docs]
        scored.sort(key=lambda x: -x[0])
        out = []
        for s, d in scored[:max(k, 1) * 3]:
            if s <= 0:
                break
            out.append({'score': round(s, 4), 'path': d['path'],
                        'head': d['head'], 'key': d['key'],
                        'snippet': _snippet(d['body'], q)})
            if len(out) >= k:
                break
        return out


def _snippet(body, query, width=220):
    low = body.lower()
    pos = None
    for t in query:
        i = low.find(t)
        if i >= 0 and (pos is None or i < pos):
            pos = i
    if pos is None:
        return body[:width]
    return body[max(0, pos - 60):pos + width]


def default_corpus() -> list:
    """The corpus the loop owns, by root."""
    roots = [os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))))]  # flux-neurotransmitter
    out = []
    for root in roots:
        for rel in ('KNOWHOW.md', 'fluxpcb/research', '.agents/skills'):
            p = os.path.join(root, rel)
            if not os.path.isdir(p):
                if os.path.exists(p):
                    out.append(p)
                continue
            # one level deep as well: LITREVIEW_2026-11-25/*.md and
            # .agents/skills/<name>/SKILL.md were invisible before
            for name in sorted(os.listdir(p)):
                full = os.path.join(p, name)
                if os.path.isfile(full) and name.endswith('.md'):
                    out.append(full)
                elif os.path.isdir(full):
                    for sub_name in sorted(os.listdir(full)):
                        if sub_name.endswith('.md'):
                            out.append(os.path.join(full, sub_name))
    bench = 'E:/DATA/vscode/ZCODE/autorouter-bench'
    if os.path.isdir(bench):
        for rel in ('.litrev', '.litreview', 'work/lit',
                    'work/lit_dsn_20261121'):
            p = os.path.join(bench, rel)
            if not os.path.isdir(p):
                continue
            for dirpath, dirs, files in os.walk(p):
                dirs[:] = [d for d in dirs if d not in ('.git',)]
                for f in files:
                    if f.endswith(('.md', '.txt', '.json')):
                        out.append(os.path.join(dirpath, f))
    return out
