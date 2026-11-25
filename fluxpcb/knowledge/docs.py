"""Numbered knowledge documents, loaded on demand.

The repository's knowledge is real but scattered: ``KNOWHOW.md`` has
K1..K30+, ``fluxpcb/research/LITREVIEW_2026-11-25/`` has five lettered
surveys, ``.agents/skills/*/SKILL.md`` has the operational skills, and
``prompts/`` has the versioned prompt texts.  Until now nothing indexed
them, so an agent either had all of it (too much) or had to guess a path
(usually wrong).

This module builds a registry — stable ids, one line of "when to read
it", the anchor of the section — and reads exactly the document asked
for.  Ids come from the documents themselves (``K7``, ``L3``,
``S_endpoint_attach``, ``P01``), so a citation in a finding, a prompt or
a report keeps pointing at the same text.

    registry()                -> [{'id','title','path','anchor','when_to_read','kind'}]
    load('K7')                -> text (one section)
    load('L3', max_chars=...) -> the survey, truncated
    index_text(limit=20)      -> a compact table for a prompt
    find('return path')       -> ids whose title matches
"""
from __future__ import annotations

import os
import re
from functools import lru_cache

# the package is <repo>/fluxpcb; the documents live at <repo>
REPO = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))

# directories that hold knowledge, and the id namespace each gets
SOURCES = (
    ('knowhow', 'KNOWHOW.md', 'K'),
    ('litreview', os.path.join('fluxpcb', 'research', 'LITREVIEW_2026-11-25'), 'L'),
    ('research', os.path.join('fluxpcb', 'research'), 'R'),
    ('skills', os.path.join('.agents', 'skills'), 'S'),
    ('prompts', 'prompts', 'P'),
)

_K_HEAD = re.compile(r'^##+\s*(K\d+)\s*[:：]?\s*(.+?)\s*$')
_L_FILE = re.compile(r'^([A-E])_.*\.md$')


def _k_sections(text: str) -> list:
    out = []
    cur = None
    for line in text.splitlines():
        m = _K_HEAD.match(line)
        if m:
            if cur:
                out.append(cur)
            cur = {'id': m.group(1), 'title': m.group(2), 'body': []}
        elif line.startswith('## ') and cur:
            out.append(cur)
            cur = None
        elif cur is not None:
            cur['body'].append(line)
    if cur:
        out.append(cur)
    return out


def _one_line(text: str, limit: int = 120) -> str:
    for line in (text or '').splitlines():
        s = line.strip().lstrip('-*#> ').strip()
        if s:
            return s[:limit]
    return ''


@lru_cache(maxsize=1)
def registry() -> tuple:
    """Every numbered document, with the one line that says when to read it."""
    out = []
    # KNOWHOW.md: one entry per K-section
    kh = os.path.join(REPO, 'KNOWHOW.md')
    if os.path.exists(kh):
        with open(kh, encoding='utf-8', errors='replace') as fh:
            text = fh.read()
        seen = {}
        for sec in _k_sections(text):
            # KNOWHOW.md repeats a few numbers (K14, K21, K33); the first
            # keeps the id, the rest get .2, .3 — every id still loads
            sid = sec['id']
            if sid in seen:
                seen[sid] += 1
                sid = f"{sec['id']}.{seen[sid]}"
            else:
                seen[sid] = 1
            out.append({'id': sid, 'kind': 'knowhow', 'path': 'KNOWHOW.md',
                        'title': sec['title'],
                        'anchor': sec['id'],
                        'when_to_read': 'when the work touches: '
                                        + _one_line(sec['title'])})
    # literature surveys
    for kind, rel, prefix in SOURCES[1:]:
        d = os.path.join(REPO, rel)
        if not os.path.isdir(d):
            continue
        if kind == 'skills':
            # one directory per skill, each with a SKILL.md
            names = [os.path.join(n, 'SKILL.md')
                     for n in sorted(os.listdir(d))
                     if os.path.isfile(os.path.join(d, n, 'SKILL.md'))]
        else:
            names = sorted(n for n in os.listdir(d) if n.endswith('.md'))
        for i, name in enumerate(names, start=1):
            path = os.path.join(rel, name)
            full = os.path.join(REPO, path)
            head = ''
            try:
                with open(full, encoding='utf-8', errors='replace') as fh:
                    head = _one_line(fh.read(1200))
            except OSError:
                pass
            if kind == 'litreview':
                m = _L_FILE.match(name)
                doc_id = f'L{m.group(1)}' if m else f'L{i}'
                title = name[:-3].replace('_', ' ')
                when = ('when designing or auditing: ' + title.split('_')[-1])
            elif kind == 'skills':
                doc_id = 'S_' + os.path.basename(os.path.dirname(full))
                title = doc_id[2:]
                when = head or 'when the work matches this skill'
            elif kind == 'prompts':
                doc_id = f'P{i:02d}'
                title = name[:-3]
                when = 'the versioned prompt text for ' + name[:-3]
            else:
                if name == 'README.md':
                    continue
                doc_id = f'R{i:02d}'
                title = name[:-3]
                when = 'background: ' + name[:-3].replace('_', ' ')
            out.append({'id': doc_id, 'kind': kind, 'path': path,
                        'title': title, 'anchor': '', 'when_to_read': when})
    return tuple(out)


def ids() -> list:
    return [d['id'] for d in registry()]


def entry(doc_id: str) -> dict:
    for d in registry():
        if d['id'] == doc_id:
            return dict(d)
    return {}


def find(query: str, limit: int = 8) -> list:
    q = (query or '').lower()
    hits = [d for d in registry()
            if q in (d['title'] or '').lower()
            or q in (d['when_to_read'] or '').lower()]
    return [d['id'] for d in hits[:limit]]


def load(doc_id: str, max_chars: int = 6000) -> str:
    """The document (one K-section for knowhow), read now."""
    d = entry(doc_id)
    if not d:
        return ''
    full = os.path.join(REPO, d['path'])
    try:
        with open(full, encoding='utf-8', errors='replace') as fh:
            text = fh.read()
    except OSError:
        return ''
    if d['kind'] == 'knowhow':
        want = doc_id.split('.')[0]
        nth = int(doc_id.split('.')[1]) if '.' in doc_id else 1
        found = 0
        for sec in _k_sections(text):
            if sec['id'] != want:
                continue
            found += 1
            if found == nth:
                body = '\n'.join(sec['body']).strip()
                return body[:max_chars]
        return ''
    return text[:max_chars]


def index_text(limit: int = 24, kinds=None, width: int = 96) -> str:
    """A compact table a prompt can carry: id, title, when to read it."""
    rows = [d for d in registry() if not kinds or d['kind'] in kinds]
    lines = []
    for d in rows[:limit]:
        line = f"{d['id']:<18} {d['title'][:44]:<46} {d['when_to_read'][:width]}"
        lines.append(line.rstrip())
    return '\n'.join(lines)


def stats() -> dict:
    out = {}
    for d in registry():
        out[d['kind']] = out.get(d['kind'], 0) + 1
    return out
