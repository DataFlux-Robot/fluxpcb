#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""meta — Meta-development loops (Components 1-2).

Component 1: MetaDevLoop (diagnose→evolve→select)
Component 2: MetaMetaLoop (harness self-improvement)

The loops are the EXPERIMENTER, not the system under test.  A board run
imports fluxpcb.meta.arm and fluxpcb.meta.policy; importing this package
must not load the loops that analyse the run's results, or every cell
executes (and is fingerprinted with) code that cannot change its outcome.
That was the case until the loops were made lazy: a campaign could not
edit a proposer or a memory module without staling every cell.

The names still work — `from fluxpcb.meta import MetaDevLoop` — through
the attribute hook (PEP 562); only the import cost moves to first use.
The hook uses plain import statements rather than import_module on a
variable: the code fingerprint resolves imports from the AST, and a
non-literal module name there would force it back to hashing everything.
"""
__all__ = ['MetaDevLoop', 'StrategyStore', 'MetaMetaLoop']


def __getattr__(name):
    if name == 'MetaDevLoop':
        from .devloop import MetaDevLoop
        return MetaDevLoop
    if name == 'StrategyStore':
        from .strategy_store import StrategyStore
        return StrategyStore
    if name == 'MetaMetaLoop':
        from .meta_loop import MetaMetaLoop
        return MetaMetaLoop
    raise AttributeError(f'module {__name__!r} has no attribute {name!r}')


def __dir__():
    return sorted(set(globals()) | set(__all__))
