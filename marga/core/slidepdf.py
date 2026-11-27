"""PowerPoint no modo PDF: cada palavra da página ligada à palavra do texto extraído do .pptx.

O PDF (exportado pelo PowerPoint ou convertido pelo LibreOffice) é só a vista: o que se comenta e o que o agente
edita é o .pptx. Os dois conversores escrevem o texto de cada slide na ordem das formas e dos parágrafos, a mesma
do texto extraído (``formats.extract_pptx``). Por isso um alinhamento de sequências liga as palavras de uma ponta
à outra sem busca nem posição aproximada: uma frase que se repete em duas formas cai na forma em que foi clicada,
e a marca de um comentário só aparece nas palavras dele. O que a página tem e o texto não (rodapé, número do
slide, textos do layout, marcadores) fica sem par, e o que o texto tem e a página não (notas, formas ocultas)
também.
"""

from __future__ import annotations

import difflib
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf

from .formats import NOTES_LABEL, Extracted
from .textmap import line_starts

_MARKER = re.compile(r"=== slide \d+")
_PREFIX = re.compile(r"#+ |\s*- ")  # título e item de lista no texto extraído (não estão no slide)
_WORD = re.compile(r"\S+")

Rect = tuple[float, float, float, float]
# Pedaço de palavra do PDF ligado ao texto: (x0, y0, x1, y1, início, fim, índice da palavra do PDF).
Piece = tuple[float, float, float, float, int, int, int]


def _norm(word: str) -> str:
    """Palavra comparável dos dois lados: ligaduras desfeitas, sem maiúsculas e sem pontuação nas pontas."""
    word = unicodedata.normalize("NFKC", word).casefold()
    return re.sub(r"^\W+|\W+$", "", word) or word


def _compact(word: str) -> str:
    return re.sub(r"\W+", "", word)


def slide_words(ext: Extracted, slide: int) -> list[tuple[int, int, str]]:
    """Palavras das formas do slide no texto extraído: (início, fim) no texto e a palavra normalizada."""
    starts = line_starts(ext.text)
    out: list[tuple[int, int, str]] = []
    notes = False
    for n, line in enumerate(ext.text.split("\n"), start=1):
        info = ext.lines[n - 1] if n <= len(ext.lines) else {}
        if info.get("page") != slide:
            continue
        if _MARKER.match(line) or line == NOTES_LABEL:
            notes = line == NOTES_LABEL
            continue
        if notes or info.get("note"):
            continue
        table = line.startswith("| ")
        prefix = None if table else _PREFIX.match(line)
        for m in _WORD.finditer(line, prefix.end() if prefix else 0):
            if not (table and m.group() == "|"):
                out.append((starts[n - 1] + m.start(), starts[n - 1] + m.end(), _norm(m.group())))
    return out


def _split(words: list[tuple], i1: int, pdf: list[str], tokens: list[tuple], j1: int, text: list[str]) -> list[Piece]:
    """Mesmas letras, cortadas em palavras diferentes (o PDF juntou "12" e "meses" de formas vizinhas, por
    exemplo): cada palavra do PDF vira um pedaço por palavra do texto, na proporção das letras."""
    a, b = [_compact(w) for w in pdf], [_compact(t) for t in text]
    if not "".join(a) or "".join(a) != "".join(b):
        return []
    ends_b = [sum(map(len, b[: k + 1])) for k in range(len(b))]
    out: list[Piece] = []
    pos = 0
    for ii, part in enumerate(a):
        w, a0, a1 = words[i1 + ii], pos, pos + len(part)
        pos = a1
        if a1 == a0:
            continue
        scale = (w[2] - w[0]) / (a1 - a0)
        for jj, b1 in enumerate(ends_b):
            lo, hi = max(a0, b1 - len(b[jj])), min(a1, b1)
            if lo < hi:
                out.append((w[0] + scale * (lo - a0), w[1], w[0] + scale * (hi - a0), w[3], *tokens[j1 + jj][:2], i1 + ii))
    return out


