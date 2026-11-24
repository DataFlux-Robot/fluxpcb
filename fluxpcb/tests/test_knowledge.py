"""Knowledge layer tests: BM25 hits known sections; cards need sources."""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from fluxpcb.knowledge.cards import (KnowledgeCard, card_of,  # noqa: E402
                                     validate_card)
from fluxpcb.knowledge.index import BM25Index  # noqa: E402

CORPUS = [
    os.path.join(os.path.dirname(ROOT), 'KNOWHOW.md'),
]


def _tmp_corpus(tmp_path):
    """A tiny corpus with known sections, plus an extra file."""
    a = tmp_path / 'a.md'
    a.write_text('# BGA escape\nDogbone fanout at 0.8 mm pitch.\n\n'
                 '# Power stitching\nStitch power fragments with vias.',
                 encoding='utf-8')
    b = tmp_path / 'b.md'
    b.write_text('# Clearance\nA 0.16 mm grid cannot resolve 0.35 mm '
                 'clearance.', encoding='utf-8')
    return [str(a), str(b)]


def test_index_scores_known_sections(tmp_path):
    idx = BM25Index(_tmp_corpus(tmp_path))
    hits = idx.search('dogbone fanout pitch')
    assert hits and 'BGA escape' in hits[0]['head']
    assert hits[0]['path'].endswith('a.md')
    assert any('0.16 mm grid' in h['snippet'] for h in
               idx.search('grid clearance 0.35'))


def test_index_handles_missing_and_json_files(tmp_path):
    (tmp_path / 'rules.json').write_text(
        '{"bga_rules": {"pitch": 0.8, "track": 0.1}}', encoding='utf-8')
    idx = BM25Index(_tmp_corpus(tmp_path) + [str(tmp_path / 'rules.json')])
    hits = idx.search('bga_rules pitch track')
    assert hits and 'rules.json' in hits[0]['path']
    assert BM25Index([]).search('x') == []


def test_repo_knowhow_is_indexable_and_hits_escape_knowledge():
    if not os.path.exists(CORPUS[0]):
        return
    idx = BM25Index(CORPUS)
    hits = idx.search('BGA escape dogbone fanout')
    assert hits, 'KNOWHOW should answer BGA escape queries'
    assert hits[0]['snippet']


def test_card_without_source_is_rejected():
    draft = {'technique': 'teardrop', 'applies_when': 'tight',
             'mechanism': 'add copper at junctions'}
    card = card_of(draft, sources=[])
    assert 'no source' in ' '.join(validate_card(card))
    ok = card_of(draft, sources=[{'path': 'x.md', 'quote': 'teardrops '
                                  'reduce stress'}])
    assert validate_card(ok) == []
    assert ok.card_id() == KnowledgeCard(**ok.as_dict()).card_id()


def test_card_source_needs_a_quote():
    card = KnowledgeCard(technique='x', applies_when='any', mechanism='y',
                         needs_primitives=[], sources=[{'path': 'a.md'}])
    assert any('quote' in e for e in validate_card(card))


def test_statuses_are_closed_and_marked():
    c = card_of({'technique': 't', 'mechanism': 'm'},
                sources=[{'path': 'p', 'quote': 'q'}])
    assert c.status == 'draft'
    from fluxpcb.knowledge.cards import mark
    mark(c, 'falsified', 'no gain on the coreset')
    assert c.status == 'falsified' and validate_card(c) == []
    bad = card_of({'technique': 't', 'mechanism': 'm'},
                  sources=[{'path': 'p', 'quote': 'q'}], status='magic')
    assert any('bad status' in e for e in validate_card(bad))
