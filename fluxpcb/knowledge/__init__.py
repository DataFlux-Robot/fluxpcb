"""fluxpcb knowledge — retrieval for the meta loop (component 1's eyes).

index.py  : BM25 over the corpus the project already owns.
online.py : arxiv / openalex / semantic-scholar adapters with backoff; a
            query is an error model's KEYWORDS, never board files.
cards.py  : KnowledgeCard — a cited, falsifiable technique the loop may
            act on; a card without a source is discarded.
"""
from .cards import KnowledgeCard, card_of, validate_card
from .index import BM25Index, default_corpus

__all__ = ['BM25Index', 'default_corpus', 'KnowledgeCard', 'card_of',
           'validate_card']
