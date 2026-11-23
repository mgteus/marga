"""Integração com TeX Live (pulados quando latexmk/synctex não estão instalados)."""

from pathlib import Path

import pymupdf
import pytest

from marga.core import actions, latex, pdf, synctex
from marga.core.versions import get_version, latest_version, version_text

needs_tex = pytest.mark.skipif(not latex.can_compile(), reason="latexmk ausente")
needs_synctex = pytest.mark.skipif(not (latex.can_compile() and synctex.available()), reason="synctex ausente")


def _pdf_text(path: str) -> str:
    with pymupdf.open(path) as doc:
        return "\n".join(page.get_text() for page in doc)


@needs_tex
def test_two_documents_compile_in_parallel(demo_ws):
    a = actions.add_document(demo_ws, "artigo")
    b = actions.add_document(demo_ws, "suplemento")
    results = latex.compile_many(demo_ws, [(a, latest_version(demo_ws, "A")), (b, latest_version(demo_ws, "B"))])
    assert all(isinstance(r, latex.CompileResult) and r.ok and r.has_pdf for r in results), results
    assert results[0].pdf != results[1].pdf
    assert any("sec:resultados" in w.message for w in results[0].warnings)
    assert "1.200 participantes" in _pdf_text(results[1].pdf)


@needs_tex
def test_errors_point_to_the_subfile_line(demo_ws):
    a = actions.add_document(demo_ws, "artigo")
    path = demo_ws.root / "artigo" / "sections" / "metodos.tex"
    path.write_text(path.read_text(encoding="utf-8") + "\n\\comandoinexistente\n", encoding="utf-8")
    actions.register_version(demo_ws, "A")
    result = latex.compile_version(demo_ws, a, latest_version(demo_ws, "A"))
    assert not result.ok
    assert any(e.file == "artigo/sections/metodos.tex" and e.line == 11 for e in result.errors), result.errors


@needs_tex
def test_old_version_compiles_from_snapshot(demo_ws):
    a = actions.add_document(demo_ws, "artigo")
    path = demo_ws.root / "artigo" / "sections" / "introducao.tex"
    path.write_text(path.read_text(encoding="utf-8").replace("em 2019 com", "em 2021 com"), encoding="utf-8")
    actions.register_version(demo_ws, "A")
    old = latex.compile_version(demo_ws, a, get_version(demo_ws, "A", 1))
    new = latex.compile_version(demo_ws, a, get_version(demo_ws, "A", 2))
    assert old.ok and new.ok
    assert "/.marga/build/A/v1/src" in old.src_dir and new.src_dir == str(demo_ws.root / "artigo")
    assert "coletados em 2019" in _pdf_text(old.pdf) and "coletados em 2021" in _pdf_text(new.pdf)


@needs_synctex
def test_synctex_roundtrip(demo_ws):
    a = actions.add_document(demo_ws, "artigo")
    result = latex.compile_version(demo_ws, a, latest_version(demo_ws, "A"))
    src = Path(result.src_dir) / "sections" / "introducao.tex"
    boxes = synctex.forward(result.pdf, src, 6, result.src_dir)
    assert boxes
    box = boxes[0]
    hits = synctex.inverse(result.pdf, box.page, box.h + 5, box.v - box.height / 2, cwd=result.src_dir)
    assert hits and hits[0][0].name == "introducao.tex" and hits[0][1] == 6
    assert "Neste trabalho" in pdf.text_in_rect(result.pdf, box.page, box.rect)


def test_standalone_pdf_text(demo_ws):
    c = actions.add_document(demo_ws, "parecer.pdf")
    assert c.kind == "pdf"
    text = version_text(demo_ws, c, latest_version(demo_ws, c.id), "parecer.pdf")
    assert text.startswith("=== página 1 ===") and "Parecer do revisor" in text
