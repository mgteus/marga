"""Word (.docx), PowerPoint (.pptx) e HTML: o texto que se revisa no Marga.

O Marga não edita esses arquivos (quem edita é o agente). Ele extrai deles um texto em linhas, parecido com
Markdown, e é nesse texto que ficam as âncoras dos comentários e o diff entre versões:

- um parágrafo por linha; títulos começam com ``#`` (um por nível) e itens de lista com ``-`` (dois espaços de
  recuo por nível);
- tabelas: uma linha de texto por linha da tabela, com as células entre ``|``;
- PowerPoint: cada slide começa em ``=== slide N ===``, e as notas do apresentador vêm depois de
  ``Notas do apresentador:``;
- Word: as notas de rodapé viram ``[^N]`` no texto e ``[^N]: …`` no fim; com revisões (controle de alterações),
  vale o texto como se todas fossem aceitas.

O texto de cada versão é extraído uma vez e guardado com ela (``Version.extracted``): se a extração mudar numa
versão futura do Marga, as âncoras das versões já registradas continuam valendo.

Só a biblioteca padrão: .docx e .pptx são .zip com XML. Os arquivos são entrada não confiável, então cada parte
XML tem um limite de tamanho descompactada.
"""

from __future__ import annotations

import json
import re
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from xml.etree import ElementTree as ET

from . import ooxml
from .versions import decode_text
from .workspace import MargaError

FORMATS: dict[str, str] = {".docx": "docx", ".pptx": "pptx", ".html": "html", ".htm": "html"}
LABELS: dict[str, str] = {"docx": "Word (.docx)", "pptx": "PowerPoint (.pptx)", "html": "HTML"}
SLIDE_MARKER = "=== slide {n}{hidden} ==="
NOTES_LABEL = "Notas do apresentador:"


def kind_for(path: str | Path) -> str | None:
    """Tipo de documento pela extensão (``docx``, ``pptx`` ou ``html``); ``None`` para as outras."""
    return FORMATS.get(Path(path).suffix.lower())


@dataclass
class Extracted:
    """Texto extraído de um arquivo, o que se sabe de cada linha e os títulos (ou slides)."""

    text: str
    lines: list[dict] = field(default_factory=list)  # por linha: {"page": slide} ou {"src": [primeira, última]}
    sections: list[dict] = field(default_factory=list)  # {"level": 1.., "title": "…", "line": n}

    def _info(self, line: int) -> dict:
        return self.lines[line - 1] if 1 <= line <= len(self.lines) else {}

    def page_of_line(self, line: int) -> int | None:
        """PowerPoint: o slide da linha."""
        return self._info(line).get("page")

    def source_lines(self, first: int, last: int) -> tuple[int, int] | None:
        """HTML: as linhas do arquivo de onde vieram as linhas ``first..last`` do texto."""
        spans = [s for s in (self._info(n).get("src") for n in range(first, last + 1)) if s]
        if not spans:
            return None
        return min(s[0] for s in spans), max(s[1] for s in spans)

    def to_json(self) -> bytes:
        data = {"text": self.text, "lines": self.lines, "sections": self.sections}
        return json.dumps(data, ensure_ascii=False).encode("utf-8")

    @classmethod
    def from_json(cls, data: bytes) -> Extracted:
        obj = json.loads(data.decode("utf-8"))
        return cls(obj["text"], obj.get("lines", []), obj.get("sections", []))


def extract(kind: str, data: bytes, name: str = "") -> Extracted:
    """Texto revisável de um arquivo ``docx``, ``pptx`` ou ``html``; erros viram ``MargaError`` legível."""
    extractor = {"docx": extract_docx, "pptx": extract_pptx, "html": extract_html}.get(kind)
    if extractor is None:
        raise MargaError(f"Não sei extrair texto de documentos do tipo {kind}.")
    try:
        return extractor(data)
    except MargaError as exc:
        raise MargaError(f"{name}: {exc}" if name else str(exc)) from exc


class _Lines:
    """Acumula as linhas do texto, sem linhas vazias repetidas, com o que se sabe de cada uma."""

    def __init__(self) -> None:
        self.lines: list[str] = []
        self.info: list[dict] = []
        self.sections: list[dict] = []

    def add(self, text: str, info: dict | None = None, section: tuple[int, str] | None = None) -> None:
        text = text.rstrip()
        if not text and (not self.lines or not self.lines[-1]):
            return
        self.lines.append(text)
        self.info.append(info or {})
        if section and text:
            self.sections.append({"level": section[0], "title": section[1], "line": len(self.lines)})

    def result(self, keep_info: bool = True) -> Extracted:
        while self.lines and not self.lines[-1]:
            self.lines.pop()
            self.info.pop()
        return Extracted("\n".join(self.lines), self.info if keep_info else [], self.sections)


