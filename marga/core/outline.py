"""Seções de um documento (o "sumário"), com a página no PDF e, para LaTeX, o arquivo e a linha do fonte.

De onde vêm, em ordem de preferência:

- LaTeX: os comandos ``\\section`` & cia. do fonte, posicionados no PDF pelo SyncTeX;
- marcadores (bookmarks) do PDF, como os que um leitor de PDF mostra na barra lateral;
- títulos detectados pela tipografia (negrito, tamanho e numeração), para PDFs sem marcadores.
"""

from __future__ import annotations

import bisect
import os
import posixpath
import re
import statistics
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path

import pymupdf

from . import synctex
from .pdf import TEXT_FLAGS
from .textmap import latex_to_plain
from .workspace import Workspace, read_json, write_json

CACHE_VERSION = 1
SOURCE_LABELS = {
    "fonte": "seções do fonte LaTeX",
    "marcadores": "marcadores do PDF",
    "tipografia": "títulos detectados no PDF",
}
TOP_OF_PAGE = 130.0  # pontos: título acima disso abre a página


@dataclass
class Section:
    level: int  # 1 = nível mais alto
    title: str  # com a numeração, quando há (ex.: "2.1 Formação Hipocampal")
    page: int | None = None  # página do PDF, 1-based
    y: float | None = None  # topo do título na página (pontos PDF), quando conhecido
    file: str | None = None  # .tex de origem (relativo ao workspace)
    line: int | None = None

    @classmethod
    def from_dict(cls, data: dict) -> Section:
        return cls(**{k: data.get(k) for k in ("level", "title", "page", "y", "file", "line")})


@dataclass
class Outline:
    sections: list[Section] = field(default_factory=list)
    source: str = ""  # chave de SOURCE_LABELS

    @property
    def source_label(self) -> str:
        return SOURCE_LABELS.get(self.source, "")

    def to_dict(self) -> dict:
        return {"version": CACHE_VERSION, "source": self.source, "sections": [asdict(s) for s in self.sections]}

    @classmethod
    def from_dict(cls, data: dict) -> Outline:
        return cls([Section.from_dict(s) for s in data.get("sections", [])], data.get("source", ""))


# ------------------------------------------------------------ navegação
def section_at(sections: list[Section], page: int) -> int | None:
    """Índice da seção em que ``page`` está: a última que começa nela ou antes."""
    found = None
    for i, sec in enumerate(sections):
        if sec.page and sec.page <= page:
            found = i
    return found


def contains(sections: list[Section], i: int, page: int) -> bool:
    """A página está entre o início da seção ``i`` e o início da seção seguinte (de qualquer nível)?"""
    sec = sections[i]
    if not sec.page or sec.page > page:
        return False
    nxt = next((s.page for s in sections[i + 1 :] if s.page), None)
    return nxt is None or nxt >= page


def page_span(sections: list[Section], i: int, total: int) -> tuple[int, int]:
    """Páginas da seção ``i``: do título até a próxima seção de nível igual ou mais alto."""
    sec = sections[i]
    start = sec.page or 1
    end = total
    for nxt in sections[i + 1 :]:
        if nxt.page and nxt.level <= sec.level:
            opens_page = nxt.y is not None and nxt.y < TOP_OF_PAGE
            end = nxt.page - 1 if opens_page else nxt.page
            break
    return start, max(start, min(end, total))


# ---------------------------------------------------------- marcadores
def bookmarks(path: str | Path) -> list[Section]:
    with pymupdf.open(str(path)) as doc:
        toc = doc.get_toc(simple=True)
        total = doc.page_count
    out = []
    for level, title, page in toc:
        title = " ".join(str(title).split())
        if title:
            out.append(Section(level=max(1, int(level)), title=title, page=page if 1 <= page <= total else None))
    return out


# ------------------------------------------------- títulos pela tipografia
_NUMBERED = re.compile(r"^(?P<num>\d{1,3}(?:\.\d{1,3}){0,4}|[A-Z](?:\.\d{1,3}){1,4})\.?\s+(?P<title>\S.*)$")
_CHAPTER = re.compile(r"^(?:cap[ií]tulo|chapter|parte|part|ap[êe]ndice|appendix|anexo)\s+(?:\d{1,3}|[A-Z]|[IVXLC]{1,6})\.?$",
                      re.I)
