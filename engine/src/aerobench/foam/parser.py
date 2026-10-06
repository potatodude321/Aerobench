"""Tolerant OpenFOAM dictionary parser.

Supports comments, quoted strings, words with balanced parentheses (``div(phi,U)``),
numbers, lists, dimension sets, sub-dictionaries, regex keys, ``#include*`` and other
directives, ``$macro`` references (kept as words) and verbatim ``#{ ... #}`` code
blocks (kept as :class:`Raw`).  Field data in ``nonuniform List<...> N(...)`` form is
parsed as tokens.  The parser never evaluates macros or includes — it preserves them.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from .ast import Dict, Dimensions, Directive, Entry, List, Raw, Str, Tokens, Word


class FoamParseError(ValueError):
    def __init__(self, msg: str, line: int):
        super().__init__(f"line {line}: {msg}")
        self.line = line


@dataclass
class Tok:
    kind: str  # 'punct' | 'word' | 'string' | 'number' | 'raw' | 'directive'
    value: Any
    line: int
    start: int = 0
    end: int = 0


PUNCT = set("{}()[];")


def tokenize(text: str) -> list[Tok]:
    toks: list[Tok] = []
    i, n, line = 0, len(text), 1
    while i < n:
        c = text[i]
        if c == "\n":
            line += 1
            i += 1
            continue
        if c.isspace():
            i += 1
            continue
        if text.startswith("//", i):
            j = text.find("\n", i)
            i = n if j < 0 else j
            continue
        if text.startswith("/*", i):
            j = text.find("*/", i + 2)
            if j < 0:
                raise FoamParseError("unterminated block comment", line)
            line += text.count("\n", i, j)
            i = j + 2
            continue
        if text.startswith("#{", i):
            j = text.find("#}", i + 2)
            if j < 0:
                raise FoamParseError("unterminated #{ code block", line)
            toks.append(Tok("raw", text[i : j + 2], line, i, j + 2))
            line += text.count("\n", i, j)
            i = j + 2
            continue
        if c == "$" and i + 1 < n and text[i + 1] == "{":
            j = _match_brace(text, i + 1)
            if j < 0:
                raise FoamParseError("unterminated ${...}", line)
            toks.append(Tok("word", text[i : j + 1], line, i, j + 1))
            line += text.count("\n", i, j)
            i = j + 1
            continue
        if c == '"':
            j = i + 1
            buf = []
            while j < n and text[j] != '"':
                if text[j] == "\\" and j + 1 < n:
                    buf.append(text[j + 1])
                    j += 2
                    continue
                if text[j] == "\n":
                    line += 1
                buf.append(text[j])
                j += 1
            if j >= n:
                raise FoamParseError("unterminated string", line)
            toks.append(Tok("string", "".join(buf), line, i, j + 1))
            i = j + 1
            continue
        if c in PUNCT:
            toks.append(Tok("punct", c, line, i, i + 1))
            i += 1
            continue
        # word / number / directive: read until whitespace or punctuation, allowing balanced ()
        j = i
        depth = 0
        while j < n:
            ch = text[j]
            if ch.isspace() or ch in '{};[]"':
                break
            if ch == "(":
                if j == i:
                    break
                depth += 1
            elif ch == ")":
                if depth == 0:
                    break
                depth -= 1
            elif ch == "/" and (text.startswith("//", j) or text.startswith("/*", j)):
                break
            j += 1
        word = text[i:j]
        if not word:
            raise FoamParseError(f"unexpected character {c!r}", line)
        if word.startswith("#"):
            if j < n and text[j] == "{":
                # inline expression such as #eval{ ... } / #calc{ ... }: keep verbatim
                k = _match_brace(text, j)
                if k < 0:
                    raise FoamParseError(f"unterminated {word}{{...}}", line)
                toks.append(Tok("raw", text[i : k + 1], line, i, k + 1))
                line += text.count("\n", i, k)
                i = k + 1
                continue
            toks.append(Tok("directive", word, line, i, j))
        else:
            num = _number(word)
            if num is not None:
                toks.append(Tok("number", num, line, i, j))
            else:
                toks.append(Tok("word", word, line, i, j))
        i = j
    return toks


def _match_brace(text: str, open_idx: int) -> int:
    depth = 0
    for k in range(open_idx, len(text)):
        if text[k] == "{":
            depth += 1
        elif text[k] == "}":
            depth -= 1
            if depth == 0:
                return k
    return -1


def _number(s: str) -> Optional[float | int]:
    try:
        if s.lstrip("+-").isdigit():
            return int(s)
        if s.lower() in ("nan", "inf", "-inf", "+inf", "infinity"):
            return None
        return float(s)
    except ValueError:
        return None


class _Parser:
    def __init__(self, toks: list[Tok], text: str = ""):
        self.t = toks
        self.i = 0
        self.text = text

    def peek(self, k: int = 0) -> Optional[Tok]:
        j = self.i + k
        return self.t[j] if j < len(self.t) else None

    def next(self) -> Tok:
        tok = self.peek()
        if tok is None:
            last = self.t[-1].line if self.t else 0
            raise FoamParseError("unexpected end of input", last)
        self.i += 1
        return tok

    def expect(self, punct: str) -> Tok:
        tok = self.next()
        if tok.kind != "punct" or tok.value != punct:
            raise FoamParseError(f"expected '{punct}', found {tok.value!r}", tok.line)
        return tok

    # dictionary body until '}' (if nested) or EOF
    def dict_body(self, nested: bool) -> Dict:
        d = Dict()
        while True:
            tok = self.peek()
            if tok is None:
                if nested:
                    raise FoamParseError("missing '}'", self.t[-1].line if self.t else 0)
                return d
            if tok.kind == "punct" and tok.value == "}":
                if not nested:
                    raise FoamParseError("unexpected '}'", tok.line)
                self.next()
                return d
            if tok.kind == "punct" and tok.value == ";":
                self.next()  # stray semicolon
                continue
            if tok.kind == "directive":
                self.next()
                arg = None
                nxt = self.peek()
                if tok.value in ("#include", "#includeEtc", "#includeIfPresent", "#includeFunc", "#includeModel", "#remove", "#sinclude"):
                    if nxt is not None and nxt.kind in ("string", "word"):
                        self.next()
                        arg = Str(nxt.value) if nxt.kind == "string" else Word(nxt.value)
                    elif nxt is not None and nxt.kind == "punct" and nxt.value == "(":
                        self.next()
                        arg = self.list_items(")")
                elif nxt is not None and nxt.kind == "raw":
                    self.next()
                    arg = Raw(nxt.value)
                d.items.append(Directive(tok.value, arg))
                continue
            if tok.kind not in ("word", "string", "number"):
                raise FoamParseError(f"expected a keyword, found {tok.value!r}", tok.line)
            self.next()
            key = tok.value if tok.kind != "string" else f'"{tok.value}"'
            key = str(key)
            nxt = self.peek()
            if tok.kind == "word" and key.startswith("$"):
                # Macro expansion used as an entry ($p; / ${..} / { $p_GAMG }): takes no value
                if nxt is not None and nxt.kind == "punct" and nxt.value == ";":
                    self.next()
                d.items.append(Entry(key, Tokens([])))
                continue
            if nxt is not None and nxt.kind == "punct" and nxt.value == "{":
                self.next()
                d.items.append(Entry(key, self.dict_body(nested=True)))
                continue
            mark = self.i
            try:
                value = self.entry_value()
            except FoamParseError:
                if not self.text:
                    raise
                self.i = mark
                value = self.raw_until_semicolon()
            d.items.append(Entry(key, value))

    def raw_until_semicolon(self) -> Raw:
        """Capture the verbatim source of an entry value we cannot structure."""
        depth = 0
        start_tok = self.peek()
        if start_tok is None:
            raise FoamParseError("unexpected end of input", self.t[-1].line)
        while True:
            tok = self.next()
            if tok.kind == "punct" and tok.value in "({[":
                depth += 1
            elif tok.kind == "punct" and tok.value in ")}]":
                depth -= 1
                if depth < 0:
                    raise FoamParseError("unbalanced brackets", tok.line)
            elif tok.kind == "punct" and tok.value == ";" and depth == 0:
                return Raw(self.text[start_tok.start : tok.start].rstrip() + ";")

    def entry_value(self) -> Any:
        items: list[Any] = []
        while True:
            tok = self.peek()
            if tok is None:
                raise FoamParseError("missing ';'", self.t[-1].line)
            if tok.kind == "punct" and tok.value == ";":
                self.next()
                break
            if tok.kind == "punct" and tok.value == "}":
                raise FoamParseError("missing ';' before '}'", tok.line)
            if tok.kind == "raw":
                # e.g.  code #{ ... #};
                self.next()
                items.append(Raw(tok.value))
                nxt = self.peek()
                if nxt is not None and nxt.kind == "punct" and nxt.value == ";":
                    self.next()
                break
            items.append(self.value_token())
        if len(items) == 1:
            return items[0]
        return Tokens(items)

    def value_token(self) -> Any:
        tok = self.next()
        if tok.kind == "number":
            return tok.value
        if tok.kind == "word":
            if tok.value in ("true", "yes", "on"):
                return Word(tok.value)  # preserve spelling
            return Word(tok.value)
        if tok.kind == "string":
            return Str(tok.value)
        if tok.kind == "directive":
            return Word(tok.value)
        if tok.kind == "punct":
            if tok.value == "(":
                return self.list_items(")")
            if tok.value == "[":
                vals = []
                while True:
                    t2 = self.next()
                    if t2.kind == "punct" and t2.value == "]":
                        break
                    if t2.kind != "number":
                        # named dimensions e.g. [m/s] — keep raw
                        vals.append(t2.value)
                    else:
                        vals.append(t2.value)
                if all(isinstance(v, (int, float)) for v in vals) and len(vals) in (5, 7):
                    return Dimensions(tuple(vals))
                return Raw("[" + " ".join(str(v) for v in vals) + "]")
            if tok.value == "{":
                return self.dict_body(nested=True)
        raise FoamParseError(f"unexpected token {tok.value!r}", tok.line)

    def list_items(self, close: str) -> List:
        items: list[Any] = []
        while True:
            tok = self.peek()
            if tok is None:
                raise FoamParseError(f"missing '{close}'", self.t[-1].line)
            if tok.kind == "punct" and tok.value == close:
                self.next()
                return List(items)
            if tok.kind == "punct" and tok.value == ";":
                self.next()  # tolerate ';' inside lists of entries
                continue
            items.append(self.value_token())


def parse(text: str) -> Dict:
    """Parse a dictionary file/body.  The ``FoamFile`` header (if present) is kept as an entry.

    Entry values that cannot be structured (exotic syntax) are preserved verbatim as
    :class:`Raw` so that nothing is lost.
    """
    return _Parser(tokenize(text), text).dict_body(nested=False)


def parse_value(text: str) -> Any:
    """Parse a single entry value (without trailing ';')."""
    p = _Parser(tokenize(text + ";"))
    return p.entry_value()


def strip_header(d: Dict) -> tuple[Optional[Dict], Dict]:
    """Split off the FoamFile header."""
    header = d.get("FoamFile")
    body = Dict()
    body.items = [it for it in d.items if not (isinstance(it, Entry) and it.key == "FoamFile")]
    return header, body