# ------------------------------------------------------------- .zip + XML
_MC = ooxml.MC
_open_zip = ooxml.open_package
_xml = ooxml.read_xml
_rels = ooxml.rels
_main_xml = ooxml.main_part
_related = ooxml.related


# ------------------------------------------------------------------ Word
_W = ooxml.W
# Dentro de um parágrafo, o que não é texto visível: propriedades, texto apagado ou movido (revisões, como se
# fossem aceitas), códigos de campo e a versão alternativa de desenhos (repetiria o texto das caixas de texto).
_DOCX_SKIP = {_W + "pPr", _W + "rPr", _W + "del", _W + "moveFrom", _W + "delText", _W + "instrText",
              _W + "delInstrText", _W + "sdtPr", _W + "sdtEndPr", _MC + "Fallback"}
_DOCX_WRAPPERS = {_W + "customXml", _W + "ins", _W + "moveTo", _W + "smartTag", _MC + "AlternateContent",
                  _MC + "Choice"}
_HEADING_STYLE = re.compile(r"heading ([1-9])")


def _wval(el: ET.Element | None, child: str) -> str | None:
    found = None if el is None else el.find(_W + child)
    return None if found is None else found.get(_W + "val")


def _level(value: str | None) -> int | None:
    """``w:outlineLvl`` (0 = título 1; 9 = corpo do texto) → nível de título, ou ``None``."""
    return int(value) + 1 if value and value.isdigit() and int(value) < 9 else None


@dataclass
class _Style:
    name: str
    based_on: str | None
    level: int | None
    listed: bool