_LEADERS = re.compile(r"(?:\.\s?){4,}")
_PAGE_NO = re.compile(r"^(?:\d{1,4}|[ivxlcdm]{1,7})$", re.I)
_BOLD_FONT = re.compile(r"bold|black|heavy|semibold|demi|^(?:cm|sf|ec|tc|lm\w*)bx", re.I)


@dataclass
class _Row:
    """Uma linha visual da página (pedaços na mesma altura, como o número e o título de uma seção)."""

    page: int
    x0: float
    y0: float
    x1: float
    y1: float
    text: str
    size: float
    bold: bool


def _is_bold(span: dict) -> bool:
    font = str(span.get("font", "")).split("+")[-1]
    return bool(span.get("flags", 0) & 16) or bool(_BOLD_FONT.search(font))


def _page_rows(page: pymupdf.Page, pno: int) -> list[_Row]:
    pieces: list[_Row] = []
    for block in page.get_text("dict", flags=TEXT_FLAGS)["blocks"]:
        if block.get("type") != 0:
            continue
        for ln in block.get("lines", []):
            dx, dy = ln.get("dir", (1.0, 0.0))
            if abs(dy) > 0.05 or dx < 0:
                continue  # texto vertical ou girado
            spans = [s for s in ln["spans"] if s["text"].strip()]
            if not spans:
                continue
            text = " ".join("".join(s["text"] for s in ln["spans"]).split())
            pieces.append(_Row(pno, *ln["bbox"], text, max(s["size"] for s in spans), all(_is_bold(s) for s in spans)))
    pieces.sort(key=lambda r: ((r.y0 + r.y1) / 2, r.x0))
    rows: list[_Row] = []
    for p in pieces:
        center, target = (p.y0 + p.y1) / 2, None
        for r in reversed(rows[-4:]):
            size = max(r.size, p.size)
            gap = p.x0 - r.x1 if p.x0 >= r.x0 else r.x0 - p.x1
            if abs((r.y0 + r.y1) / 2 - center) <= 0.35 * size and gap <= 3 * size:
                target = r
                break
        if target is None:
            rows.append(p)
            continue
        first, second = (target, p) if p.x0 >= target.x0 else (p, target)
        sep = "" if second.x0 - first.x1 < 0.1 * max(first.size, second.size) else " "
        target.text = f"{first.text}{sep}{second.text}"
        target.x0, target.x1 = min(target.x0, p.x0), max(target.x1, p.x1)
        target.y0, target.y1 = min(target.y0, p.y0), max(target.y1, p.y1)
        target.size, target.bold = max(target.size, p.size), target.bold and p.bold
    return rows


