import pytest

from marga.core import actions
from marga.core.anchors import lines_anchor, make_anchor, reanchor
from marga.core.versions import doc_status, latest_version, version_text
from marga.core.workspace import MargaError


def test_add_text_doc_and_register_version(text_ws):
    doc = actions.add_document(text_ws, "doc")
    assert doc.kind == "text" and not doc.single
    v1 = latest_version(text_ws, doc.id)
    assert v1.n == 1 and list(v1.files) == ["doc/a.txt"] and v1.origin == "inicial"
    assert not doc_status(text_ws, doc).dirty
    with pytest.raises(MargaError):
        actions.register_version(text_ws, doc.id)
    (text_ws.root / "doc" / "a.txt").write_text("linha zero\nlinha um\nlinha dois\n", encoding="utf-8")
    assert doc_status(text_ws, doc).modified == ["doc/a.txt"]
    report = actions.register_version(text_ws, doc.id)
    assert report.version.n == 2 and report.version.origin == "manual"


def test_latin1_files_are_decoded(text_ws):
    (text_ws.root / "doc" / "b.tex").write_bytes("análise em português".encode("latin-1"))
    doc = actions.add_document(text_ws, "doc", kind="text")
    v1 = latest_version(text_ws, doc.id)
    assert v1.encodings == {"doc/b.tex": "latin-1"}
    assert version_text(text_ws, doc, v1, "doc/b.tex") == "análise em português"


def test_reanchor_states():
    old = {"f.tex": "alfa\nbeta gama\ndelta\n"}
    anchor = make_anchor(old["f.tex"], "f.tex", 1, 2, 5, 2, 9)
    assert anchor.quote == "gama"

    shifted = reanchor(anchor, old, {"f.tex": "novo\nalfa\nbeta gama\ndelta\n"}, 2)
    assert shifted.state == "inalterado"
    assert (shifted.anchor.start_line, shifted.anchor.version, shifted.anchor.quote) == (3, 2, "gama")

    changed = reanchor(anchor, old, {"f.tex": "alfa\nbeta gamma corrigida\ndelta\n"}, 2)
    assert changed.state == "alterado" and changed.anchor.start_line == 2

    moved = reanchor(anchor, old, {"g.tex": "x\nbeta gama\n"}, 2)
    assert moved.state == "movido" and moved.anchor.file == "g.tex"

    orphan = reanchor(anchor, old, {"g.tex": "nada aqui\n"}, 2)
    assert orphan.state == "orfao" and orphan.anchor.version == 1


def test_comment_is_reanchored_when_new_version_is_registered(text_ws):
    doc = actions.add_document(text_ws, "doc")
    text = version_text(text_ws, doc, latest_version(text_ws, doc.id), "doc/a.txt")
    anchor = make_anchor(text, "doc/a.txt", 1, 2, 11, 2, 19)
    assert anchor.quote == "com erro"
    c1 = actions.create_annotation(text_ws, doc.id, anchor, "corrigir", "tirar o erro", suggestion="sem erro")
    assert c1.id == "c1" and c1.suggestion == "sem erro"

    (text_ws.root / "doc" / "a.txt").write_text("linha um\nlinha dois sem erro\nlinha três\n", encoding="utf-8")
    ok, why = actions.can_annotate(text_ws, doc, 1)
    assert not ok and "Registre a v2" in why
    with pytest.raises(MargaError):
        actions.create_annotation(text_ws, doc.id, anchor, "nota", "outra")

    report = actions.register_version(text_ws, doc.id)
    assert report.states == {"c1": "alterado"}
    c1 = text_ws.get_annotation("c1")
    assert c1.anchor.version == 2 and c1.anchor_history[0].version == 1 and c1.region_state == "alterado"


def test_links_and_annotation_lifecycle(text_ws):
    a = actions.add_document(text_ws, "doc")
    b = actions.add_document(text_ws, "outro.md")
    assert (a.id, b.id) == ("A", "B") and b.single
    ta = version_text(text_ws, a, latest_version(text_ws, "A"), "doc/a.txt")
    tb = version_text(text_ws, b, latest_version(text_ws, "B"), "outro.md")
    c1 = actions.create_annotation(text_ws, "A", lines_anchor(ta, "doc/a.txt", 1, 1, 2), "reescrever", "melhorar")
    m1 = actions.create_annotation(text_ws, "B", lines_anchor(tb, "outro.md", 1, 3, 3), "marca", "")
    assert m1.id == "m1" and m1.is_mark
    with pytest.raises(MargaError):
        actions.create_link(text_ws, "c1", "c1", "consistente")
    link = actions.create_link(text_ws, "c1", "m1", "consistente", "mesmo dado")
    assert link.id == "L1"
    with pytest.raises(MargaError):
        actions.create_link(text_ws, "c1", "m1", "consistente")
    actions.set_status(text_ws, "c1", "resolvido")
    actions.add_reply(text_ws, "c1", "ainda falta o título", reopen=True)
    c1 = text_ws.get_annotation("c1")
    assert c1.status == "aberto" and c1.thread[-1].author == "voce"
    actions.delete_annotation(text_ws, "m1")
    assert text_ws.links() == []