def align(words: list[tuple], tokens: list[tuple[int, int, str]]) -> list[Piece]:
    """Liga as palavras do PDF às do texto pelo alinhamento das duas sequências, na ordem em que vêm."""
    pdf = [_norm(w[4]) for w in words]
    text = [t[2] for t in tokens]
    out: list[Piece] = []

    def whole(i: int, j: int) -> None:
        out.append((*words[i][:4], *tokens[j][:2], i))

    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, pdf, text, autojunk=False).get_opcodes():
        if tag == "equal":
            for k in range(i2 - i1):
                whole(i1 + k, j1 + k)
        elif tag == "replace":
            split = _split(words, i1, pdf[i1:i2], tokens, j1, text[j1:j2])
            if split:
                out += split
            elif i2 - i1 == j2 - j1:  # trocas uma a uma valem se as palavras forem parecidas (aspas, acentos…)
                for k in range(i2 - i1):
                    if difflib.SequenceMatcher(None, pdf[i1 + k], text[j1 + k]).ratio() >= 0.6:
                        whole(i1 + k, j1 + k)
    return out


def _dist(box: tuple, x: float, y: float) -> float:
    dx = 0 if box[0] <= x <= box[2] else min(abs(x - box[0]), abs(x - box[2]))
    dy = 0 if box[1] <= y <= box[3] else min(abs(y - box[1]), abs(y - box[3]))
    return dx * dx + dy * dy * 4


@dataclass
class SlideMap:
    """Palavras de uma página do PDF (na ordem do arquivo) e os pedaços delas ligados ao texto extraído."""

    words: list[tuple] = field(default_factory=list)  # (x0, y0, x1, y1, palavra, bloco, linha, n)
    pieces: list[Piece] = field(default_factory=list)  # na ordem do PDF

    def _line(self, piece: Piece) -> tuple:
        return self.words[piece[6]][5:7]

    def at(self, x: float, y: float) -> tuple[int, int] | None:
        """Trecho do texto da palavra sob o ponto (ou da mais próxima).

        Numa palavra sem par (marcador de lista, número do slide), vale a palavra com par mais próxima na mesma
        linha do PDF; sem nenhuma (rodapé, texto do layout), ``None``.
        """
        if not self.words:
            return None
        # Empate (palavras uma sobre a outra): vale a desenhada por último, que é a que aparece por cima.
        best = min(range(len(self.words)), key=lambda i: (_dist(self.words[i], x, y), -i))
        line = self.words[best][5:7]
        cands = [p for p in self.pieces if p[6] == best] or [p for p in self.pieces if self._line(p) == line]
        if not cands:
            return None
        piece = min(cands, key=lambda p: _dist(p, x, y))
        return piece[4], piece[5]

    def within(self, rect: Rect) -> tuple[int, int] | None:
        """Do começo da primeira ao fim da última palavra ligada dentro do retângulo."""
        x0, y0, x1, y1 = rect
        hits = [p for p in self.pieces if x0 <= (p[0] + p[2]) / 2 <= x1 and y0 <= (p[1] + p[3]) / 2 <= y1]
        return (min(p[4] for p in hits), max(p[5] for p in hits)) if hits else None

    def boxes(self, start: int, end: int) -> list[Rect]:
        """Onde o trecho ``[start, end)`` do texto aparece na página: um retângulo por linha do PDF."""
        out: list[list[float]] = []
        prev = None
        for p in self.pieces:
            if not (p[4] < end and p[5] > start) and not (start == end and p[4] <= start <= p[5]):
                continue
            line = self._line(p)
            if out and prev is not None and prev[1] == line and p[6] - prev[0] <= 1:
                box = out[-1]
                box[:] = [min(box[0], p[0]), min(box[1], p[1]), max(box[2], p[2]), max(box[3], p[3])]
            else:
                out.append(list(p[:4]))
            prev = (p[6], line)
        return [(b[0], b[1], b[2], b[3]) for b in out]


def slide_map(pdf_path: str | Path, page: int, ext: Extracted) -> SlideMap:
    """Liga as palavras da página ``page`` do PDF dos slides ao texto extraído (a página N é o slide N)."""
    with pymupdf.open(str(pdf_path), filetype="pdf") as doc:
        if not 1 <= page <= doc.page_count:
            return SlideMap()
        words = [tuple(w) for w in doc[page - 1].get_text("words", sort=False)]
    return SlideMap(words, align(words, slide_words(ext, page)))
