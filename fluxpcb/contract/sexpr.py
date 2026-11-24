"""sexpr — span-preserving S-expression scanning.

We do not parse-and-re-serialise a whole DSN: reformatting a 40 KB file
invites diff noise and parser surprises.  We locate nodes by character
offset and splice only the rule-carrying spans, then re-parse the result
to prove the edit (see ``dsn_patch``).
"""
from __future__ import annotations


def tokenize(text: str):
    """Split ``text`` into (kind, value, start, end) tokens.

    kind is one of 'open', 'close', 'atom', 'string'.  Quoted strings keep
    their escapes in ``value`` but their quotes are stripped.  Positions
    index the original text exactly.
    """
    toks = []
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c in ' \t\r\n':
            i += 1
            continue
        if c == '(':
            toks.append(('open', '(', i, i + 1))
            i += 1
            continue
        if c == ')':
            toks.append(('close', ')', i, i + 1))
            i += 1
            continue
        if c == '"':
            # Specctra allows a lone quote as a token value:
            #   (string_quote ")      <- the value is the quote character
            # A naive scanner treats it as an unterminated string and eats
            # the rest of the file; recognise the one-character form.
            nxt = text[i + 1] if i + 1 < n else ''
            if nxt in (')', ' ', '\t', '\r', '\n', ''):
                toks.append(('string', '"', i, i + 1))
                i += 1
                continue
            j = i + 1
            buf = []
            while j < n:
                if text[j] == '\\' and j + 1 < n:
                    buf.append(text[j:j + 2])
                    j += 2
                    continue
                if text[j] == '"':
                    j += 1
                    break
                buf.append(text[j])
                j += 1
            toks.append(('string', ''.join(buf), i, j))
            i = j
            continue
        j = i
        while j < n and text[j] not in ' \t\r\n()"':
            j += 1
        toks.append(('atom', text[i:j], i, j))
        i = j
    return toks


def match_pairs(toks):
    """Map each open token index to its close index and vice versa."""
    stack = []
    match = {}
    for i, t in enumerate(toks):
        if t[0] == 'open':
            stack.append(i)
        elif t[0] == 'close':
            if not stack:
                raise ValueError(f'unbalanced ")" at offset {t[2]}')
            j = stack.pop()
            match[j] = i
            match[i] = j
    if stack:
        raise ValueError(f'unbalanced "(" at offset {toks[stack[-1]][2]}')
    return match


def node_name(toks, i):
    """The atom immediately after the open token ``i`` ('' if none)."""
    if i + 1 < len(toks) and toks[i + 1][0] in ('atom', 'string'):
        return toks[i + 1][1]
    return ''


def direct_children(toks, match, i):
    """Indices of the direct child nodes of the node opened at ``i``."""
    out = []
    j = i + 1
    end = match[i]
    while j < end:
        t = toks[j]
        if t[0] == 'open':
            out.append(j)
            j = match[j] + 1
        else:
            j += 1
    return out


def find_node(toks, name, root=None, match=None):
    """First open-token index whose node name is ``name``.

    Searches direct children of ``root`` when given (root is an open-token
    index); otherwise scans the whole token stream.
    """
    if root is not None:
        if match is None:
            match = match_pairs(toks)
        for c in direct_children(toks, match, root):
            if node_name(toks, c) == name:
                return c
        return None
    for i, t in enumerate(toks):
        if t[0] == 'open' and i + 1 < len(toks) and toks[i + 1][1] == name:
            return i
    return None


def span(toks, match, i):
    """Character span (start, end) of the node opened at ``i``."""
    return toks[i][2], toks[match[i]][3]


def unquote(value: str) -> str:
    """Undo DSN backslash escapes inside a quoted atom."""
    if '\\' not in value:
        return value
    out = []
    k = 0
    while k < len(value):
        if value[k] == '\\' and k + 1 < len(value):
            out.append(value[k + 1])
            k += 2
        else:
            out.append(value[k])
            k += 1
    return ''.join(out)
