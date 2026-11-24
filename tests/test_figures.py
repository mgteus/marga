"""Figuras no PDF: caixas do SyncTeX para \\includegraphics, seleção dentro de imagens e recorte à página."""

from pathlib import Path

import pymupdf
import pytest

from marga.core import actions, latex, pdf, synctex
from marga.core.synctex import SyncBox
from marga.core.versions import latest_version
from marga.core.workspace import Workspace

needs_synctex = pytest.mark.skipif(not (latex.can_compile() and synctex.available()), reason="synctex ausente")

# Números reais de uma dissertação (p. 47): duas imagens lado a lado, escaladas para 0.48\linewidth.
LEFT, RIGHT = (85.1, 72.3, 296.0, 266.9), (299.3, 72.3, 510.2, 266.9)


def _box(x0: float, y0: float, x1: float, y1: float) -> SyncBox:
    return SyncBox(page=47, h=x0, v=y1, width=x1 - x0, height=y1 - y0)


def test_image_boxes_become_the_drawn_image():
    # o SyncTeX mede cada imagem no tamanho natural: as caixas sobem acima da página e passam da borda direita
    left, right = _box(85.0, -137.0, 553.0, 267.0), _box(299.0, -137.0, 767.0, 267.0)
    line = _box(78.0, -137.0, 767.0, 267.0)  # a linha inteira (\begin{figure}, \centering)
    caption = _box(78.0, 277.0, 517.0, 296.0)
    images = [LEFT, RIGHT]
    assert synctex.fit_images([left, left, line], images) == [LEFT]
    assert synctex.fit_images([right], images) == [RIGHT]
    assert synctex.fit_images([line], images) == [LEFT, RIGHT]
    assert synctex.fit_images([left, caption], images) == [LEFT, caption.rect]
    assert synctex.fit_images([left], []) == [left.rect]


def test_marks_are_clipped_to_the_page():
    assert pdf.clip_rect((78.0, -137.0, 767.0, 267.0), 595.3, 841.9) == (78.0, 0.0, 595.3, 267.0)
    assert pdf.clip_rect((600.0, 10.0, 700.0, 20.0), 595.3, 841.9) is None
    assert pdf.clip_rect((10.0, 10.0, 20.0, 20.0), 595.3, 841.9) == (10.0, 10.0, 20.0, 20.0)


def test_figure_lines_skip_comments():
    text = ("\\begin{figure}\n  \\includegraphics{a.png}\n  % \\includegraphics{b.png}\n"
            "  50\\% \\includegraphics{c.png}\n\\end{figure}\n")
    assert synctex.figure_lines(text) == [2, 4]


FIGURE_TEX = r"""\documentclass{article}
\usepackage{graphicx}
\begin{document}
Texto antes da figura.

\begin{figure}[h]
  \centering
  \includegraphics[width=0.4\linewidth]{a.png}
  \includegraphics[width=0.4\linewidth]{b.png}
  \caption{Duas imagens lado a lado.}
\end{figure}

Texto depois da figura.
\end{document}
"""


@needs_synctex
def test_point_inside_an_image_maps_to_its_includegraphics(tmp_path):
    proj = tmp_path / "ws" / "fig"
    proj.mkdir(parents=True)
    for name, shade in (("a.png", 90), ("b.png", 200)):
        pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 400, 300), False)
        pix.clear_with(shade)
        pix.save(proj / name)
    (proj / "main.tex").write_text(FIGURE_TEX, encoding="utf-8")
    ws = Workspace.open(tmp_path / "ws")
    doc = actions.add_document(ws, "fig")
    result = latex.compile_version(ws, doc, latest_version(ws, doc.id))
    assert result.ok, result.errors
    images = sorted(pdf.image_rects(result.pdf, 1))
    assert len(images) == 2
    src = Path(result.src_dir) / "main.tex"
    for img, line in zip(images, (8, 9)):
        x, y = (img[0] + img[2]) / 2, (img[1] + img[3]) / 2
        assert synctex.figure_at(result.pdf, 1, x, y, src, FIGURE_TEX, result.src_dir) == line
        boxes = [b for b in synctex.forward(result.pdf, src, line, result.src_dir) if b.page == 1]
        assert synctex.fit_images(boxes, images) == [img]
