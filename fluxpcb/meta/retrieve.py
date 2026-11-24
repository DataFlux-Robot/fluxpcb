"""Retrieval orchestration — error model → passages → cited card.

Component 1's eyes: for a zero-reach error model, query the local index
first (always), the network when allowed, and turn the passages into a
KnowledgeCard.  The card is validated; an uncited draft is discarded.
"""
from __future__ import annotations

from ..knowledge.cards import KnowledgeCard, card_of, validate_card
from ..knowledge.index import BM25Index
from ..knowledge.online import search as online_search
from .error_model import ErrorModel


# condition value -> discriminating domain terms.  A raw counterfactual
# sentence is mostly stopwords for BM25 ("the blocking net must move");
# these are the words the knowledge base actually uses.
TERMS = {
    'sealed': 'sealed anchor via slot escape fanout',
    'no_gap': 'clearance corridor gap shove',
    'tight': 'clearance near miss',
    'track': 'track blocker nudge rip up reroute',
    'pad': 'pad clearance escape',
    'via': 'via clearance',
    'unconnected': 'open connection stitch',
    'track_width': 'track width neckdown widen',
    'clearance': 'clearance violation',
    'budget': 'timeout budget passes',
    'verify_rejected': 'verified rejected DRC',
    'no_path': 'no path blocked',
}


def keywords_of(model: ErrorModel) -> str:
    """Discriminating query terms for the model's conditions."""
    parts = []
    for c in model.conditions:
        if c in TERMS:
            parts.append(TERMS[c])
    for w in ('rip', 'nudge', 'shove', 'sealed', 'narrower'):
        if w in model.counterfactual:
            parts.append(w)
    return ' '.join(dict.fromkeys(' '.join(parts).split()))[:200]


def retrieve(model: ErrorModel, index: BM25Index = None,
             allow_online: bool = True, k: int = 6) -> list:
    """Passages (local + online) for one error model."""
    q = keywords_of(model)
    hits = (index.search(q, k=k) if index is not None else [])
    if allow_online:
        try:
            for h in online_search(q, n=3):
                hits.append({'score': None, 'path': h.get('url'),
                             'head': [], 'snippet': h.get('quote', '')[:300],
                             'key': '', 'online': True,
                             'quote': h.get('quote', '')[:300],
                             'url': h.get('url')})
        except Exception:
            pass
    return hits


def draft_card(model: ErrorModel, passages: list,
               allow_llm: bool = True) -> KnowledgeCard:
    """A cited card from passages; the LLM may name the technique."""
    sources = []
    for p in passages:
        url = p.get('url') or p.get('path')
        quote = p.get('quote') or p.get('snippet') or ''
        if url and quote.strip():
            sources.append({'path' if 'path' in p or not p.get('url')
                            else 'url': url, 'quote': quote[:500]})
    if not sources:
        return None
    technique, mechanism = '', ''
    if allow_llm:
        technique, mechanism = _llm_draft(model, passages)
    if not technique:
        technique = f"counterfactual for {model.conditions[0]}"
    if not mechanism:
        mechanism = model.counterfactual
    card = card_of({'technique': technique,
                    'applies_when': ' | '.join(str(c) for c in model.conditions),
                    'mechanism': mechanism,
                    'needs_primitives': []},
                   sources=sources, error_models=[model.model_id()])
    return card if not validate_card(card) else None


def _llm_draft(model: ErrorModel, passages: list) -> tuple:
    try:
        from ..llm import LLMAdapter
        adapter = LLMAdapter()
    except Exception:
        return '', ''
    if not adapter.online:
        return '', ''
    import json
    prompt = ('From these passages, name the technique and its mechanism '
              'in one sentence each, as JSON {"technique": "...", '
              '"mechanism": "..."}:\n'
              + json.dumps({'conditions': list(model.conditions),
                            'counterfactual': model.counterfactual,
                            'passages': [(p.get('snippet') or '')[:400]
                                         for p in passages[:4]]},
                           ensure_ascii=False))
    out = adapter.complete('You answer with JSON only.', prompt)
    if not out.get('ok'):
        return '', ''
    try:
        text = out.get('text') or ''
        body = json.loads(text[text.index('{'):text.rindex('}') + 1])
        return body.get('technique', ''), body.get('mechanism', '')
    except ValueError:
        return '', ''
