"""Âncoras: criação a partir de seleções e re-ancoragem entre versões (n → n+1)."""

from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher

from .diffing import line_opcodes
from .models import Anchor
from .textmap import find_phrase, line_end, line_starts, to_linecol, to_offset

CONTEXT_CHARS = 64


def anchor_from_offsets(text: str, file: str, version: int, start: int, end: int, page: int | None = None) -> Anchor:
    start = max(0, min(start, len(text)))
    end = max(start, min(end, len(text)))
    while end > start and text[end - 1] == "\n":
        end -= 1
    starts = line_starts(text)
    sl, sc = to_linecol(text, start, starts)
    el, ec = to_linecol(text, end, starts)
    return Anchor(
        file=file,
        version=version,
        start_line=sl,
        start_col=sc,
        end_line=el,
        end_col=ec,
        quote=text[start:end],
        prefix=text[max(0, start - CONTEXT_CHARS) : start],
        suffix=text[end : end + CONTEXT_CHARS],
        page=page,
    )


def make_anchor(text: str, file: str, version: int, start_line: int, start_col: int, end_line: int,
                end_col: int, page: int | None = None) -> Anchor:
    starts = line_starts(text)
    s = to_offset(text, start_line, start_col, starts)
    e = to_offset(text, end_line, end_col, starts)
    if e < s:
        s, e = e, s
    return anchor_from_offsets(text, file, version, s, e, page)


def lines_anchor(text: str, file: str, version: int, first: int, last: int, page: int | None = None) -> Anchor:
    """Âncora cobrindo linhas inteiras."""
    first, last = sorted((first, last))
    starts = line_starts(text)
    first = min(max(first, 1), len(starts))
    last = min(max(last, 1), len(starts))
    return anchor_from_offsets(text, file, version, starts[first - 1], line_end(text, last, starts), page)


def anchor_offsets(text: str, anchor: Anchor) -> tuple[int, int]:
    starts = line_starts(text)
    return (
        to_offset(text, anchor.start_line, anchor.start_col, starts),
        to_offset(text, anchor.end_line, anchor.end_col, starts),
    )


def _context_score(text: str, pos: int, anchor: Anchor) -> float:
    end = pos + len(anchor.quote)
    before = text[max(0, pos - len(anchor.prefix)) : pos]
    after = text[end : end + len(anchor.suffix)]
    score = 0.0
    if anchor.prefix:
        score += SequenceMatcher(None, before, anchor.prefix, autojunk=False).ratio()
    if anchor.suffix:
        score += SequenceMatcher(None, after, anchor.suffix, autojunk=False).ratio()
    return score


def locate_quote(text: str, anchor: Anchor, hint: int | None = None,
                 window: tuple[int, int] | None = None) -> int | None:
    """Posição da citação exata; com várias ocorrências, desempata por prefixo/sufixo e distância."""
    quote = anchor.quote
    if not quote:
        return None
    lo, hi = window if window else (0, len(text))
    positions: list[int] = []
    pos = text.find(quote, lo)
    while pos >= 0 and pos + len(quote) <= hi and len(positions) < 200:
        positions.append(pos)
        pos = text.find(quote, pos + 1)
    if not positions:
        return None
    if len(positions) == 1:
        return positions[0]
    return min(positions, key=lambda p: (-round(_context_score(text, p, anchor), 3),
                                         abs(p - hint) if hint is not None else 0))


@dataclass
class Reanchored:
    anchor: Anchor
    state: str  # inalterado | alterado | movido | orfao


def _map_line(opcodes, line: int) -> tuple[int, bool]:
    """Linha antiga (1-based) → (linha nova, inalterada?)."""
    i = line - 1
    for tag, i1, i2, j1, j2 in opcodes:
        if tag == "insert" or not i1 <= i < i2:
            continue
        if tag == "equal":
            return j1 + (i - i1) + 1, True
        if tag == "replace":
            return min(j1 + (i - i1), j2 - 1) + 1, False
        return j1 + 1, False  # delete: primeira linha que sobreviveu depois do trecho
    return (opcodes[-1][4] if opcodes else 1), False


def _changed_region(opcodes, first: int, last: int) -> tuple[int, int] | None:
    """Linhas novas (1-based, inclusivas) dos blocos alterados que tocam as linhas antigas first..last."""
    lo = hi = None
    for tag, i1, i2, j1, j2 in opcodes:
        if tag == "equal":
            continue
        if tag == "insert":
            if not first - 1 < i1 < last:
                continue
        elif i2 <= first - 1 or i1 >= last:
            continue
        lo = j1 if lo is None else min(lo, j1)
        hi = j2 if hi is None else max(hi, j2)
    if lo is None:
        return None
    return lo + 1, max(lo + 1, hi)


def reanchor(anchor: Anchor, old_texts: dict[str, str], new_texts: dict[str, str], new_version: int) -> Reanchored:
    """Leva uma âncora da versão n para a n+1."""
    has_quote = bool(anchor.quote.strip())
    new = new_texts.get(anchor.file)
    old = old_texts.get(anchor.file)

    def at(text: str, path: str, pos: int, state: str) -> Reanchored:
        return Reanchored(anchor_from_offsets(text, path, new_version, pos, pos + len(anchor.quote), anchor.page), state)

    if new is None:
        if has_quote:
            for path, text in new_texts.items():
                pos = locate_quote(text, anchor)
                if pos is not None:
                    return at(text, path, pos, "movido")
        return Reanchored(anchor, "orfao")

    if old == new:
        return Reanchored(anchor.with_version(new_version), "inalterado")
    if old is None:
        pos = locate_quote(new, anchor) if has_quote else None
        return at(new, anchor.file, pos, "inalterado") if pos is not None else Reanchored(anchor, "orfao")

    ops = line_opcodes(old, new)
    starts = line_starts(new)
    total = len(starts)
    first, same = _map_line(ops, anchor.start_line)
    first = min(max(first, 1), total)
    last = min(max(_map_line(ops, anchor.end_line)[0], first), total)
    hint = to_offset(new, first, anchor.start_col if same else 0, starts)

    if has_quote:
        near = (starts[max(0, first - 4)], line_end(new, min(total, last + 3), starts))
        pos = locate_quote(new, anchor, hint=hint, window=near)
        if pos is not None:
            return at(new, anchor.file, pos, "inalterado")
        pos = locate_quote(new, anchor, hint=hint)
        if pos is not None:
            return at(new, anchor.file, pos, "movido")
    elif same:
        end_line, end_same = _map_line(ops, anchor.end_line)
        if end_same:
            s = to_offset(new, first, anchor.start_col, starts)
            e = to_offset(new, end_line, anchor.end_col, starts)
            return Reanchored(anchor_from_offsets(new, anchor.file, new_version, s, e, anchor.page), "inalterado")

    region = _changed_region(ops, anchor.start_line, anchor.end_line) or (first, last)
    r1 = min(max(region[0], 1), total)
    r2 = min(max(region[1], r1), total)
    if has_quote:
        lo = starts[max(0, r1 - 2)]
        hi = line_end(new, min(total, r2 + 1), starts)
        found = find_phrase(new, anchor.quote, start=lo, end=hi, max_results=1, min_score=0.5)
        if found:
            m = found[0]
            return Reanchored(anchor_from_offsets(new, anchor.file, new_version, m.start, m.end, anchor.page), "alterado")
    return Reanchored(lines_anchor(new, anchor.file, new_version, r1, r2, anchor.page), "alterado")
