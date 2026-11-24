"""Templates and retrieval orchestration."""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from fluxpcb.meta.error_model import ErrorModel  # noqa: E402
from fluxpcb.meta.retrieve import (draft_card, keywords_of,  # noqa: E402
                                   retrieve)
from fluxpcb.meta.templates import Templates  # noqa: E402


def _model():
    return ErrorModel(conditions=('verify_rejected', 'unconnected',
                                  'lt20um', 'no_gap', 'track', 'fine', '2',
                                  'mid'),
                      counterfactual=('corridor short by lt20um: blocker '
                                      'must move >= 0.017 mm'))


def test_templates_render_version_and_apply(tmp_path):
    t = Templates(str(tmp_path / 'templates.json'))
    rendered = t.render('induce', certs='x', conditions='y')
    assert 'x' in rendered and 'y' in rendered
    old = t.current('induce')
    e = t.propose('induce', 'new text {certs}', 'test')
    assert e['digest'] and e['prev_digest'] is not None
    t.save()
    again = Templates(str(tmp_path / 'templates.json'))
    assert again.items['induce']['history'][-1]['digest'] == e['digest']
    assert again.apply('induce', e['digest'])
    assert again.render('induce', certs='c') == 'new text c'
    assert again.current('induce') != old
    with pytest.raises(ValueError):
        t.propose('nope', 'x', 'y')
    with pytest.raises(ValueError):
        # a rendered template must be digest-bound; craft a broken one
        t.items['abstract'] = {'text': '{missing}', 'version': 1}
        t.render('abstract')


def test_retrieve_returns_cited_passages_and_no_card_without_sources(tmp_path):
    m = _model()
    assert 'rejected' in keywords_of(m) and 'rip' in keywords_of(m)
    (tmp_path / 'a.md').write_text('# Corridor\nA short corridor by 20 um '
                                   'needs a narrower track.', encoding='utf-8')
    from fluxpcb.knowledge.index import BM25Index
    idx = BM25Index([str(tmp_path / 'a.md')])
    passages = retrieve(m, idx, allow_online=False)
    assert passages and 'narrower' in passages[0]['snippet']
    card = draft_card(m, passages, allow_llm=False)
    assert card is not None
    from fluxpcb.knowledge.cards import validate_card
    assert validate_card(card) == []
    assert card.error_models == [m.model_id()]
    assert draft_card(m, [], allow_llm=False) is None
