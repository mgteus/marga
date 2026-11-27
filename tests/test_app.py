"""Smoke test do app Streamlit (renderiza todas as abas sem exceções)."""

import zipfile
from pathlib import Path

import pymupdf
from streamlit.testing.v1 import AppTest

from marga.core import actions, export, folders
from marga.core.anchors import anchor_from_offsets
from marga.core.versions import latest_version, load_versions, version_text
from marga.core.workspace import Workspace

APP = str(Path(__file__).resolve().parents[1] / "marga" / "app.py")


def test_app_renders_every_section(demo_ws, monkeypatch):
    actions.add_document(demo_ws, "artigo")
    actions.add_document(demo_ws, "suplemento")
    actions.add_document(demo_ws, "parecer.pdf")
    export.export_round(demo_ws, "teste")
    monkeypatch.setenv("MARGA_WORKSPACE", str(demo_ws.root))
    at = AppTest.from_file(APP, default_timeout=180)
    at.run()
    assert not at.exception, at.exception
    for section in ("Links", "Comparar versões", "Rodadas", "Revisão"):
        at.session_state["nav"] = section
        at.run()
        assert not at.exception, (section, at.exception)



def test_right_column_turns_off_and_comes_back_as_it_was(demo_ws, monkeypatch):
    actions.add_document(demo_ws, "artigo")
    parecer = actions.add_document(demo_ws, "parecer.pdf")
    v = latest_version(demo_ws, parecer.id)
    text = version_text(demo_ws, parecer, v, parecer.root)
    actions.create_annotation(demo_ws, parecer.id, anchor_from_offsets(text, parecer.root, v.n, 0, 12, 1),
                              "corrigir", "teste")
    monkeypatch.setenv("MARGA_WORKSPACE", str(demo_ws.root))
    at = AppTest.from_file(APP, default_timeout=180)
    at.session_state["R_doc"] = "A"  # a direita mostra o artigo (o padrão seria o parecer)
    at.session_state["L_doc"] = parecer.id
    at.session_state[f"L_mode_{parecer.id}"] = "PDF"
    at.run()

    def doc_pickers():
        return [box.key for box in at.selectbox if box.key in ("L_doc", "R_doc")]

    def notes_headers():
        return [m.value for m in at.markdown if m.value.startswith("**Anotações de")]

    assert doc_pickers() == ["L_doc", "R_doc"] and at.selectbox(key="L_zoom").value == "width"
    at.toggle(key="show_right").set_value(False).run()
    assert not at.exception, at.exception
    assert doc_pickers() == ["L_doc"]  # um documento só, com a seleção e as anotações ao lado
    assert notes_headers() == [f"**Anotações de {parecer.id}** · 1 aberta(s) de 1"]
    at.selectbox(key="L_zoom").select("page").run()  # página inteira na altura da tela
    assert not at.exception, at.exception
    at.toggle(key="show_right").set_value(True).run()
    assert doc_pickers() == ["L_doc", "R_doc"] and at.selectbox(key="R_doc").value == "A"
    assert at.selectbox(key="L_zoom").value == "page"

def test_sections_navigate_pdf_and_filter_its_text(demo_ws, monkeypatch):
    path = demo_ws.root / "livro.pdf"
    pdf = pymupdf.open()
    for n in range(1, 6):
        pdf.new_page().insert_text((72, 90), f"Conteúdo da página {n}")
    pdf.set_toc([[1, "Parte I", 1], [2, "Capítulo 1", 2], [1, "Parte II", 4]])
    pdf.save(path)
    doc = actions.add_document(demo_ws, "livro.pdf")
    monkeypatch.setenv("MARGA_WORKSPACE", str(demo_ws.root))
    at = AppTest.from_file(APP, default_timeout=180)
    at.session_state["L_doc"] = doc.id
    at.session_state[f"L_mode_{doc.id}"] = "PDF"
    at.run()
    assert not at.exception, at.exception

    picker = at.selectbox(key=f"L_sec_{doc.id}_1")
    assert picker.value == 0  # página 1 está na Parte I
    picker.select(2).run()  # Parte II
    assert at.session_state[f"L_page_{doc.id}_1"] == 4
    assert at.selectbox(key=f"L_sec_{doc.id}_1").value == 2

    at.session_state[f"L_mode_{doc.id}"] = "Fonte"
    at.run()
    trecho = at.selectbox(key=f"L_rng_{doc.id}_1")
    assert trecho.options[0].startswith("Documento inteiro")
    trecho.select("s1").run()  # Capítulo 1: páginas 2–4 (a Parte II começa no meio da página 4)
    assert not at.exception, at.exception


def _project_zip(path: Path, intro: str) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("main.tex", "\\documentclass{article}\n\\begin{document}\n\\input{intro}\n\\end{document}\n")
        zf.writestr("intro.tex", intro)
    return path