class _Docx:
    def __init__(self, zf: zipfile.ZipFile) -> None:
        main, self.root = _main_xml(zf, "word/document.xml", _W + "document", LABELS["docx"])
        rels = _rels(zf, main)
        styles_part, notes_part = _related(rels, "styles"), _related(rels, "footnotes")
        self.styles = self._load_styles(_xml(zf, styles_part, required=False) if styles_part else None)
        notes = _xml(zf, notes_part, required=False) if notes_part else None
        self.notes = {} if notes is None else {
            n.get(_W + "id"): n for n in notes.iter(_W + "footnote")
            if n.get(_W + "type") not in ("separator", "continuationSeparator", "continuationNotice")
        }
        self.note_numbers: dict[str, int] = {}
        self.out = _Lines()

    @staticmethod
    def _load_styles(root: ET.Element | None) -> dict[str, _Style]:
        styles: dict[str, _Style] = {}
        for st in [] if root is None else root.iter(_W + "style"):
            if st.get(_W + "type") != "paragraph":
                continue
            ppr = st.find(_W + "pPr")
            styles[st.get(_W + "styleId", "")] = _Style(
                name=(_wval(st, "name") or "").lower(),
                based_on=_wval(st, "basedOn"),
                level=_level(_wval(ppr, "outlineLvl")),
                listed=ppr is not None and ppr.find(_W + "numPr") is not None,
            )
        return styles

    def _style_chain(self, style_id: str | None) -> Iterator[_Style]:
        seen: set[str] = set()
        while style_id and style_id in self.styles and style_id not in seen:
            seen.add(style_id)
            yield self.styles[style_id]
            style_id = self.styles[style_id].based_on

    def _heading(self, style_id: str | None) -> int | None:
        for st in self._style_chain(style_id):
            match = _HEADING_STYLE.fullmatch(st.name)
            if match:
                return int(match.group(1))
            if st.name == "title":
                return 1
            if st.level:
                return st.level
        return None

    def _note(self, note_id: str | None) -> str:
        number = self.note_numbers.setdefault(note_id or "", len(self.note_numbers) + 1)
        return f"[^{number}]"

    def _inline(self, el: ET.Element, parts: list[str], boxes: list[ET.Element]) -> None:
        for child in el:
            tag = child.tag
            if tag in _DOCX_SKIP:
                continue
            if tag == _W + "t":
                parts.append(child.text or "")
            elif tag in (_W + "tab", _W + "ptab", _W + "br", _W + "cr"):
                parts.append(" ")
            elif tag == _W + "noBreakHyphen":
                parts.append("-")
            elif tag == _W + "footnoteReference":
                parts.append(self._note(child.get(_W + "id")))
            elif tag == _W + "txbxContent":
                boxes.append(child)  # caixa de texto: vira as linhas seguintes ao parágrafo
            else:
                self._inline(child, parts, boxes)

    def _paragraph_text(self, p: ET.Element) -> tuple[str, list[ET.Element]]:
        parts: list[str] = []
        boxes: list[ET.Element] = []
        self._inline(p, parts, boxes)
        return "".join(parts).strip(), boxes

    def paragraph(self, p: ET.Element) -> None:
        text, boxes = self._paragraph_text(p)
        ppr = p.find(_W + "pPr")
        style = _wval(ppr, "pStyle")
        level = _level(_wval(ppr, "outlineLvl")) or self._heading(style)
        num = None if ppr is None else ppr.find(_W + "numPr")
        if num is not None:
            listed = _wval(num, "numId") != "0"  # numId 0 tira a numeração herdada do estilo
        else:
            listed = any(st.listed for st in self._style_chain(style))
        depth = _wval(num, "ilvl") if num is not None else None
        if not text:
            self.out.add("")
        elif level:
            self.out.add("#" * min(level, 6) + " " + text, section=(level, text))
        elif listed:
            self.out.add("  " * (int(depth) if depth and depth.isdigit() else 0) + "- " + text)
        else:
            self.out.add(text)
        for box in boxes:
            self.blocks(box)

    def _cell_texts(self, el: ET.Element) -> list[str]:
        """Textos de uma célula, achatando tabelas aninhadas."""
        out: list[str] = []
        for child in el:
            if child.tag == _W + "p":
                out.append(self._paragraph_text(child)[0])
            elif child.tag == _W + "tbl":
                for tr in child.iter(_W + "tr"):
                    for tc in tr.findall(_W + "tc"):
                        out += self._cell_texts(tc)
            elif child.tag == _W + "sdt" or child.tag in _DOCX_WRAPPERS:
                out += self._cell_texts(child.find(_W + "sdtContent") if child.tag == _W + "sdt" else child)
        return [t for t in out if t]

    def table(self, tbl: ET.Element) -> None:
        for tr in tbl.findall(_W + "tr"):
            cells = [" ".join(self._cell_texts(tc)) for tc in tr.findall(_W + "tc")]
            if any(cells):
                self.out.add("| " + " | ".join(cells) + " |")

    def blocks(self, parent: ET.Element | None) -> None:
        for el in [] if parent is None else parent:
            if el.tag == _W + "p":
                self.paragraph(el)
            elif el.tag == _W + "tbl":
                self.table(el)
            elif el.tag == _W + "sdt":
                self.blocks(el.find(_W + "sdtContent"))
            elif el.tag in _DOCX_WRAPPERS:
                self.blocks(el)

    def run(self) -> Extracted:
        self.blocks(self.root.find(_W + "body"))
        footnotes = []
        for note_id, number in sorted(self.note_numbers.items(), key=lambda kv: kv[1]):
            note = self.notes.get(note_id)
            if note is not None:
                footnotes.append(f"[^{number}]: " + " ".join(self._cell_texts(note)))
        if footnotes:
            self.out.add("")
            for line in footnotes:
                self.out.add(line)
        return self.out.result(keep_info=False)


def extract_docx(data: bytes) -> Extracted:
    with _open_zip(data, LABELS["docx"]) as zf:
        return _Docx(zf).run()


# ------------------------------------------------------------ PowerPoint
_P = ooxml.P
_A = ooxml.A
_R_ID = ooxml.R_ID
_TITLES = {"title", "ctrTitle"}
_BULLETED = {"body", "obj"}  # placeholders de conteúdo: tópicos com marcador, a menos que o parágrafo o tire
_IGNORED = {"dt", "ftr", "sldNum", "hdr", "sldImg"}  # data, rodapé, número do slide…


def a_text(p: ET.Element) -> str:
    """Texto de um parágrafo do DrawingML (``a:p``), como vai para o texto extraído: quebras viram espaço."""
    out = []
    for child in p:
        if child.tag in (_A + "r", _A + "fld"):
            t = child.find(_A + "t")
            out.append(t.text or "" if t is not None else "")
        elif child.tag == _A + "br":
            out.append(" ")
    return "".join(out).strip()


_a_text = a_text


def placeholder(sp: ET.Element) -> tuple[str, str | None] | None:
    """Tipo e índice do placeholder de uma forma (``obj`` quando o tipo não vem), ou ``None``."""
    nv = sp[0] if len(sp) else None
    ph = None if nv is None else nv.find(f"{_P}nvPr/{_P}ph")
    return None if ph is None else (ph.get("type", "obj"), ph.get("idx"))


