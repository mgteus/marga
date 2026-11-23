"""Smoke test do app Streamlit (renderiza todas as abas sem exceções)."""

from pathlib import Path

import pymupdf
from streamlit.testing.v1 import AppTest

from marga.core import actions, export

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
