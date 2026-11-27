"""Fila de compilações em segundo plano: uma compilação por versão, estados para a interface."""

import threading
import time

import pytest

from marga.core import actions, builds, latex
from marga.core.versions import latest_version
from marga.core.workspace import MargaError, write_json

needs_tex = pytest.mark.skipif(not latex.can_compile(), reason="latexmk ausente")


@pytest.fixture(autouse=True)
def clean_queue():
    builds.forget()
    yield
    builds.forget()


def _until(check, timeout: float = 5.0) -> None:
    end = time.monotonic() + timeout
    while not check():
        assert time.monotonic() < end, "o estado esperado não chegou"
        time.sleep(0.01)


@pytest.fixture
def fake_compile(monkeypatch, demo_ws):
    """Uma compilação de mentira, que espera a vez de soltar a prévia e o resultado."""
    gates = {"previa": threading.Event(), "final": threading.Event()}
    calls = []

    def compile_version(ws, doc, version, *, force=False, timeout=latex.DEFAULT_TIMEOUT, on_preview=None):
        calls.append((doc.id, version.n, force))
        bdir = latex.build_dir(ws, doc.id, version.n)
        bdir.mkdir(parents=True, exist_ok=True)
        (bdir / "main.pdf").write_bytes(b"%PDF-1.4\n")
        result = latex.CompileResult(doc=doc.id, version=version.n, ok=True, pdf=str(bdir / "main.pdf"), log=None,
                                     src_dir=str(bdir), duration=1.0, finished_at="", engine="pdflatex",
                                     source_hash=latex.manifest_hash(version.files))
        assert gates["previa"].wait(5)
        on_preview(result)
        assert gates["final"].wait(5)
        if doc.id == "erro":
            raise MargaError("A v1 já está sendo compilada por outro processo do Marga.")
        write_json(bdir / "result.json", result.to_dict())
        return result

    monkeypatch.setattr(latex, "compile_version", compile_version)
    monkeypatch.setattr(latex, "can_compile", lambda: True)
    doc = actions.add_document(demo_ws, "artigo")
    return demo_ws, doc, latest_version(demo_ws, doc.id), gates, calls


def test_a_version_goes_through_the_queue_once(fake_compile):
    ws, doc, version, gates, calls = fake_compile
    first = builds.submit(ws, doc, version)
    again = builds.submit(ws, doc, version)  # outra aba, ou a página recarregada no meio
    assert first.state == "fila" and again is not None and again.running
    _until(lambda: builds.status(ws, doc.id, version.n).state == "compilando")
    assert [j.version for j in builds.active(ws, doc.id)] == [version.n]
    gates["previa"].set()
    _until(lambda: builds.status(ws, doc.id, version.n).state == "previa")
    assert "prévia pronta" in builds.status(ws, doc.id, version.n).label
    gates["final"].set()
    done = builds.wait(ws, doc.id, version.n)
    assert done.state == "pronto" and done.result.ok and done.preview_at and done.finished_at
    assert calls == [(doc.id, version.n, False)]
    assert builds.active(ws) == []
    assert builds.submit(ws, doc, version) is None  # já compilada
    builds.forget()
    assert builds.status(ws, doc.id, version.n).state == "pronto"  # o resultado continua no disco


def test_force_compiles_again(fake_compile):
    ws, doc, version, gates, calls = fake_compile
    gates["previa"].set(), gates["final"].set()
    builds.submit(ws, doc, version)
    builds.wait(ws, doc.id, version.n)
    builds.submit(ws, doc, version, force=True)
    assert builds.wait(ws, doc.id, version.n).state == "pronto"
    assert calls == [(doc.id, version.n, False), (doc.id, version.n, True)]


def test_a_failure_becomes_the_error_state(fake_compile, monkeypatch):
    ws, doc, version, gates, _calls = fake_compile
    monkeypatch.setattr(doc, "id", "erro")
    gates["previa"].set(), gates["final"].set()
    builds.submit(ws, doc, version)
    failed = builds.wait(ws, "erro", version.n)
    assert failed.state == "erro" and "outro processo do Marga" in failed.error and not failed.running
    assert "não compilou" in failed.label


def test_only_latex_documents_compile(text_ws, monkeypatch):
    monkeypatch.setattr(latex, "can_compile", lambda: True)
    doc = actions.add_document(text_ws, "doc")
    assert builds.submit(text_ws, doc, latest_version(text_ws, doc.id)) is None
    assert builds.status(text_ws, doc.id, 1) is None


@needs_tex
def test_a_real_compile_in_the_background(demo_ws):
    doc = actions.add_document(demo_ws, "artigo")
    version = latest_version(demo_ws, doc.id)
    assert builds.submit(demo_ws, doc, version).state == "fila"
    done = builds.wait(demo_ws, doc.id, version.n)
    assert done.state == "pronto" and done.result.ok and done.preview_at is not None
    assert latex.load_preview(demo_ws, doc.id, version.n).has_pdf
