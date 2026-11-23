"""Importação de .zip (pacote do Overleaf), uploads e caminhos do disco.

O teste com um .zip real roda só quando ``MARGA_ZIP`` aponta para ele, por exemplo:

    MARGA_ZIP="$HOME/Downloads/DissertacaoMestrado (4).zip" uv run pytest tests/test_zip_import.py -k real -s
"""

import io
import os
import zipfile
from pathlib import Path

import pymupdf
import pytest

from marga.core import actions, latex, outline, synctex
from marga.core.versions import get_version, latest_version
from marga.core.workspace import MargaError, Workspace

needs_tex = pytest.mark.skipif(not latex.can_compile(), reason="latexmk ausente")
needs_synctex = pytest.mark.skipif(not (latex.can_compile() and synctex.available()), reason="synctex ausente")
REAL_ZIP = os.environ.get("MARGA_ZIP")

MAIN = r"""\documentclass[a4paper]{article}
\usepackage[utf8]{inputenc}
\usepackage[T1]{fontenc}
\usepackage[portuguese]{babel}
\usepackage{graphicx}
\usepackage{hyperref}
\graphicspath{{figuras/}}
\begin{document}
\tableofcontents
\include{capitulos/introducao}
% \include{capitulos/rascunho}
\include{capitulos/metodos}
\bibliographystyle{plain}
\bibliography{refs}
\end{document}
"""
INTRO = r"""\section{Introdução}\label{sec:intro}
Este projeto testa o Marga com um pacote do Overleaf \cite{silva2020}.
\subsection{Motivação}
Texto da motivação.
"""
METODOS = r"""\section{Métodos}
A Figura~\ref{fig:grafico} mostra o resultado.
\begin{figure}[h]
\centering
\includegraphics[width=0.3\linewidth]{grafico.png}
\caption{Um gráfico de teste.}\label{fig:grafico}
\end{figure}
\subsection{Coleta de dados}
Foram coletados 1.200 registros.
"""
REFS = "@article{silva2020, author={Silva, Ana}, title={Um estudo}, journal={Revista}, year={2020}}\n"
TEXT_FILES = ("main.tex", "refs.bib", "capitulos/introducao.tex", "capitulos/metodos.tex", "capitulos/rascunho.tex")


def _png() -> bytes:
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 60, 40), False)
    pix.clear_with(180)
    return pix.tobytes("png")


def _pdf(text: str = "Olá") -> bytes:
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), text)
    return doc.tobytes()


def overleaf_zip(path: Path, top: str | None = None, extra: dict[str, bytes | str] | None = None) -> Path:
    """Um projeto como o Overleaf baixa: main.tex na raiz, capítulos em subpasta, figuras e .bib."""
    prefix = f"{top}/" if top else ""
    files = {"main.tex": MAIN, "capitulos/introducao.tex": INTRO, "capitulos/metodos.tex": METODOS,
             "capitulos/rascunho.tex": "\\section{Rascunho}\n", "refs.bib": REFS, "figuras/grafico.png": _png()}
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in files.items():
            zf.writestr(prefix + name, data)
        zf.writestr("__MACOSX/._main.tex", b"lixo do Finder")
        zf.writestr(prefix + ".DS_Store", b"lixo do Finder")
        for name, data in (extra or {}).items():
            zf.writestr(name, data)
    return path


@pytest.fixture
def ws(tmp_path) -> Workspace:
    (tmp_path / "ws").mkdir()
    return Workspace.open(tmp_path / "ws")


def test_overleaf_zip_becomes_a_latex_document(ws, tmp_path):
    doc = actions.import_zip(ws, overleaf_zip(tmp_path / "Projeto Overleaf.zip"), "Projeto Overleaf.zip")
    assert (doc.kind, doc.name, doc.engine) == ("latex", "Projeto Overleaf", "pdflatex")
    assert doc.root == "uploads/Projeto Overleaf" and doc.main == "uploads/Projeto Overleaf/main.tex"
    root = ws.abspath(doc.root)
    assert (root / "figuras" / "grafico.png").read_bytes() == _png()
    assert not (root / ".DS_Store").exists() and not (ws.root / "uploads" / "__MACOSX").exists()
    # versiona os textos (inclusive o capítulo comentado, que continua sendo do projeto); figuras ficam no disco
    assert set(latest_version(ws, doc.id).files) == {f"{doc.root}/{p}" for p in TEXT_FILES}


def test_zip_inside_one_folder_is_flattened_and_keeps_the_typed_name(ws, tmp_path):
    data = overleaf_zip(tmp_path / "tese.zip", top="minha-tese").read_bytes()
    doc = actions.import_upload(ws, "tese.zip", io.BytesIO(data), name="Tese (v3)")
    assert doc.name == "Tese (v3)" and doc.root == "uploads/tese" and doc.main == "uploads/tese/main.tex"
    again = actions.import_upload(ws, "tese.zip", data)
    assert again.root == "uploads/tese-2" and again.id != doc.id


def test_unsafe_zip_is_refused_without_leftovers(ws, tmp_path):
    bad = overleaf_zip(tmp_path / "mal.zip", extra={"../fora.tex": "\\documentclass{article}"})
    with pytest.raises(MargaError, match="inseguro"):
        actions.import_zip(ws, bad, "mal.zip")
    assert not (tmp_path / "fora.tex").exists() and not (ws.root / "uploads" / "mal").exists() and not ws.docs()