def shape_id(sh: ET.Element) -> str:
    """Id da forma no slide (``p:cNvPr``): a chave que liga o texto extraído ao leitor de slides."""
    nv = sh[0] if len(sh) else None
    c_nv = None if nv is None else nv.find(_P + "cNvPr")
    return "" if c_nv is None else c_nv.get("id", "")


def _shape_lines(tree: ET.Element | None) -> Iterator[tuple[bool, str, str]]:
    """Linhas das formas de um slide, na ordem em que aparecem: (é título?, texto, chave).

    A chave é ``id:parágrafo`` (``id:rN`` numa linha de tabela), com o índice entre todos os parágrafos da forma.
    """
    for sh in [] if tree is None else tree:
        if sh.tag in (_MC + "AlternateContent", _MC + "Choice", _P + "grpSp"):
            yield from _shape_lines(sh if sh.tag != _MC + "AlternateContent" else sh.find(_MC + "Choice"))
        elif sh.tag == _P + "sp":
            ph = placeholder(sh)
            kind = ph[0] if ph else None
            body = sh.find(_P + "txBody")
            if kind in _IGNORED or body is None:
                continue
            sid = shape_id(sh)
            for i, p in enumerate(body.findall(_A + "p")):
                text = a_text(p)
                if not text:
                    continue
                ppr = p.find(_A + "pPr")
                lvl = "0" if ppr is None else ppr.get("lvl", "0")
                bullet = ppr is not None and (ppr.find(_A + "buChar") is not None or ppr.find(_A + "buAutoNum") is not None)
                no_bullet = ppr is not None and ppr.find(_A + "buNone") is not None
                if kind in _TITLES:
                    yield True, text, f"{sid}:{i}"
                elif bullet or (kind in _BULLETED and not no_bullet):
                    yield False, "  " * (int(lvl) if lvl.isdigit() else 0) + "- " + text, f"{sid}:{i}"
                else:
                    yield False, text, f"{sid}:{i}"
        elif sh.tag == _P + "graphicFrame":
            tbl = sh.find(f".//{_A}tbl")
            sid = shape_id(sh)
            for r, tr in enumerate([] if tbl is None else tbl.findall(_A + "tr")):
                cells = [" ".join(t for t in map(a_text, tc.iter(_A + "p")) if t) for tc in tr.findall(_A + "tc")]
                if any(cells):
                    yield False, "| " + " | ".join(cells) + " |", f"{sid}:r{r}"


def _notes_lines(zf: zipfile.ZipFile, slide_part: str) -> list[str]:
    part = _related(_rels(zf, slide_part), "notesSlide")
    notes = _xml(zf, part, required=False) if part else None
    if notes is None:
        return []
    out = []
    for sp in notes.iter(_P + "sp"):
        body = sp.find(_P + "txBody")
        ph = placeholder(sp)
        if ph and ph[0] == "body" and body is not None:
            out += [t for t in map(a_text, body.findall(_A + "p")) if t]
    return out


def extract_pptx(data: bytes) -> Extracted:
    with _open_zip(data, LABELS["pptx"]) as zf:
        main, pres = _main_xml(zf, "ppt/presentation.xml", _P + "presentation", LABELS["pptx"])
        rels = _rels(zf, main)
        slides = [rels[s.get(_R_ID)][1] for s in pres.iter(_P + "sldId") if s.get(_R_ID) in rels]
        out = _Lines()
        for n, part in enumerate(slides, start=1):
            slide = _xml(zf, part)
            page = {"page": n}
            items = list(_shape_lines(slide.find(f"{_P}cSld/{_P}spTree")))
            title = next((text for is_title, text, _ in items if is_title), "")
            marker = SLIDE_MARKER.format(n=n, hidden=" · oculto" if slide.get("show") == "0" else "")
            out.add(marker, page, section=(1, f"Slide {n}" + (f" · {title}" if title else "")))
            for is_title, text, key in items:
                out.add(f"# {text}" if is_title else text, {"page": n, "key": key})
            notes = _notes_lines(zf, part)
            if notes:
                out.add(NOTES_LABEL, page)
                for text in notes:
                    out.add(text, {"page": n, "note": True})
            out.add("", page)
        return out.result()


# ------------------------------------------------------------------ HTML
_HTML_SKIP = {"script", "style", "template", "noscript", "title", "svg", "math", "iframe", "object", "select",
              "textarea"}
_HTML_HEADINGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
_HTML_BLOCKS = {"address", "article", "aside", "blockquote", "body", "caption", "center", "dd", "details", "dialog",
                "div", "dl", "dt", "fieldset", "figcaption", "figure", "footer", "form", "header", "hgroup", "hr",
                "html", "legend", "li", "main", "nav", "ol", "p", "pre", "section", "summary", "table", "tbody",
                "td", "tfoot", "th", "thead", "tr", "ul", *_HTML_HEADINGS}


