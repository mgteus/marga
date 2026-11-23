"""Texto: offsets ↔ (linha, coluna), normalização de LaTeX e busca de frases (exata e aproximada).

Convenções: linhas começam em 1, colunas em 0 e contam code points (``str`` do Python).
O texto é dividido com ``text.split("\\n")``.
"""

from __future__ import annotations

import bisect
import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from functools import lru_cache

# ----------------------------------------------------------------- linhas


def line_starts(text: str) -> list[int]:
    return [0] + [m.end() for m in re.finditer("\n", text)]


def line_count(text: str) -> int:
    return text.count("\n") + 1


def line_end(text: str, line: int, starts: list[int] | None = None) -> int:
    """Offset do fim da linha (antes do ``\\n``)."""
    starts = starts if starts is not None else line_starts(text)
    line = min(max(line, 1), len(starts))
    return starts[line] - 1 if line < len(starts) else len(text)


def to_offset(text: str, line: int, col: int, starts: list[int] | None = None) -> int:
    starts = starts if starts is not None else line_starts(text)
    line = min(max(line, 1), len(starts))
    start = starts[line - 1]
    return min(start + max(col, 0), line_end(text, line, starts))


def to_linecol(text: str, offset: int, starts: list[int] | None = None) -> tuple[int, int]:
    starts = starts if starts is not None else line_starts(text)
    offset = min(max(offset, 0), len(text))
    idx = bisect.bisect_right(starts, offset) - 1
    return idx + 1, offset - starts[idx]


def get_line(text: str, line: int, starts: list[int] | None = None) -> str:
    starts = starts if starts is not None else line_starts(text)
    if line < 1 or line > len(starts):
        return ""
    return text[starts[line - 1] : line_end(text, line, starts)]


def excerpt(text: str, start_line: int, end_line: int, context: int = 2) -> list[tuple[int, str, bool]]:
    """Linhas ``[start-context, end+context]`` como ``(número, texto, está_no_trecho)``."""
    lines = text.split("\n")
    lo = max(1, start_line - context)
    hi = min(len(lines), end_line + context)
    return [(n, lines[n - 1], start_line <= n <= end_line) for n in range(lo, hi + 1)]


# ------------------------------------------------------------ normalização

_DASHES = dict.fromkeys(map(ord, "‐‑‒–—―−"), "-")
_QUOTES = set("\"'`´‘’‚‛“”„‟«»")

# Comandos cujo argumento não aparece no PDF (ou aparece transformado).
_DROP_ARG = {
    "label", "ref", "eqref", "pageref", "autoref", "cref", "Cref", "nameref",
    "cite", "citep", "citet", "citealp", "citeauthor", "citeyear", "parencite", "textcite",
    "autocite", "footcite", "nocite", "input", "include", "includegraphics", "bibliography",
    "bibliographystyle", "addbibresource", "usepackage", "documentclass", "begin", "end",
    "newcommand", "renewcommand", "providecommand", "vspace", "hspace", "setlength",
    "addtolength", "pagestyle", "thispagestyle", "graphicspath", "hypersetup", "definecolor",
    "color", "textcolor", "colorbox", "setcounter", "addtocounter", "linespread",
}


@dataclass
class NormText:
    text: str
    index: list[int]  # index[i] = offset no texto original do caractere normalizado i


def _emit(out: list[str], idx: list[int], chunk: str, pos: int) -> None:
    for ch in unicodedata.normalize("NFKD", chunk):
        if unicodedata.combining(ch):
            continue
        ch = ch.lower().translate(_DASHES)
        if ch in _QUOTES:
            continue
        if ch.isspace():
            if out and out[-1] != " ":
                out.append(" ")
                idx.append(pos)
            continue
        if ch == "-" and out and out[-1] == "-":
            continue
        out.append(ch)
        idx.append(pos)


def _skip_ws(src: str, i: int) -> int:
    while i < len(src) and src[i] in " \t":
        i += 1
    return i


def _skip_group(src: str, i: int, open_ch: str, close_ch: str) -> int:
    depth = 0
    while i < len(src):
        ch = src[i]
        if ch == "\\":
            i += 2
            continue
        if ch == open_ch:
            depth += 1
        elif ch == close_ch:
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return i


def normalize(src: str, latex: bool = False) -> NormText:
    """Minúsculas, sem acentos, espaços colapsados, sem aspas; com ``latex`` remove a marcação."""
    out: list[str] = []
    idx: list[int] = []
    n = len(src)
    if not latex:
        for i, ch in enumerate(src):
            _emit(out, idx, ch, i)
        return NormText("".join(out), idx)

    i = 0
    while i < n:
        ch = src[i]
        if ch == "%":
            j = src.find("\n", i)
            i = n if j < 0 else j
            continue
        if ch == "\\":
            if i + 1 < n and src[i + 1].isascii() and src[i + 1].isalpha():
                j = i + 1
                while j < n and src[j].isascii() and src[j].isalpha():
                    j += 1
                name = src[i + 1 : j]
                if j < n and src[j] == "*":
                    j += 1
                i = j
                if name in _DROP_ARG:
                    i = _skip_ws(src, i)
                    while i < n and src[i] == "[":
                        i = _skip_ws(src, _skip_group(src, i, "[", "]"))
                    if i < n and src[i] == "{":
                        i = _skip_group(src, i, "{", "}")
                continue
            if i + 1 < n:
                nxt = src[i + 1]
                if nxt in "%&$#_{}":
                    _emit(out, idx, nxt, i + 1)
                elif nxt in "\\ ":
                    _emit(out, idx, " ", i)
                i += 2
                continue
            i += 1
            continue
        if ch in "{}$&^_":
            i += 1
            continue
        if ch == "~":
            _emit(out, idx, " ", i)
            i += 1
            continue
        _emit(out, idx, ch, i)
        i += 1
    return NormText("".join(out), idx)


