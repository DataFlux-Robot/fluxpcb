"""The clearance-degradation counter is shared between the loop and auto_route.

`_closed_loop` kept it as a local, `auto_route` read it — every route raised
NameError at the report line (the H743 run never reached it).  This pins the
shared list so the scoping cannot regress silently.
"""
from __future__ import annotations

import inspect

from fluxpcb import routing


def test_counter_is_module_level_and_shared():
    assert isinstance(routing._CLEAR_FB, list)
    loop = inspect.getsource(routing._closed_loop)
    assert '_clear_fb = _CLEAR_FB' in loop
    auto = inspect.getsource(routing.auto_route)
    assert 'if _CLEAR_FB:' in auto and 'len(_CLEAR_FB)' in auto
