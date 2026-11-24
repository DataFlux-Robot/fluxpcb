"""Minimal MCP server (stdio JSON-RPC): expose the tool registry.

Any MCP client can then call the same tools the meta loop calls, through
`initialize / tools/list / tools/call`.  The server never touches secrets;
it only dispatches to fluxpcb.tools.call.
"""
from __future__ import annotations

import json
import sys

from .call import call as _call
from .registry import ToolRegistry


def serve(registry: ToolRegistry, stdin=None, stdout=None) -> None:
    """Read JSON-RPC lines from stdin, answer on stdout, until EOF.

    The standard MCP lifecycle is reduced to its minimal working form:
    initialize (protocol negotiation), tools/list, tools/call, and
    notifications (drop).  Enough for any compliant client to route.
    """
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout

    def send(payload: dict):
        stdout.write(json.dumps(payload) + '\n')
        stdout.flush()

    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        mid = msg.get('id')
        method = msg.get('method')
        params = msg.get('params') or {}
        if mid is None:
            continue                     # notification
        if method == 'initialize':
            send({'jsonrpc': '2.0', 'id': mid, 'result': {
                'protocolVersion': params.get('protocolVersion', '0'),
                'capabilities': {'tools': {'listChanged': False}},
                'serverInfo': {'name': 'fluxpcb-tools',
                               'version': registry.digest()[:8]}}})
        elif method == 'tools/list':
            send({'jsonrpc': '2.0', 'id': mid,
                  'result': {'tools': registry.mcp_tools()}})
        elif method == 'tools/call':
            name = (params.get('name') or '')
            spec = registry.get(name)
            if spec is None:
                send({'jsonrpc': '2.0', 'id': mid,
                      'error': {'code': -32602,
                                'message': f'unknown tool {name!r}'}})
                continue
            out = _call(spec, params.get('arguments') or {})
            send({'jsonrpc': '2.0', 'id': mid, 'result': {
                'content': [{'type': 'text',
                             'text': json.dumps(out, default=str)}],
                'isError': not out.get('ok', False)}})
        else:
            send({'jsonrpc': '2.0', 'id': mid,
                  'error': {'code': -32601, 'message': f'unknown {method}'}})


def main() -> None:
    from .builtins import builtins as _default
    serve(_default())


if __name__ == '__main__':
    main()
