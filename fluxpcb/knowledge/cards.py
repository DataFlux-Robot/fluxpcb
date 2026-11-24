"""KnowledgeCard — a cited technique the loop may act on.

A card is only as good as its source.  `validate_card` enforces the one
hard rule: no source, no card.  The LLM may draft the mechanism from
retrieved passages; the sources list is what makes the draft citable or
discardable.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field

STATUS = ('draft', 'tested', 'falsified', 'admitted')


@dataclass
class KnowledgeCard:
    technique: str
    applies_when: str
    mechanism: str
    needs_primitives: list
    sources: list                 # [{path|url, quote}]
    evidence: str = ''
    status: str = 'draft'
    error_models: list = field(default_factory=list)   # model ids addressed

    def card_id(self) -> str:
        body = json.dumps(self.as_dict(), sort_keys=True,
                          separators=(',', ':'), default=str)
        return hashlib.sha256(body.encode('utf-8')).hexdigest()[:16]

    def as_dict(self) -> dict:
        return asdict(self)


def validate_card(card) -> list:
    """Contract violations; an empty list means the card may be used."""
    if isinstance(card, KnowledgeCard):
        card = card.as_dict()
    errs = []
    if not (card.get('technique') or '').strip():
        errs.append('no technique')
    if not (card.get('mechanism') or '').strip():
        errs.append('no mechanism')
    if not card.get('sources'):
        errs.append('no source: uncited knowledge is not admitted')
    for s in card.get('sources') or []:
        if not (isinstance(s, dict) and (s.get('path') or s.get('url'))
                and (s.get('quote') or '').strip()):
            errs.append('source without path/url or quote')
    if card.get('status') not in STATUS:
        errs.append(f"bad status {card.get('status')!r}")
    return errs


def card_of(draft: dict, sources: list = None,
            error_models: list = None, status: str = 'draft') -> KnowledgeCard:
    """A card from an LLM draft plus the retrieved sources it cites.

    The caller passes the passages the draft was based on; the draft may
    only reference what is in them.  `sources` carries path/url + quote.
    """
    cited = (draft.get('sources') or []) if sources is None else sources
    return KnowledgeCard(
        technique=(draft.get('technique') or '').strip(),
        applies_when=(draft.get('applies_when') or '').strip(),
        mechanism=(draft.get('mechanism') or '').strip(),
        needs_primitives=list(draft.get('needs_primitives') or []),
        sources=list(cited or []),
        evidence=(draft.get('evidence') or '').strip(),
        status=status,
        error_models=list(error_models or []),
    )


def mark(card: KnowledgeCard, status: str, evidence: str) -> KnowledgeCard:
    card.status = status
    card.evidence = evidence
    return card