def detect_headings(path: str | Path) -> list[Section]:
    """Títulos de seção de um PDF sem marcadores, pela tipografia.

    Um título é uma linha curta em negrito ou maior que o texto do corpo. Os numerados ("2.1 Título")
    dão o nível pela numeração; os sem número ("Agradecimentos") herdam o nível de quem tem o mesmo tamanho.
    Ficam de fora a folha de rosto, as entradas de sumários e listas, notas pequenas e cabeçalhos repetidos.
    """
    rows: list[_Row] = []
    with pymupdf.open(str(path)) as doc:
        for pno, page in enumerate(doc, start=1):
            rows.extend(_page_rows(page, pno))
    weights: Counter = Counter()
    for r in rows:
        weights[round(r.size, 1)] += len(r.text)
    if not weights:
        return []
    body = weights.most_common(1)[0][0]

    # páginas de sumário/listas: vários números de página alinhados à direita
    right: dict[int, float] = {}
    for r in rows:
        right[r.page] = max(right.get(r.page, 0.0), r.x1)
    numbers: Counter = Counter(r.page for r in rows if _PAGE_NO.match(r.text) and r.x1 >= right[r.page] - 2)
    listing = {p for p, n in numbers.items() if n >= 4}

    cands: list[dict] = []
    for r in sorted(rows, key=lambda r: (r.page, r.y0, r.x0)):
        text = r.text.strip()
        if not 2 <= len(text) <= 160 or not any(ch.isalpha() for ch in text):
            continue
        if r.size < body * 0.97 or _LEADERS.search(text) or not (r.bold or r.size >= body * 1.12):
            continue
        numbered = _NUMBERED.match(text)
        if numbered and r.page not in listing:
            cands.append({"row": r, "title": text, "depth": numbered["num"].count(".") + 1, "kind": "num"})
        elif _CHAPTER.match(text) and r.page not in listing:
            cands.append({"row": r, "title": text, "depth": 1, "kind": "chap"})
        elif not numbered and r.bold and r.size >= body * 1.12:
            cands.append({"row": r, "title": text, "depth": None, "kind": "plain"})

    # junta "Capítulo 1" + título da linha de baixo, e títulos que quebram em duas linhas
    merged: list[dict] = []
    for c in cands:
        prev = merged[-1] if merged else None
        r = c["row"]
        if prev and prev["row"].page == r.page and c["kind"] != "chap":
            gap = r.y0 - prev["bottom"]
            if prev["kind"] == "chap" and " — " not in prev["title"] and gap < 5 * r.size:
                prev["title"] += " — " + c["title"]
                prev["bottom"] = r.y1
                continue
            if (c["kind"] == "plain" and abs(prev["row"].size - r.size) < 0.5 and prev["row"].bold == r.bold
                    and gap < 0.7 * r.size):
                prev["title"] += " " + c["title"]
                prev["bottom"] = r.y1
                continue
        merged.append({**c, "bottom": r.y1})

    sizes: dict[int, list[float]] = {}
    for c in merged:
        if c["depth"]:
            sizes.setdefault(c["depth"], []).append(c["row"].size)
    level_size = {d: statistics.median(v) for d, v in sizes.items()}
    largest = max(level_size.values()) if level_size else None
    title_pages = {r.page for r in rows if largest and r.size > largest + 1.0}
    plain_sizes = sorted({round(c["row"].size, 1) for c in merged if not c["depth"]}, reverse=True)

    out: list[Section] = []
    for c in merged:
        r = c["row"]
        if c["depth"]:
            if abs(r.size - level_size[c["depth"]]) > 1.5:
                continue  # ex.: célula de tabela em negrito que começa com número
            level = c["depth"]
        elif level_size:
            if r.page in title_pages or r.size > largest + 1.0:
                continue  # folha de rosto
            level = min(level_size, key=lambda d: abs(level_size[d] - r.size))
        else:
            level = min(3, plain_sizes.index(round(r.size, 1)) + 1)
        out.append(Section(level=level, title=" ".join(c["title"].split()), page=r.page, y=round(r.y0, 1)))

    repeated = Counter(s.title.casefold() for s in out)
    seen: set[str] = set()
    unique = []
    for sec in out:
        key = sec.title.casefold()
        if repeated[key] > 2 and key in seen:
            continue  # cabeçalho corrente repetido em todas as páginas
        seen.add(key)
        unique.append(sec)
    if unique:
        lowest = min(s.level for s in unique)
        for sec in unique:
            sec.level = sec.level - lowest + 1
    return unique


# ------------------------------------------------------------ fonte LaTeX
_TEX_TOKEN = re.compile(
    r"\\(?P<cmd>part|chapter|section|subsection|subsubsection)(?P<star>\*?)\s*(?:\[[^\]]*\]\s*)?\{"
    r"|\\(?:input|include|subfile)\s*\{(?P<inc>[^}]+)\}"
    r"|\\(?P<app>appendix)\b"
)
_TEX_COMMENT = re.compile(r"(?<!\\)%[^\n]*")
_TEX_LEVELS = {"part": 0, "chapter": 1, "section": 2, "subsection": 3, "subsubsection": 4}


def _braced(text: str, start: int) -> str:
    depth, i = 1, start
    while i < len(text) and depth:
        ch = text[i]
        if ch == "\\":
            i += 2
            continue
        depth += {"{": 1, "}": -1}.get(ch, 0)
        i += 1
    return text[start : i - 1] if depth == 0 else text[start : start + 200]


def _resolve_input(arg: str, root: str, current: str, texts: dict[str, str]) -> str | None:
    arg = arg.strip()
    bases = [root, posixpath.dirname(current)]
    for base in bases:
        joined = posixpath.normpath(posixpath.join(base, arg) if base not in ("", ".") else arg)
        for cand in (joined, joined + ".tex"):
            if cand in texts:
                return cand
    return None


