# -*- coding: utf-8 -*-
"""Board State Kernel — lessons become invariants (the lint).

The K7-K20 record showed the same disease families returning in new
costumes: prose KNOWHOW entries documented each incident but nothing
ENFORCED the generalized rule, so the next round paid the diagnosis
cost again. Kernel discipline: from K21 on, every knowhow entry must
either (a) name the invariant/gate/test it generalized into, or
(b) declare itself an isolated case. This lint makes the contract
checkable in CI.
"""
from __future__ import annotations

import re
from pathlib import Path


def lint_knowhow(path: Path, first_kernel_round: int = 21) -> list:
    """-> [violation strings] for entries >= first_kernel_round missing
    a 泛化为 line (or an explicit 个案 declaration)."""
    txt = path.read_text(encoding="utf-8", errors="replace")
    bad = []
    for m in re.finditer(r"^## (K(\d+)) .*$", txt, re.M):
        heading, knum = m.group(1), int(m.group(2))
        if knum < first_kernel_round:
            continue
        # section extends to the next heading or EOF
        start = m.end()
        nxt = re.search(r"^## ", txt[start:], re.M)
        body = txt[start:start + (nxt.start() if nxt else len(txt))]
        if "泛化为" not in body and "个案" not in body:
            bad.append(f"{heading}: 缺少'泛化为:'标注（或显式'个案'）")
        if knum >= 22 and "契约" not in body and "个案" not in body:
            bad.append(f"{heading}: 缺少'契约:'声明(读什么/写什么/语义前提)")
    return bad
