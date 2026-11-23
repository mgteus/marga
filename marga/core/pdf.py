"""PDF via PyMuPDF: render de páginas, palavras por região e extração de texto de PDFs avulsos.

Coordenadas sempre em pontos PDF com origem no canto superior esquerdo (igual ao SyncTeX).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pymupdf

from .workspace import Workspace, read_json, write_json

PAGE_MARKER = "=== página {n} ==="


def _open(path: str | Path) -> pymupdf.Document:
    return pymupdf.open(str(path), filetype="pdf")


def page_count(path: str | Path) -> int:
    with _open(path) as doc:
        return doc.page_count


def page_size(path: str | Path, page: int) -> tuple[float, float]:
    with _open(path) as doc:
        rect = doc[page - 1].rect
        return rect.width, rect.height


def render_png(path: str | Path, page: int, zoom: float = 2.0) -> bytes:
    with _open(path) as doc:
        pix = doc[page - 1].get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
        return pix.tobytes("png")


def _join_words(words: list[tuple]) -> str:
    """Junta palavras na ordem de leitura, desfazendo a hifenização de fim de linha."""
    out = ""
    prev_line = None
    for w in words:
        text, line_key = w[4], (w[5], w[6])
        if not out:
            out = text
        elif line_key == prev_line:
            out += " " + text
        elif out.endswith("-") and len(out) > 1 and out[-2].isalpha():
            out = out[:-1] + text
        else:
            out += " " + text
        prev_line = line_key
    return out


def _words(path: str | Path, page: int) -> list[tuple]:
    with _open(path) as doc:
        return doc[page - 1].get_text("words", sort=True)


def text_in_rect(path: str | Path, page: int, rect: tuple[float, float, float, float]) -> str:
    x0, y0, x1, y1 = rect
    words = [w for w in _words(path, page)
             if x0 <= (w[0] + w[2]) / 2 <= x1 and y0 <= (w[1] + w[3]) / 2 <= y1]
    return _join_words(words)


def text_near(path: str | Path, page: int, x: float, y: float, radius: int = 4) -> str:
    """A palavra sob o ponto (ou a mais próxima) e até ``radius`` vizinhas na mesma linha."""
    words = _words(path, page)
    if not words:
        return ""

    def dist(w):
        dx = 0 if w[0] <= x <= w[2] else min(abs(x - w[0]), abs(x - w[2]))
        dy = 0 if w[1] <= y <= w[3] else min(abs(y - w[1]), abs(y - w[3]))
        return dx * dx + dy * dy * 4

    best = min(range(len(words)), key=lambda i: dist(words[i]))
    key = (words[best][5], words[best][6])
    same_line = [i for i, w in enumerate(words) if (w[5], w[6]) == key]
    pos = same_line.index(best)
    chosen = same_line[max(0, pos - radius) : pos + radius + 1]
    return _join_words([words[i] for i in chosen])


def search_rects(path: str | Path, page: int, text: str) -> list[tuple[float, float, float, float]]:
    if not text.strip():
        return []
    with _open(path) as doc:
        return [tuple(r) for r in doc[page - 1].search_for(text)]


# ------------------------------------------------------------- PDF avulso
@dataclass
class PdfText:
    text: str
    lines: list[dict] = field(default_factory=list)  # por linha: {"page": n, "bbox": [x0,y0,x1,y1] | None}

    def page_of_line(self, line: int) -> int | None:
        if 1 <= line <= len(self.lines):
            return self.lines[line - 1]["page"]
        return None

    def lines_in_rect(self, page: int, rect: tuple[float, float, float, float]) -> list[int]:
        x0, y0, x1, y1 = rect
        out = []
        for i, info in enumerate(self.lines, start=1):
            box = info.get("bbox")
            if info["page"] != page or not box:
                continue
            if box[0] < x1 and box[2] > x0 and box[1] < y1 and box[3] > y0:
                out.append(i)
        return out

    def line_at(self, page: int, x: float, y: float) -> int | None:
        best, best_d = None, None
        for i, info in enumerate(self.lines, start=1):
            box = info.get("bbox")
            if info["page"] != page or not box:
                continue
            dy = 0 if box[1] <= y <= box[3] else min(abs(y - box[1]), abs(y - box[3]))
            dx = 0 if box[0] <= x <= box[2] else min(abs(x - box[0]), abs(x - box[2]))
            d = dy * 4 + dx
            if best_d is None or d < best_d:
                best, best_d = i, d
        return best

    def line_boxes(self, page: int, first: int, last: int) -> list[tuple[float, float, float, float]]:
        out = []
        for i in range(max(1, first), min(len(self.lines), last) + 1):
            info = self.lines[i - 1]
            if info["page"] == page and info.get("bbox"):
                out.append(tuple(info["bbox"]))
        return out


def extract_text(path: str | Path) -> PdfText:
    lines: list[str] = []
    info: list[dict] = []
    with _open(path) as doc:
        for pno, page in enumerate(doc, start=1):
            lines.append(PAGE_MARKER.format(n=pno))
            info.append({"page": pno, "bbox": None})
            for block in page.get_text("dict", sort=True)["blocks"]:
                if block.get("type") != 0:
                    continue
                added = False
                for ln in block["lines"]:
                    text = "".join(span["text"] for span in ln["spans"]).rstrip()
                    if not text.strip():
                        continue
                    lines.append(text)
                    info.append({"page": pno, "bbox": [round(v, 2) for v in ln["bbox"]]})
                    added = True
                if added:
                    lines.append("")
                    info.append({"page": pno, "bbox": None})
    return PdfText("\n".join(lines), info)


def pdf_text(ws: Workspace, sha: str) -> PdfText:
    """Texto extraído de um PDF guardado como blob (com cache em ``.marga/cache``)."""
    from .versions import blob_path

    cache = ws.dir / "cache" / "pdftext" / f"{sha}.json"
    data = read_json(cache, None)
    if data is None:
        extracted = extract_text(blob_path(ws, sha))
        write_json(cache, {"text": extracted.text, "lines": extracted.lines})
        return extracted
    return PdfText(data["text"], data["lines"])