def _span(line: int, data: str) -> tuple[int, int] | None:
    """Linhas do arquivo ocupadas pelo texto visível de ``data``, que começa na linha ``line``."""
    if not data.strip():
        return None
    lead = len(data) - len(data.lstrip())
    return line + data.count("\n", 0, lead), line + data.rstrip().count("\n")


class _Html(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out = _Lines()
        self.buf: list[str] = []
        self.src: list[int] = []  # linhas do arquivo do texto em ``buf``
        self.prefix = ""
        self.heading = 0
        self.skip = 0
        self.in_head = False
        self.pre = 0
        self.pre_start = False  # a quebra de linha logo depois de <pre> não conta (como no navegador)
        self.lists = 0
        self.row: list[str] | None = None
        self.row_src: list[int] = []
        self.cell: list[str] | None = None

    def _flush(self) -> None:
        raw = "".join(self.buf)
        text = raw.rstrip() if self.pre else " ".join(raw.split())
        info = {"src": [min(self.src), max(self.src)]} if self.src else {}
        if text:
            self.out.add(self.prefix + text, info, (self.heading, text) if self.heading else None)
            self.prefix = ""
        elif self.pre and self.src:
            self.out.add("", info)
        self.buf, self.src = [], []

    def _end_cell(self) -> None:
        if self.cell is not None:
            self.row = [] if self.row is None else self.row
            self.row.append(" ".join("".join(self.cell).split()))
            self.cell = None

    def _end_row(self) -> None:
        self._end_cell()
        if self.row and any(self.row):
            info = {"src": [min(self.row_src), max(self.row_src)]} if self.row_src else {}
            self.out.add("| " + " | ".join(self.row) + " |", info)
        self.row, self.row_src = None, []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag == "head":
            self.in_head = True
            return
        if tag == "body":
            self.in_head = False
        if tag in _HTML_SKIP:
            self.skip += 1
            return
        if self.skip or self.in_head:
            return
        if tag == "tr":
            self._end_row()
            self._flush()
            self.row = []
        elif tag in ("td", "th"):
            self._end_cell()
            if self.row is None:
                self._flush()
            self.cell = []
        elif self.cell is not None:
            if tag in _HTML_BLOCKS or tag == "br":
                self.cell.append(" ")  # dentro de uma célula, blocos só separam palavras
        elif tag in ("ul", "ol"):
            self._flush()
            self.lists += 1
        elif tag == "li":
            self._flush()
            self.prefix = "  " * max(0, self.lists - 1) + "- "
        elif tag in _HTML_HEADINGS:
            self._flush()
            self.heading = int(tag[1])
            self.prefix = "#" * self.heading + " "
        elif tag == "pre":
            self._flush()
            self.pre += 1
            self.pre_start = True
        elif tag == "br" or tag in _HTML_BLOCKS:
            self._flush()

    def handle_endtag(self, tag: str) -> None:
        if tag == "head":
            self.in_head = False
            return
        if tag in _HTML_SKIP:
            self.skip = max(0, self.skip - 1)
            return
        if self.skip or self.in_head:
            return
        if tag in ("td", "th"):
            self._end_cell()
        elif tag in ("tr", "table"):
            self._end_row()
        elif self.cell is not None:
            return
        elif tag in ("ul", "ol"):
            self._flush()
            self.lists = max(0, self.lists - 1)
        elif tag == "li":
            self._flush()
            self.prefix = ""
        elif tag in _HTML_HEADINGS:
            self._flush()
            self.heading, self.prefix = 0, ""
        elif tag == "pre":
            self._flush()
            self.pre = max(0, self.pre - 1)
        elif tag in _HTML_BLOCKS:
            self._flush()

    def handle_data(self, data: str) -> None:
        if self.skip or self.in_head:
            return
        line = self.getpos()[0]
        if self.cell is not None:
            self.cell.append(data)
            self.row_src += _span(line, data) or []
        elif not self.pre:
            self.buf.append(data)
            self.src += _span(line, data) or []
        else:
            if self.pre_start and data.startswith("\n"):
                data, line = data[1:], line + 1
            self.pre_start = False
            for i, part in enumerate(data.split("\n")):
                if i:
                    self._flush()
                self.buf.append(part)
                self.src.append(line + i)

    def finish(self) -> Extracted:
        self.close()
        self._end_row()
        self._flush()
        return self.out.result()


def extract_html(data: bytes) -> Extracted:
    parser = _Html()
    parser.feed(decode_text(data)[0])
    return parser.finish()
