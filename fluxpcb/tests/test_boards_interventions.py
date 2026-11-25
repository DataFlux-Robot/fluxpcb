"""The verifiers must be able to fail — proven on real copper.

Each mutation changes a board in exactly the way one rule is about, and
the verifier that owns that rule has to notice.  A mutation the board's
own rules do not constrain is reported as "cannot be generated, here is
why", never as a pass — an unconstrained rule is a blind spot only while
the board leaves it unconstrained.

The calibration test is the one the reference project asks for: a bad
example must yield strictly more high-level findings than a good one.
"""
import os
import sys

import pytest

pytest.importorskip('pcbnew')

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from fluxpcb.repair_drc import no_assert_dialogs  # noqa: E402
from fluxpcb.tests import boards_local as F  # noqa: E402
from fluxpcb.verifiers import interventions as IV  # noqa: E402

pytestmark = pytest.mark.board
GOOD = '0023'


def test_each_constrained_rule_flips_under_its_own_mutation(tmp_path):
    no_assert_dialogs()
    pcb = F.board(GOOD, tmp_path)
    base = IV.drc_findings(pcb)
    assert base == {}, base                # the clean control really is clean
    records = IV.prove_all(pcb, str(tmp_path / 'iv'))
    by = {r['mutation']: r for r in records}
    for name in ('mutation:thin', 'mutation:move', 'mutation:short',
                 'mutation:via_drill'):
        r = by[name]
        assert r['generated'], (name, r.get('why'))
        assert r['crossed'], (name, r.get('why'), r.get('new_findings'))
        assert r['killed'] and not r['blind_spot'], (name, r)
    # the edge rule is not constrained on this board: reported, not passed
    edge = by['mutation:edge']
    assert not edge['generated']
    assert 'no copper-to-edge clearance' in edge['why']
    summary = IV.summary(records)
    assert sum(v['crossed'] for v in summary.values()) >= 4


def test_the_mutations_feed_the_rule_registry_that_was_designed_for_them():
    """contract/pairs.py declares rules validated by 'mutation:thin' etc.
    and had no producer; interventions.summary() has that shape."""
    from fluxpcb.contract.pairs import RulePairRegistry
    reg = RulePairRegistry.default()
    ops = {p.evidence for p in reg.pairs.values() if p.evidence.startswith('mutation:')}
    assert ops, 'the seed pairs should declare mutation evidence'
    # statuses() keys the summary by the mutation NAME after the
    # prefix ('thin', not 'mutation:thin')
    summary = {op.split(':', 1)[1]: {'generated': 1, 'crossed': 1,
                                     'killed': 1} for op in ops}
    have = {'mutation:' + n.split(':', 1)[1] for n in IV.MUTATIONS}
    assert have >= ops, (
        'every mutation a seed pair names must exist as an intervention')
    st = reg.statuses(mutation_summary=summary)
    assert st and all(isinstance(v, str) for v in st.values())
    assert 'validated' in set(st.values())


def test_a_good_board_has_no_p0_where_a_bad_one_has_several(tmp_path):
    no_assert_dialogs()
    good = F.board(GOOD, tmp_path)
    bad = []
    for key in ('0054', '0400', '0520', '0571'):
        p = F.board(key, tmp_path / key)
        bad.append(p)
    for name in F.control_names()[:3]:
        bad.append(F.control(name, tmp_path))
    cal = IV.calibrate(good, bad)
    assert cal['ok'], cal
    assert cal['good']['P0'] == 0
    assert all(r['more_than_good'] for r in cal['bad']), cal['bad']
    # and the gap is a real gap, not one finding: at least one bad board
    # has several P0 findings
    assert max(r['P0'] for r in cal['bad']) >= 2
