"""The working principles every LLM call in this package is given.

They are prepended in one place (``fluxpcb.llm.LLMAdapter.complete``), so
a new call site cannot forget them.  The centre of gravity is one rule
taken from the reference project's operating manual:

    不确定就说不确定，把它列为需要补充的信息。

An agent that invents a value and then reasons from it produces a
confident wrong answer that nobody can trace; an agent that lists the
missing datasheet page produces a question that a person can answer in a
minute.  The same discipline is enforced in code: ``findings.Finding``
cannot carry a measurement it does not have, and a verifier that cannot
decide returns ``unknown`` with its ``needed_information``.
"""
from __future__ import annotations

PRINCIPLES = """工作原则（每一次回答都适用）:
1. 不确定就说不确定。把它写成“需要补充的信息”并说明要什么（数据手册哪一页、
   哪个库文件、哪个引脚号），不要猜一个值再基于它下结论。
2. 每个数字都要能被复查：说明它来自哪个文件、哪条命令、哪次测量；没有来源的
   数字一律写成“未测量”，不要写 0。
3. 区分确认与估计。估计必须标明“估计”，并说明它依赖的假设；估计不能当作结论
   去否决一个已验证的结果。
4. 结论带规则身份：能引用规则编号/门禁名就引用，便于别人对照手册。
5. 只答被问到的，不扩写。给出结论、依据、缺口三部分即可。
"""

# a compact form for prompts with tight budgets
PRINCIPLES_SHORT = (
    "不确定就说不确定，列为“需要补充的信息”；没有来源的数字写“未测量”；"
    "估计必须标注假设，不当结论用；结论引用规则编号。"
)


def with_principles(system: str, short: bool = False) -> str:
    """`system` with the principles in front, unless they are already there."""
    body = PRINCIPLES_SHORT if short else PRINCIPLES
    if system and system.startswith(body[:24]):
        return system
    if not system:
        return body
    return body + '\n' + system
