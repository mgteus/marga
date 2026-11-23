"""Seções de documentos: marcadores, títulos pela tipografia e seções do fonte LaTeX.

Para conferir a detecção em PDFs reais (sem marcadores), aponte ``MARGA_PDF`` para eles
(separados por ``:``) e rode ``uv run pytest tests/test_outline.py -k real -s``.
"""

import os
from pathlib import Path

import pymupdf
import pytest

from marga.core import actions, outline
from marga.core.outline import Section
from marga.core.versions import latest_version

REAL_PDFS = [p for p in os.environ.get("MARGA_PDF", "").split(os.pathsep) if p]
BODY = "Texto corrido do documento, com palavras suficientes para formar uma linha inteira do corpo."


def _body(page: pymupdf.Page, y: float, lines: int) -> float:
    for _ in range(lines):
        page.insert_text((72, y), BODY, fontsize=11, fontname="helv")
        y += 15
    return y


def headings_pdf(path: Path) -> Path:
    """PDF sem marcadores, com cara de dissertação: folha de rosto, sumário, títulos numerados e ruído."""
    doc = pymupdf.open()
    rosto = doc.new_page()
    rosto.insert_text((90, 150), "Universidade Exemplo", fontsize=22, fontname="hebo")
    rosto.insert_text((230, 300), "Autora", fontsize=14, fontname="hebo")

    sumario = doc.new_page()
    sumario.insert_text((72, 90), "Sumário", fontsize=16, fontname="hebo")
    for i, (num, title, page) in enumerate([("1", "Introdução", "3"), ("1.1", "Motivação", "3"),
                                            ("2", "Métodos", "4"), ("2.1", "Coleta", "4")]):
        y = 130 + 22 * i
        sumario.insert_text((72, y), num, fontsize=11, fontname="hebo")
        sumario.insert_text((100, y), title, fontsize=11, fontname="hebo")
        sumario.insert_text((510, y), page, fontsize=11, fontname="hebo")

    intro = doc.new_page()
    intro.insert_text((72, 90), "1", fontsize=16, fontname="hebo")  # número e título separados, como no LaTeX
    intro.insert_text((95, 90), "Introdução", fontsize=16, fontname="hebo")
    y = _body(intro, 125, 12)
    intro.insert_text((72, y + 20), "1.1", fontsize=13, fontname="hebo")
    intro.insert_text((100, y + 20), "Motivação", fontsize=13, fontname="hebo")
    _body(intro, y + 50, 10)

    metodos = doc.new_page()
    metodos.insert_text((72, 90), "2", fontsize=16, fontname="hebo")
    metodos.insert_text((95, 90), "Métodos", fontsize=16, fontname="hebo")
    metodos.insert_text((72, 115), "[NOK] revisar esta parte", fontsize=8, fontname="hebo")  # nota pequena
    y = _body(metodos, 140, 8)
    metodos.insert_text((72, y + 10), "5 estados", fontsize=11, fontname="hebo")  # célula de tabela em negrito
    metodos.insert_text((72, y + 40), "2.1", fontsize=13, fontname="hebo")
    metodos.insert_text((100, y + 40), "Coleta", fontsize=13, fontname="hebo")
    _body(metodos, y + 70, 10)
    doc.save(path)
    return path


def test_headings_are_detected_from_typography(tmp_path):
    found = outline.detect_headings(headings_pdf(tmp_path / "tese.pdf"))
    assert [(s.level, s.title, s.page) for s in found] == [
        (1, "Sumário", 2),
        (1, "1 Introdução", 3),
        (2, "1.1 Motivação", 3),
        (1, "2 Métodos", 4),
        (2, "2.1 Coleta", 4),
    ]


def test_bookmarks_win_over_typography(demo_ws):
    path = demo_ws.root / "livro.pdf"
    doc = pymupdf.open()
    for _ in range(3):
        doc.new_page()
    doc.set_toc([[1, "Parte I", 1], [2, "Capítulo 1", 2], [1, "Parte II", 3]])
    doc.save(path)
    pdf_doc = actions.add_document(demo_ws, "livro.pdf")
    found = outline.for_pdf(demo_ws, latest_version(demo_ws, pdf_doc.id).files["livro.pdf"])
    assert found.source == "marcadores"
    assert [(s.level, s.title, s.page) for s in found.sections] == [(1, "Parte I", 1), (2, "Capítulo 1", 2),
                                                                   (1, "Parte II", 3)]
    assert (demo_ws.dir / "cache" / "outline").is_dir()


def test_latex_sections_follow_includes_comments_and_numbering():
    texts = {
        "proj/main.tex": "\\documentclass{article}\n\\begin{document}\n\\section*{Lista de siglas}\n"
                         "\\include{cap/intro}\n% \\include{cap/rascunho}\n\\input{cap/metodo.tex}\n"
                         "\\appendix\n\\include{cap/apendice}\n\\end{document}\n",
        "proj/cap/intro.tex": "\\section{Introdução}\\label{sec:intro}\nTexto.\n\\subsection{Motivação}\n",
        "proj/cap/rascunho.tex": "\\section{Rascunho}\n",
        "proj/cap/metodo.tex": "% \\section{Antigo}\n\\section[Curto]{Métodos e \\emph{dados}}\n"
                               "\\subsection*{Nota}\n\\subsection{Coleta}\n",
        "proj/cap/apendice.tex": "\\section{Questionário}\n\\subsection{Itens}\n",
    }
    found = outline.latex_sections(texts, "proj/main.tex", "proj")
    assert [(s.level, s.title, s.file, s.line) for s in found] == [
        (1, "Lista de siglas", "proj/main.tex", 3),
        (1, "1 Introdução", "proj/cap/intro.tex", 1),
        (2, "1.1 Motivação", "proj/cap/intro.tex", 3),
        (1, "2 Métodos e dados", "proj/cap/metodo.tex", 2),
        (2, "Nota", "proj/cap/metodo.tex", 3),
        (2, "2.1 Coleta", "proj/cap/metodo.tex", 4),
        (1, "A Questionário", "proj/cap/apendice.tex", 1),
        (2, "A.1 Itens", "proj/cap/apendice.tex", 2),
    ]


def test_navigation_helpers():
    secs = [Section(1, "A", page=1, y=80), Section(2, "A.1", page=2, y=300), Section(1, "B", page=4, y=80),
            Section(1, "C", page=6, y=400)]
    assert [outline.section_at(secs, p) for p in (1, 3, 5, 9)] == [0, 1, 2, 3]
    assert outline.contains(secs, 1, 3) and not outline.contains(secs, 0, 3)
    assert outline.page_span(secs, 0, 10) == (1, 3)  # B abre a página 4
    assert outline.page_span(secs, 2, 10) == (4, 6)  # C começa no meio da página 6
    assert outline.page_span(secs, 3, 10) == (6, 10)


@pytest.mark.skipif(not REAL_PDFS, reason="defina MARGA_PDF=/caminho/do/arquivo.pdf")
def test_real_pdfs_have_sections():
    for path in REAL_PDFS:
        found = outline.detect_headings(path) or outline.bookmarks(path)
        assert found, path
        print(f"\n{Path(path).name}: {len(found)} seções")
        for sec in found[:12]:
            print(f"  {'  ' * (sec.level - 1)}{sec.title} · p. {sec.page}")