@lru_cache(maxsize=64)
def _normalized(src: str, latex: bool) -> NormText:
    return normalize(src, latex)


_DROP_ARG_RE = re.compile(
    r"\\(?:" + "|".join(sorted(_DROP_ARG, key=len, reverse=True)) + r")(?![A-Za-z])\*?"
    r"(?:\s*\[[^\]]*\])*(?:\s*\{[^{}]*\})?"
)
_ACCENTS = {"'": "\u0301", "`": "\u0300", "^": "\u0302", '"': "\u0308", "~": "\u0303"}


def latex_to_plain(src: str) -> str:
    """Aproximação do texto impresso pelo LaTeX (mantém acentos e caixa), para procurar no PDF."""
    s = re.sub(r"(?<!\\)%[^\n]*", "", src)
    s = _DROP_ARG_RE.sub("", s)
    s = re.sub(r"\\(['`^\"~])\{?([A-Za-z])\}?",
               lambda m: unicodedata.normalize("NFC", m.group(2) + _ACCENTS[m.group(1)]), s)
    s = s.replace("---", "—").replace("--", "–").replace("``", "“").replace("''", "”")
    s = re.sub(r"\\\\|~", " ", s)
    s = re.sub(r"\\([%&$#_])", r"\1", s)
    s = re.sub(r"\\[A-Za-z]+\*?", "", s)
    s = re.sub(r"[{}$]", "", s)
    return " ".join(s.split())


def normalize_query(phrase: str) -> str:
    text = normalize(phrase).text
    text = re.sub(r"(\w)- (\w)", r"\1\2", text)  # hifenização de fim de linha do PDF
    return text.strip()


# ----------------------------------------------------------------- busca


@dataclass
class Match:
    start: int  # offsets no texto original
    end: int
    score: float  # 1.0 = exata


_CMD_TAIL = re.compile(r"\\[A-Za-z]+\*?$")


def _balance(src: str, s: int, e: int) -> tuple[int, int]:
    """Inclui chaves (e o comando) vizinhos para não devolver ``\\textbf{2019`` ou ``dados}`` desbalanceados."""
    quote = src[s:e]
    opens, closes = quote.count("{"), quote.count("}")
    while opens > closes and e < len(src) and src[e] == "}":
        e += 1
        closes += 1
    while closes > opens and s > 0 and src[s - 1] == "{":
        s -= 1
        opens += 1
        cmd = _CMD_TAIL.search(src, max(0, s - 40), s)
        if cmd:
            s = cmd.start()
    return s, e


def _to_match(src: str, nt: NormText, ns: int, ne: int, score: float, latex: bool) -> Match:
    s = nt.index[ns]
    e = nt.index[ne - 1] + 1
    if latex:
        s, e = _balance(src, s, e)
    return Match(s, e, score)


def find_phrase(
    src: str,
    phrase: str,
    *,
    latex: bool = False,
    start: int = 0,
    end: int | None = None,
    max_results: int = 5,
    min_score: float = 0.6,
) -> list[Match]:
    """Procura ``phrase`` em ``src[start:end]`` ignorando caixa, acentos e (com ``latex``) a marcação."""
    q = normalize_query(phrase)
    if len(q) < 2:
        return []
    nt = _normalized(src, latex)
    hay = nt.text
    lo = bisect.bisect_left(nt.index, start)
    hi = len(hay) if end is None else bisect.bisect_left(nt.index, end)
    if hi <= lo:
        return []

    results: list[Match] = []
    pos = hay.find(q, lo, hi)
    while pos >= 0 and len(results) < max_results:
        results.append(_to_match(src, nt, pos, pos + len(q), 1.0, latex))
        pos = hay.find(q, pos + 1, hi)
    if results:
        return results
    return [
        _to_match(src, nt, a, b, score, latex)
        for score, a, b in _fuzzy(hay, q, lo, hi, max_results, min_score)
    ]


def _fuzzy(hay: str, q: str, lo: int, hi: int, max_results: int, min_score: float):
    size = len(q)
    words = sorted({w for w in re.findall(r"\w{3,}", q)}, key=len, reverse=True)[:5]
    starts: set[int] = set()
    window = hay[lo:hi]
    for word in words:
        offset_in_q = q.find(word)
        for m in re.finditer(re.escape(word), window):
            starts.add(max(lo, lo + m.start() - offset_in_q))
            if len(starts) > 400:
                break
    if not starts:
        step = max(1, size // 3)
        starts = set(range(lo, max(lo + 1, hi - size + 1), step))

    scored: list[tuple[float, int, int]] = []
    for s in starts:
        best: tuple[float, int, int] | None = None
        for length in {size, max(1, int(size * 0.85)), int(size * 1.15) + 1}:
            seg = hay[s : min(hi, s + length)]
            if not seg:
                continue
            sm = SequenceMatcher(None, seg, q, autojunk=False)
            if sm.real_quick_ratio() < min_score or sm.quick_ratio() < min_score:
                continue
            ratio = sm.ratio()
            if ratio >= min_score and (best is None or ratio > best[0]):
                blocks = [b for b in sm.get_matching_blocks() if b.size]
                best = (ratio, s + blocks[0].a, s + blocks[-1].a + blocks[-1].size)
        if best:
            scored.append(best)

    scored.sort(key=lambda t: (-t[0], t[1]))
    chosen: list[tuple[float, int, int]] = []
    for score, a, b in scored:
        if any(not (b <= ca or a >= cb) for _, ca, cb in chosen):
            continue
        chosen.append((round(score, 3), a, b))
        if len(chosen) >= max_results:
            break
    return chosen
