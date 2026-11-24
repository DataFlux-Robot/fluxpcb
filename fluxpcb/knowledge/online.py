"""Online knowledge acquisition — arxiv / openalex / semantic scholar.

A query is the KEYWORDS of an error model (its conditions and
counterfactual), never board files and never keys.  All endpoints are
stdlib urllib with retry/backoff; failures return [] and the loop proceeds
with local knowledge alone.
"""
from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request

UA = {'User-Agent': 'fluxpcb-meta/0.1 (research; mailto:fluxpcb@example.invalid)'}
TIMEOUT = 20
TRIES = 2


def _get(url, tries=TRIES):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                return json.loads(r.read())
        except Exception:
            if i + 1 < tries:
                time.sleep(1.5 * (i + 1))
    return None


def arxiv(query: str, n: int = 5) -> list:
    """arXiv search hits as knowledge sources."""
    q = urllib.parse.quote(query)
    data = _get(f'http://export.arxiv.org/api/query?search_query=all:{q}'
                f'&start=0&max_results={n}')
    if not data:
        return []
    out = []
    import re
    ns = {'a': 'http://www.w3.org/2005/Atom'}
    for e in data.get('feed', {}).get('entry', [])[:n]:
        title = re.sub(r'\s+', ' ', e.get('title', '')).strip()
        summary = re.sub(r'\s+', ' ', e.get('summary', '')).strip()
        out.append({'url': e.get('id', ''), 'quote': f'{title}. {summary[:400]}'})
    return out


def openalex(query: str, n: int = 5) -> list:
    q = urllib.parse.quote(query)
    data = _get('https://api.openalex.org/works?search=' + q +
                f'&per-page={n}')
    if not data:
        return []
    out = []
    for w in data.get('results', [])[:n]:
        title = w.get('title') or ''
        abstract = ''
        inv = (w.get('abstract_inverted_index') or {})
        if inv:
            pos = {}
            for word, idxs in inv.items():
                for i in idxs:
                    pos[i] = word
            abstract = ' '.join(pos[i] for i in sorted(pos))
        out.append({'url': w.get('doi') or w.get('id') or '',
                    'quote': f'{title}. {abstract[:400]}'})
    return out


def semantic_scholar(query: str, n: int = 5) -> list:
    q = urllib.parse.quote(query)
    data = _get('https://api.semanticscholar.org/graph/v1/paper/search'
                f'?query={q}&limit={n}&fields=title,abstract,url,externalIds')
    if not data:
        return []
    out = []
    for p in (data.get('data') or [])[:n]:
        out.append({'url': p.get('url') or '',
                    'quote': f"{p.get('title') or ''}. "
                             f"{(p.get('abstract') or '')[:400]}"})
    return out


def search(query: str, n: int = 5) -> list:
    """Union of the three sources, deduplicated by url."""
    out, seen = [], set()
    for src in (arxiv, openalex, semantic_scholar):
        for hit in src(query, n):
            if hit.get('url') and hit['url'] not in seen:
                seen.add(hit['url'])
                out.append(hit)
    return out