def test_sidebar_registers_a_zip_from_disk_as_the_next_version(demo_ws, monkeypatch, tmp_path):
    doc = actions.import_zip(demo_ws, _project_zip(tmp_path / "proj.zip", "Versão um.\n"), "proj.zip")
    newer = _project_zip(tmp_path / "proj (2).zip", "Versão dois.\n")
    monkeypatch.setenv("MARGA_WORKSPACE", str(demo_ws.root))
    at = AppTest.from_file(APP, default_timeout=180)
    at.session_state["add_from"] = "disco"
    at.run()
    at.text_input(key="add_disk_path").input(str(newer)).run()
    at.selectbox(key="add_disk_as").select(doc.id).run()
    assert at.button(key="add_disk_ver").label == f"Registrar como v2 de {doc.id} · 1 KB"
    at.button(key="add_disk_ver").click().run()
    assert not at.exception, at.exception
    assert [v.n for v in load_versions(demo_ws, doc.id)] == [1, 2] and len(demo_ws.docs()) == 1
    assert (demo_ws.abspath(doc.root) / "intro.tex").read_text(encoding="utf-8") == "Versão dois.\n"


def test_sidebar_switches_to_a_recent_workspace(demo_ws, monkeypatch, tmp_path):
    other = tmp_path / "outro"
    other.mkdir()
    folders.remember_workspace(Workspace.open(other).root)
    monkeypatch.setenv("MARGA_WORKSPACE", str(demo_ws.root))
    at = AppTest.from_file(APP, default_timeout=180)
    at.run()
    recent = at.selectbox(key="ws_recent")
    assert len(recent.options) == 1 and recent.options[0].startswith("outro · ")  # o workspace atual fica de fora
    recent.select(str(other.resolve())).run()
    assert not at.exception, at.exception
    assert at.session_state["ws_path"] == str(other.resolve())
    assert folders.recent_workspaces()[0] == other.resolve()


def test_latex_source_jumps_to_a_section(demo_ws, monkeypatch):
    doc = actions.add_document(demo_ws, "artigo")
    monkeypatch.setenv("MARGA_WORKSPACE", str(demo_ws.root))
    at = AppTest.from_file(APP, default_timeout=180)
    at.session_state["L_doc"] = doc.id
    at.run()
    jump = at.selectbox(key=f"L_jump_{doc.id}_1")
    assert jump.options[1:] == ["1 Introdução · introducao.tex:1", "2 Métodos · metodos.tex:1"]
    jump.select(1).run()  # 2 Métodos
    assert not at.exception, at.exception
    assert at.session_state[f"L_file_{doc.id}_1"] == "artigo/sections/metodos.tex"
    assert at.session_state["focus"]["file"] == "artigo/sections/metodos.tex"
    assert at.selectbox(key=f"L_jump_{doc.id}_1").value == -1


def test_word_powerpoint_and_html_render_linked_and_jump_to_a_slide(demo_ws, monkeypatch):
    slides = actions.add_document(demo_ws, "apresentacao.pptx")
    report = actions.add_document(demo_ws, "relatorio.docx")
    page = actions.add_document(demo_ws, "pagina.html")
    marks = []
    for doc, phrase in ((slides, "1.200 participantes"), (report, "1.250 participantes"), (page, "120 estações")):
        version = latest_version(demo_ws, doc.id)
        text = version_text(demo_ws, doc, version, doc.root)
        start = text.index(phrase)
        anchor = anchor_from_offsets(text, doc.root, version.n, start, start + len(phrase))
        marks.append(actions.create_annotation(demo_ws, doc.id, anchor, "marca", "").id)
    actions.create_link(demo_ws, marks[0], marks[1], "contradiz")
    actions.create_link(demo_ws, marks[2], marks[1], "consistente")
    export.export_round(demo_ws)
    monkeypatch.setenv("MARGA_WORKSPACE", str(demo_ws.root))
    at = AppTest.from_file(APP, default_timeout=180)
    at.session_state["L_doc"] = slides.id
    at.session_state["R_doc"] = report.id
    at.run()
    assert not at.exception, at.exception

    jump = at.selectbox(key=f"L_jump_{slides.id}_1")
    assert jump.options[1:] == ["Slide 1 · Bicicletas compartilhadas em Curitiba", "Slide 2 · Metodologia",
                                "Slide 3 · Resultados"]
    assert at.selectbox(key=f"R_jump_{report.id}_1").options[2:4] == ["Resumo", "Amostra"]
    jump.select(2).run()
    assert not at.exception, at.exception
    assert at.session_state["focus"]["line"] == 13
    for section in ("Links", "Comparar versões", "Rodadas", "Revisão"):
        at.session_state["nav"] = section
        at.run()
        assert not at.exception, (section, at.exception)


def test_powerpoint_shows_slides_and_pdf(demo_ws, monkeypatch):
    doc = actions.add_document(demo_ws, "apresentacao.pptx")
    monkeypatch.setenv("MARGA_WORKSPACE", str(demo_ws.root))
    at = AppTest.from_file(APP, default_timeout=180)
    at.session_state["L_doc"] = at.session_state["R_doc"] = doc.id
    at.session_state[f"L_mode_{doc.id}"], at.session_state[f"R_mode_{doc.id}"] = "Slides", "PDF"
    at.session_state[f"L_page_{doc.id}_1"] = 2
    at.run()
    assert not at.exception, at.exception
    assert at.selectbox(key=f"L_sec_{doc.id}_1").value == 1  # o seletor acompanha o slide 2
