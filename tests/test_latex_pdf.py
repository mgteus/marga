"""Integração com TeX Live (pulados quando latexmk/synctex não estão instalados)."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pymupdf
import pytest

from marga.core import actions, latex, pdf, synctex
from marga.core.versions import get_version, latest_version, version_text
from marga.core.workspace import MargaError, Workspace

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
    # toda versão compila numa cópia própria, até a mais recente: editar a pasta durante a compilação não a afeta
    assert "/.marga/build/A/v1/src" in old.src_dir and "/.marga/build/A/v2/src" in new.src_dir
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


THREE_STAGES_TEX = r"""\documentclass{article}
\usepackage[T1]{fontenc}
\usepackage{graphicx}
\begin{document}
\section{Introdução}\label{sec:intro}
Veja a seção~\ref{sec:fim}, a figura~\ref{fig:a} e o trabalho de~\cite{knuth}.
\begin{figure}[h]
  \centering
  \includegraphics[width=0.5\linewidth]{a.png}
  \caption{Uma figura.}\label{fig:a}
\end{figure}
\section{Fim}\label{sec:fim}
Volte à seção~\ref{sec:intro}.
\bibliographystyle{plain}
\bibliography{refs}
\end{document}
"""
REFS_BIB = "@book{knuth, author = {Donald Knuth}, title = {The TeXbook}, year = {1984}, publisher = {AW}}\n"


def _figure_project(tmp_path):
    """Documento que pede várias passadas (referências cruzadas e bibtex) e tem uma figura PNG com alfa."""
    proj = tmp_path / "ws" / "tese"
    proj.mkdir(parents=True)
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 600, 400), True)
    pix.clear_with(150)
    pix.save(proj / "a.png")
    (proj / "main.tex").write_text(THREE_STAGES_TEX, encoding="utf-8")
    (proj / "refs.bib").write_text(REFS_BIB, encoding="utf-8")
    ws = Workspace.open(tmp_path / "ws")
    doc = actions.add_document(ws, "tese")
    return ws, doc, latest_version(ws, doc.id)


@needs_tex
def test_compile_shows_a_preview_without_figures_then_the_final_pdf(tmp_path):
    ws, doc, version = _figure_project(tmp_path)
    bdir = latex.build_dir(ws, doc.id, version.n)
    seen = []

    def on_preview(preview):
        # a prévia sai antes do fim: ainda não há resultado, e o PDF dela já está no lugar
        seen.append((preview, (bdir / "result.json").exists(), Path(preview.pdf).exists()))

    result = latex.compile_version(ws, doc, version, on_preview=on_preview)
    assert result.ok, result.errors
    assert set(result.stages) == {"rascunho", "previa", "final"} and not result.preview
    [(preview, had_result, had_pdf)] = seen
    assert preview.preview and not had_result and had_pdf
    assert preview.pdf == str(bdir / "previa.pdf") and preview.src_dir == result.src_dir
    with pymupdf.open(preview.pdf) as pre, pymupdf.open(result.pdf) as fin:
        assert pre.page_count == fin.page_count
        assert not pre[0].get_images() and fin[0].get_images()  # na prévia, a figura é só uma moldura
        for pdf_doc in (pre, fin):  # as passadas em rascunho resolveram referências e bibliografia
            text = "\n".join(page.get_text() for page in pdf_doc)
            assert "??" not in text and "Knuth" in text
    assert latex.load_pdf(ws, doc.id, version.n).pdf == result.pdf
    assert not (bdir / latex.LOCK_FILE).exists()


@needs_synctex
def test_preview_keeps_synctex(tmp_path):
    ws, doc, version = _figure_project(tmp_path)
    latex.compile_version(ws, doc, version)
    preview = latex.load_preview(ws, doc.id, version.n)
    src = Path(preview.src_dir) / "main.tex"
    boxes = synctex.forward(preview.pdf, src, 6, preview.src_dir)
    assert boxes
    box = boxes[0]
    hits = synctex.inverse(preview.pdf, box.page, box.h + 5, box.v - box.height / 2, cwd=preview.src_dir)
    assert hits and hits[0][0].name == "main.tex" and hits[0][1] == 6


@needs_tex
def test_load_pdf_falls_back_to_the_preview_and_then_to_the_errors(tmp_path):
    ws, doc, version = _figure_project(tmp_path)
    result = latex.compile_version(ws, doc, version)
    Path(result.pdf).unlink()
    shown = latex.load_pdf(ws, doc.id, version.n)
    assert shown.preview and shown.pdf.endswith("previa.pdf")
    Path(shown.pdf).unlink()
    shown = latex.load_pdf(ws, doc.id, version.n)
    assert not shown.preview and not shown.has_pdf


@needs_tex
def test_recompiling_starts_clean_even_from_a_broken_aux(demo_ws):
    """O caso da v5 da dissertação: um main.aux estragado prendia a versão em erro ("Nothing to do")."""
    a = actions.add_document(demo_ws, "artigo")
    version = latest_version(demo_ws, "A")
    first = latex.compile_version(demo_ws, a, version)
    assert first.ok
    out = latex.build_dir(demo_ws, "A", version.n) / "out"
    (out / "main.aux").write_text("\\relax\n\\newlabel{sec:intro}{{1}{1}\n\\@writefile{toc}{}}}\n", encoding="utf-8")
    (out / "sobra-da-anterior.txt").write_text("x", encoding="utf-8")
    again = latex.compile_version(demo_ws, a, version, force=True)
    assert again.ok and not again.errors, again.errors
    assert not (out / "sobra-da-anterior.txt").exists()  # o outdir começou vazio


@needs_tex
def test_an_interrupted_compile_keeps_no_partial_pdf(demo_ws):
    a = actions.add_document(demo_ws, "artigo")
    result = latex.compile_version(demo_ws, a, latest_version(demo_ws, "A"), timeout=0.05)
    assert result.timed_out and not result.ok and result.pdf is None
    assert result.errors[0].message.startswith("Tempo esgotado")


@needs_tex
def test_a_version_compiles_once_at_a_time(demo_ws):
    a = actions.add_document(demo_ws, "artigo")
    version = latest_version(demo_ws, "A")
    bdir = latex.build_dir(demo_ws, "A", version.n)
    bdir.mkdir(parents=True)
    lock = bdir / latex.LOCK_FILE
    # trava de outro servidor do Marga, vivo: a compilação recusa
    lock.write_text(json.dumps({"pid": os.getppid(), "pgid": None}), encoding="utf-8")
    with pytest.raises(MargaError, match="já está sendo compilada por outro processo"):
        latex.compile_version(demo_ws, a, version)
    # trava de um servidor que morreu: o latexmk órfão dele (neste outdir) é encerrado, um processo alheio não
    dead = subprocess.Popen(["true"])
    dead.wait()
    sleeper = [sys.executable, "-c", "import time; time.sleep(60)"]
    orphan = subprocess.Popen([*sleeper, str(bdir / "out")], start_new_session=True)
    other = subprocess.Popen([*sleeper, "outra-pasta"], start_new_session=True)
    try:
        lock.write_text(json.dumps({"pid": dead.pid, "pgid": orphan.pid}), encoding="utf-8")
        assert latex.compile_version(demo_ws, a, version).ok
        assert orphan.wait(timeout=5) is not None
        lock.write_text(json.dumps({"pid": dead.pid, "pgid": other.pid}), encoding="utf-8")
        assert latex.compile_version(demo_ws, a, version, force=True).ok
        assert other.poll() is None
    finally:
        for proc in (orphan, other):
            proc.kill()
            proc.wait()
    assert not lock.exists()