def latex_sections(texts: dict[str, str], main: str, root: str) -> list[Section]:
    """Seções do fonte na ordem do documento (seguindo ``\\input``/``\\include`` a partir do principal).

    Ignora o que está comentado e numera como o LaTeX: ``\\section*`` sem número, apêndices com letras.
    """
    raw: list[tuple[int, bool, str, str, int, bool]] = []
    visited: set[str] = set()
    appendix = False

    def walk(path: str, depth: int) -> None:
        nonlocal appendix
        if path in visited or depth > 20 or path not in texts:
            return
        visited.add(path)
        text = _TEX_COMMENT.sub("", texts[path])
        newlines = [i for i, ch in enumerate(text) if ch == "\n"]
        for m in _TEX_TOKEN.finditer(text):
            if m["cmd"]:
                title = latex_to_plain(_braced(text, m.end())) or "(sem título)"
                line = bisect.bisect_left(newlines, m.start()) + 1
                raw.append((_TEX_LEVELS[m["cmd"]], bool(m["star"]), title, path, line, appendix))
            elif m["inc"]:
                target = _resolve_input(m["inc"], root, path, texts)
                if target:
                    walk(target, depth + 1)
            elif m["app"]:
                appendix = True

    walk(main, 0)
    if not raw:
        return []
    numbered_levels = [lvl for lvl, *_ in raw if lvl > 0]
    top = min(numbered_levels) if numbered_levels else 2
    counters = {lvl: 0 for lvl in range(1, 5)}
    in_appendix = False
    out: list[Section] = []
    for level, starred, title, path, line, is_appendix in raw:
        if is_appendix and not in_appendix:
            in_appendix = True
            counters = {lvl: 0 for lvl in range(1, 5)}
        number = ""
        if level >= 1 and not starred:
            counters[level] += 1
            for deeper in range(level + 1, 5):
                counters[deeper] = 0
            if level >= top:
                parts = [counters[lvl] for lvl in range(top, level + 1)]
                first = chr(64 + parts[0]) if in_appendix and 1 <= parts[0] <= 26 else str(parts[0])
                number = ".".join([first, *map(str, parts[1:])])
        out.append(Section(level=level, title=f"{number} {title}" if number else title, file=path, line=line))
    lowest = min(s.level for s in out)
    for sec in out:
        sec.level = sec.level - lowest + 1
    return out


def place_on_pdf(sections: list[Section], pdf: str | Path, src_dir: str | Path, doc_root: str) -> list[Section]:
    """Página (e altura) de cada seção do fonte no PDF compilado, via SyncTeX."""
    if not synctex.available():
        return sections
    for sec in sections:
        if not sec.file or not sec.line:
            continue
        src = Path(src_dir) / os.path.relpath(sec.file, doc_root)
        for line in (sec.line, sec.line + 1):
            boxes = synctex.forward(pdf, src, line, src_dir)
            if boxes:
                first = min(boxes, key=lambda b: (b.page, b.v))
                sec.page, sec.y = first.page, round(first.rect[1], 1)
                break
    return sections


# ---------------------------------------------------------------- caches
def _from_pdf(path: Path) -> Outline:
    found = bookmarks(path)
    return Outline(found, "marcadores") if found else Outline(detect_headings(path), "tipografia")


def for_pdf(ws: Workspace, sha: str) -> Outline:
    """Seções de um PDF avulso (guardado como blob), com cache em ``.marga/cache/outline``."""
    from .versions import blob_path

    cache = ws.dir / "cache" / "outline" / f"{sha}.json"
    data = read_json(cache, None)
    if data and data.get("version") == CACHE_VERSION:
        return Outline.from_dict(data)
    outline = _from_pdf(blob_path(ws, sha))
    write_json(cache, outline.to_dict())
    return outline


def for_build(ws: Workspace, doc, version, result) -> Outline:
    """Seções de um documento LaTeX compilado (cache no diretório da compilação, por PDF)."""
    from .latex import build_dir
    from .versions import version_texts

    pdf = Path(result.pdf)
    stamp = f"{CACHE_VERSION}:{pdf.stat().st_mtime_ns}:{doc.main}"
    cache = build_dir(ws, doc.id, version.n) / "outline.json"
    data = read_json(cache, None)
    if data and data.get("stamp") == stamp:
        return Outline.from_dict(data["outline"])
    sections: list[Section] = []
    if doc.main and doc.main in version.files:
        sections = latex_sections(version_texts(ws, doc, version), doc.main, doc.root)
        place_on_pdf(sections, pdf, result.src_dir, doc.root)
    outline = Outline(sections, "fonte") if any(s.page for s in sections) else _from_pdf(pdf)
    write_json(cache, {"stamp": stamp, "outline": outline.to_dict()})
    return outline
