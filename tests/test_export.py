import json

import pytest

from marga.core import actions, export
from marga.core.anchors import lines_anchor, make_anchor
from marga.core.versions import latest_version, version_text
from marga.core.workspace import MargaError


def _setup(ws):
    a = actions.add_document(ws, "doc")
    b = actions.add_document(ws, "outro.md")
    ta = version_text(ws, a, latest_version(ws, a.id), "doc/a.txt")
    tb = version_text(ws, b, latest_version(ws, b.id), "outro.md")
    c1 = actions.create_annotation(ws, a.id, make_anchor(ta, "doc/a.txt", 1, 2, 11, 2, 19), "corrigir",
                                   "tirar o erro", suggestion="sem erro")
    m1 = actions.create_annotation(ws, b.id, lines_anchor(tb, "outro.md", 1, 3, 3), "marca", "")
    link = actions.create_link(ws, c1.id, m1.id, "propagar", "mesma correção")
    return a, b, c1, m1, link


def test_export_round_writes_bundle(text_ws):
    _setup(text_ws)
    info = export.export_round(text_ws, "Seja conciso.")
    rdir = export.round_dir(text_ws, info.n)
    for name in ("bundle.json", "bundle.md", "bundle.html", "round.json"):
        assert (rdir / name).exists()
    bundle = json.loads((rdir / "bundle.json").read_text(encoding="utf-8"))
    assert bundle["schema"] == "marga.bundle/v1" and bundle["round"] == 1
    [c1] = [a for a in bundle["annotations"] if a["id"] == "c1"]
    assert c1["quote"] == "com erro" and c1["suggestion"] == "sem erro"
    assert c1["links"][0]["other"] == "m1" and c1["links"][0]["direction"] == "saida"
    assert [r["anchored"] for r in c1["excerpt"]] == [False, True, False, False]
    assert bundle["links"][0]["relation"] == "propagar"
    md = (rdir / "bundle.md").read_text(encoding="utf-8")
    assert "c1 · Corrigir" in md and "Link L1" in md and "```mermaid" in md and "Seja conciso." in md
    assert "- com erro\n" in md and "+ sem erro" in md
    assert (rdir / "bundle.html").read_text(encoding="utf-8").startswith("<!doctype html>")
    assert text_ws.get_annotation("c1").rounds == [1]
    assert (text_ws.dir / "rounds" / "latest.md").exists() and (text_ws.dir / "AGENT.md").exists()


def test_export_is_blocked_when_disk_changed(text_ws):
    _setup(text_ws)
    (text_ws.root / "doc" / "a.txt").write_text("mudou\n", encoding="utf-8")
    with pytest.raises(MargaError, match="registre a v2"):
        export.export_round(text_ws)


def test_agent_response_is_imported_when_new_version_is_registered(text_ws):
    a, *_ = _setup(text_ws)
    info = export.export_round(text_ws)
    (text_ws.root / "doc" / "a.txt").write_text("linha um\nlinha dois sem erro\nlinha três\n", encoding="utf-8")
    export.response_path(text_ws, info.n).write_text(json.dumps({
        "round": 1, "resumo": "ok", "comments": {"c1": {"status": "feito", "nota": "troquei"}},
    }), encoding="utf-8")
    report = actions.register_version(text_ws, a.id)
    assert report.round == 1 and report.imported == 1 and report.version.origin == "agente"
    c1 = text_ws.get_annotation("c1")
    assert c1.thread[-1].author == "agente" and c1.thread[-1].agent_status == "feito"
    assert export.get_round(text_ws, 1).response_imported and export.get_round(text_ws, 1).summary == "ok"

    info2 = export.export_round(text_ws)
    bundle = json.loads((export.round_dir(text_ws, info2.n) / "bundle.json").read_text(encoding="utf-8"))
    doc_a = next(d for d in bundle["documents"] if d["id"] == "A")
    assert doc_a["previous_version"] == 1 and "+linha dois sem erro" in doc_a["changes_since_previous"]["diff"]


def test_invalid_response_is_reported(text_ws):
    _setup(text_ws)
    info = export.export_round(text_ws)
    export.response_path(text_ws, info.n).write_text("{ não é json", encoding="utf-8")
    with pytest.raises(MargaError):
        export.import_response(text_ws, info.n)
