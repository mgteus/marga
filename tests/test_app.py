"""Smoke test do app Streamlit (renderiza todas as abas sem exceções)."""

from pathlib import Path

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
