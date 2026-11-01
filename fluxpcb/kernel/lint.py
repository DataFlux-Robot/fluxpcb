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


# 测量/变更核心: 静默 except 在这里是"信息黑洞"(记录丢失/测量降级
# 静默发生) — 硬失败; 其余模块先可见后治理(债务清单化)
CORE_SILENT_EXCEPT = [
    "fluxpcb/kernel/state.py", "fluxpcb/kernel/txn.py",
    "fluxpcb/kernel/decision.py", "fluxpcb/kernel/intel.py",
    "fluxpcb/kernel/causal.py", "fluxpcb/kernel/truthnet.py",
    "fluxpcb/kernel/invariants.py", "fluxpcb/kernel/rules.py",
    "fluxpcb/kicadgen/geomir.py", "fluxpcb/kicadgen/fab_waiver.py",
    "fluxpcb/kicadgen/gap_nudge.py", "fluxpcb/kicadgen/direct_link.py",
    "fluxpcb/kicadgen/place_opt.py", "fluxpcb/kicadgen/commit_gate.py",
]


def lint_silent_excepts(py_path: Path) -> list:
    """-> [violation strings] for silent except blocks.

    静默 = except 块体只有 pass/continue 且无 '# ok-silent:' 理由标注
    (_json 教训第 2 例: 能失败的 except 不许沉默 — 但有正当理由的
    清理/失效安全路径必须显式声明理由, 不许裸 pass)。"""
    bad = []
    lines = py_path.read_text(encoding="utf-8",
                              errors="replace").splitlines()
    for i, ln in enumerate(lines):
        stripped = ln.lstrip()
        if not (stripped.startswith("except ")
                or stripped.startswith("except:")):
            continue
        indent = len(ln) - len(stripped)
        if "# ok-silent:" in ln:
            continue
        # block body: following lines with deeper indent (one-liner
        # 'except X: pass' counts as body on the same line)
        body = []
        after = ln.split(":", 1)[1] if ":" in ln else ""
        if after.strip():
            body.append(after.strip())
        for j in range(i + 1, len(lines)):
            cur = lines[j]
            if not cur.strip():
                continue
            cur_indent = len(cur) - len(cur.lstrip())
            if cur_indent <= indent:
                break
            body.append(cur.strip())
        stmts = [b for b in body
                 if b and not b.startswith("#")]
        if stmts and all(b in ("pass", "continue") for b in stmts):
            if not any("# ok-silent:" in b for b in body):
                bad.append(
                    f"{py_path.name}:{i + 1}: 静默except(体={stmts}) "
                    f"— 可见化或标注 '# ok-silent: 理由'")
    return bad
