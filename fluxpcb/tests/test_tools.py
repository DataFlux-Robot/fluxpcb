"""Tool layer tests: registry, inline call, MCP round-trip, sandbox gate."""
import io
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from fluxpcb.tools import call as tcall  # noqa: E402
from fluxpcb.tools.builtins import builtins  # noqa: E402
from fluxpcb.tools.mcp_server import serve  # noqa: E402
from fluxpcb.tools.registry import ToolRegistry, ToolSpec  # noqa: E402


def test_registry_has_the_nine_primitives():
    reg = builtins()
    names = set(reg.names())
    assert {'geomir_plan', 'fanout_calc', 'bga_escape_plan', 'maze_route',
            'explore_route', 'eikonal_route', 'ncer_board',
            'freerouting_board', 'pathfinder_board'} <= names
    assert reg.get('freerouting_board').cost_band == 'heavy'
    assert reg.get('geomir_plan').fn


def test_registry_rejects_bad_specs():
    reg = ToolRegistry()
    with pytest.raises(ValueError):
        reg.register(ToolSpec(name='x', description='', category='route',
                              input_schema={}))
    with pytest.raises(ValueError):
        reg.register(ToolSpec(name='x', description='d', category='magic',
                              input_schema={}))
    with pytest.raises(ValueError):
        reg.register(ToolSpec(name='x', description='d', category='route',
                              input_schema={}, source='library', driver=''))
    reg.register(ToolSpec(name='x', description='d', category='route',
                          input_schema={}))
    with pytest.raises(ValueError):
        reg.register(ToolSpec(name='x', description='d', category='route',
                              input_schema={}))
    assert reg.digest() and len(reg.digest()) == 16


def test_inline_call_runs_geomir_and_reports_blockers():
    reg = builtins()
    segs = [[7, (9.5, 4.0), (9.5, 6.0), 0.1]]
    out = tcall.call(reg.get('geomir_plan'), {
        'ax': 0.0, 'ay': 5.0, 'bx': 8.0, 'by': 5.0, 'width': 0.2, 'nc': 5,
        'segs': segs, 'rects': []})
    assert out['ok'] and out['tool'] == 'geomir_plan'
    assert any(p['pts'] for p in out['result']['plans'])


def test_inline_call_wraps_exceptions_as_errors():
    reg = builtins()
    out = tcall.call(reg.get('geomir_plan'), {})      # missing args
    assert not out['ok'] and out['error']


def test_subprocess_call_needs_driver_and_board():
    spec = ToolSpec(name='t', description='d', category='route',
                    input_schema={}, source='builtin', driver='nope.py')
    out = tcall.call(spec, {}, ctx={'board': 'missing'})
    assert not out['ok'] and ('driver missing' in out['error']
                              or 'board missing' in out['error'])


def _roundtrip(reg, method, params, mid=1):
    stdin = io.StringIO(json.dumps({'jsonrpc': '2.0', 'id': mid,
                                    'method': method,
                                    'params': params}) + '\n')
    out = io.StringIO()
    serve(reg, stdin=stdin, stdout=out)
    return json.loads(out.getvalue())


def test_mcp_roundtrip_initialize_list_and_call():
    reg = builtins()
    init = _roundtrip(reg, 'initialize', {})
    assert init['result']['capabilities']['tools']
    listed = _roundtrip(reg, 'tools/list', {})
    names = {t['name'] for t in listed['result']['tools']}
    assert 'geomir_plan' in names
    called = _roundtrip(reg, 'tools/call', {
        'name': 'geomir_plan',
        'arguments': {'ax': 0, 'ay': 0, 'bx': 5, 'by': 0, 'width': 0.2,
                      'nc': 1, 'segs': [], 'rects': []}})
    assert 'isError' in called['result']
    assert not called['result']['isError']
    unknown = _roundtrip(reg, 'tools/call', {'name': 'nope'})
    assert unknown['error']['code'] == -32602
    bad = _roundtrip(reg, 'not/a/method', {})
    assert bad['error']['code'] == -32601