def test_zip_without_anything_to_review_is_refused(ws, tmp_path):
    path = tmp_path / "figuras.zip"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("a.png", _png())
    with pytest.raises(MargaError, match="documentclass"):
        actions.import_zip(ws, path, "figuras.zip")
    assert not (ws.root / "uploads" / "figuras").exists()


def test_upload_uses_the_typed_name(ws):
    """Regressão: o nome digitado ficava de fora quando o documento vinha de um arquivo enviado."""
    doc = actions.import_upload(ws, "DissertacaoMestrado-2.pdf", io.BytesIO(_pdf()), name="Dissertação v2")
    assert (doc.name, doc.kind, doc.root) == ("Dissertação v2", "pdf", "uploads/DissertacaoMestrado-2.pdf")
    again = actions.import_upload(ws, "DissertacaoMestrado-2.pdf", _pdf())
    assert again.root == "uploads/DissertacaoMestrado-2-2.pdf" and again.name == "DissertacaoMestrado-2-2.pdf"
    with pytest.raises(MargaError, match="não suportado"):
        actions.import_upload(ws, "planilha.xlsx", b"x")


def test_import_path_accepts_a_path_pasted_from_the_terminal(ws, tmp_path):
    src = tmp_path / "Downloads" / "Dissertacao Mestrado (2).pdf"
    src.parent.mkdir()
    src.write_bytes(_pdf("versão 2"))
    escaped = str(src).replace(" ", "\\ ").replace("(", "\\(").replace(")", "\\)")
    doc = actions.import_path(ws, escaped, name="v2")
    assert doc.root == "uploads/Dissertacao Mestrado (2).pdf" and doc.name == "v2"
    assert ws.abspath(doc.root).read_bytes() == src.read_bytes()
    zipped = actions.import_path(ws, f'"{overleaf_zip(tmp_path / "Downloads" / "proj.zip")}"')
    assert zipped.kind == "latex" and zipped.root == "uploads/proj"
    with pytest.raises(MargaError, match="fora do workspace"):
        actions.import_path(ws, str(tmp_path / "Downloads"))
    with pytest.raises(MargaError, match="Não encontrei"):
        actions.import_path(ws, str(tmp_path / "nada.zip"))


@needs_synctex
def test_overleaf_project_compiles_with_figures_bibliography_and_sections(ws, tmp_path):
    doc = actions.import_zip(ws, overleaf_zip(tmp_path / "projeto.zip"), "projeto.zip")
    version = latest_version(ws, doc.id)
    result = latex.compile_version(ws, doc, version)
    assert result.ok and result.has_pdf, (result.errors, result.output_tail)
    with pymupdf.open(result.pdf) as pdf:
        text = "\n".join(page.get_text() for page in pdf)
    assert "1.200 registros" in text and "Silva" in text and "Rascunho" not in text

    found = outline.for_build(ws, doc, version, result)
    assert found.source == "fonte"
    assert [s.title for s in found.sections] == ["1 Introdução", "1.1 Motivação", "2 Métodos", "2.1 Coleta de dados"]
    metodos = found.sections[2]
    assert (metodos.file, metodos.line) == (f"{doc.root}/capitulos/metodos.tex", 1)
    assert found.sections[0].page < metodos.page  # \include abre página nova
    with pymupdf.open(result.pdf) as pdf:
        assert pdf[metodos.page - 1].get_images(), "a figura deveria estar na página da seção Métodos"


@needs_tex
def test_old_version_of_a_project_reuses_figures_without_touching_the_work_files(ws, tmp_path):
    doc = actions.import_zip(ws, overleaf_zip(tmp_path / "projeto.zip"), "projeto.zip")
    work = ws.abspath(f"{doc.root}/capitulos/metodos.tex")
    work.write_text(work.read_text(encoding="utf-8").replace("1.200", "1.250"), encoding="utf-8")
    actions.register_version(ws, doc.id)

    old = latex.compile_version(ws, doc, get_version(ws, doc.id, 1))
    assert old.ok, old.errors
    figure = Path(old.src_dir) / "figuras" / "grafico.png"
    assert figure.stat().st_ino == ws.abspath(f"{doc.root}/figuras/grafico.png").stat().st_ino  # hard link
    assert "1.250" in work.read_text(encoding="utf-8")  # o arquivo de trabalho não muda
    with pymupdf.open(old.pdf) as pdf:
        assert "1.200 registros" in "\n".join(page.get_text() for page in pdf)


@pytest.mark.skipif(not REAL_ZIP or not latex.can_compile(), reason="defina MARGA_ZIP=/caminho/do/projeto.zip")
def test_real_overleaf_zip(ws):
    doc = actions.import_path(ws, REAL_ZIP)
    assert doc.kind == "latex", doc
    version = latest_version(ws, doc.id)
    result = latex.compile_version(ws, doc, version, timeout=900)
    problems = [f"{e.location} {e.message}" for e in result.errors][:8]
    assert result.ok and result.has_pdf, problems
    found = outline.for_build(ws, doc, version, result)
    assert found.sections and all(s.page for s in found.sections), found
    print(f"\n{doc.label}: {len(version.files)} arquivos de texto · main {doc.main} · {doc.engine} · "
          f"compilou em {result.duration:.0f} s · {len(result.warnings)} aviso(s) · "
          f"{len(found.sections)} seções ({found.source_label})")
