"""Minimal S-expression writer/serializer for KiCad 10 file formats.

KiCad files are S-expressions: (symbol key value (child ...) "string")
This module provides deterministic serialization with 4-space-free
KiCad-style indentation (2 spaces per level, no indentation for atoms).
"""
from __future__ import annotations

from typing import Any


def _escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def dump(node: Any) -> str:
    """Serialize an S-expression node to a KiCad-style string."""
    return _dump(node, 0)


def _dump(node: Any, depth: int) -> str:
    pad = "  " * depth
    if isinstance(node, Symbol):
        return f"{pad}{node.name}"
    if isinstance(node, str):
        return f'"{_escape(node)}"'
    if isinstance(node, bool):
        return "yes" if node else "no"
    if isinstance(node, (int, float)):
        return str(node)
    if isinstance(node, (list, tuple)):
        if not node:
            return f"{pad}()"
        head = node[0]
        head_s = head.name if isinstance(head, Symbol) else _dump(head, 0)
        children = []
        for c in node[1:]:
            s = _dump(c, depth + 1) if isinstance(c, (list, tuple)) or (
                isinstance(c, Symbol)) else _dump(c, 0)
            children.append(s)
        inner = "\n".join(children)
        if "\n" in inner or len(inner) > 60:
            return f"{pad}({head_s}\n{inner}\n{pad})"
        return f"{pad}({head_s} {inner.strip()})".replace("()", "( )") \
            if inner else f"{pad}({head_s})"
    raise TypeError(f"cannot serialize {type(node)}")


class Symbol(str):
    """An unquoted symbolic atom."""
    def __new__(cls, s):
        o = super().__new__(cls, s)
        return o


def sym(name: str) -> Symbol:
    return Symbol(name)


def atom(v) -> Any:
    """Wrap a value as a bare atom (number/keyword)."""
    if isinstance(v, (int, float, bool, Symbol)):
        return v
    return sym(str(v))


def sexpr(*items) -> list:
    """Build an S-expression node: first item becomes the head."""
    return list(items)
