"""Tool evolution and gate tests: the sandbox keeps bad code out."""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from fluxpcb.meta import tool_evolve as TE  # noqa: E402
from fluxpcb.meta import tool_gate as TG  # noqa: E402
from fluxpcb.meta.error_model import ErrorModel  # noqa: E402
from fluxpcb.tools.builtins import builtins  # noqa: E402

GOOD_TOOL = '''"""A lawful tool."""
import json
import math


def run(args):
    x = args.get("x", 0)
    return {"ok": True, "result": math.hypot(x, 1.0)}
'''

BAD_IMPORT = '''"""Uses subprocess."""
import subprocess


def run(args):
    return subprocess.run(["echo"]).returncode == 0
'''

BAD_BOARD = '''"""Board-specific hack."""


def run(args):
    # 0054_induction-hob needs its special corridor
    return {"ok": True}
'''

NO_RUN = 'x = 1\n'

BAD_NAME = GOOD_TOOL


def _model():
    return ErrorModel(conditions=('verify_rejected', 'unconnected',
                                  'lt20um', 'no_gap', 'track', 'fine', '2',
                                  'mid'))


def test_l0_rejects_banned_imports_board_names_and_missing_run():
    assert TG.check_static(GOOD_TOOL, 't1')['ok']
    assert not TG.check_static(BAD_IMPORT, 't2')['ok']
    assert any('banned' in p for p in TG.check_static(BAD_IMPORT, 't2')['problems'])
    assert any('board-name' in p for p in TG.check_static(BAD_BOARD, 't3')['problems'])
    assert any('run(' in p for p in TG.check_static(NO_RUN, 't4')['problems'])
    assert any('bad tool name' in p for p in TG.check_static(GOOD_TOOL, 'Bad Name')['problems'])


def test_sandbox_loads_only_what_it_wrote(tmp_path):
    sb = TG.Sandbox(str(tmp_path))
    sb.write('ok_tool', GOOD_TOOL)
    mod = sb.load('ok_tool')
    assert mod.run({'x': 3})['ok'] is True
    assert sb.check('ok_tool')['ok']
    sb.write('bad_tool', BAD_IMPORT)
    assert not sb.check('bad_tool')['ok']
    try:
        sb.load('missing')
        assert False, 'missing tool must raise'
    except FileNotFoundError:
        pass


def test_ladder_stops_at_the_first_failed_level(tmp_path):
    out = TG.ladder(BAD_IMPORT, 'x', smoke=lambda: {'pass': True},
                    effect=lambda: {'pass': True},
                    controls=lambda: {'pass': True},
                    confirm=lambda: {'pass': True})
    assert not out['admitted'] and out['blocked'] == 'l0_static'
    ok = TG.ladder(GOOD_TOOL, 'y', smoke=lambda: {'pass': True},
                   effect=lambda: {'pass': True},
                   controls=lambda: {'pass': True},
                   confirm=lambda: {'pass': True})
    assert ok['admitted'] and ok['levels']['l4_confirm']['pass'] is True
    missing = TG.ladder(GOOD_TOOL, 'z', smoke=lambda: {'pass': True})
    assert not missing['admitted'] and missing['blocked'] == 'l2_effect'
    assert missing['levels']['l2_effect']['pass'] is None   # not run, not pass


def test_proposal_must_compile_and_declare_a_direction():
    m = _model()
    ctx = TE.build_context(m, [], [], builtins())
    good = TE.ToolProposal(kind='new_tool', name='t', description='d',
                           source=GOOD_TOOL, input_schema={},
                           address={'model_id': m.model_id(),
                                    'field': 'w_max_mm', 'prediction': 'up'})
    assert good.validate() == []
    bad = TE.ToolProposal(kind='new_tool', name='t2', description='d',
                          source=BAD_IMPORT, input_schema={},
                          address={'model_id': m.model_id(),
                                   'field': 'w_max_mm', 'prediction': 'up'})
    assert any('static' in e for e in bad.validate())
    no_addr = TE.ToolProposal(kind='skill_update', name='t3', description='d',
                              source='', skill_text='do better',
                              input_schema={}, address={})
    assert any('model_id' in e for e in no_addr.validate())


def test_rule_propose_always_compiles_and_addresses_the_model():
    m = _model()
    ctx = TE.build_context(m, [], [], builtins())
    p = TE.rule_propose(ctx)
    assert p.validate() == []
    assert p.address['model_id'] == m.model_id()
    assert p.address['prediction'] in ('up', 'down')
