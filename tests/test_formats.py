"""Word, PowerPoint e HTML: extração do texto, versões, re-ancoragem, links com os outros tipos e pacote."""

import io
import json
import zipfile
from pathlib import Path

import docx
import pptx
import pytest
from pptx.util import Inches

from marga.core import actions, export, formats
from marga.core.anchors import anchor_from_offsets
from marga.core.formats import extract
from marga.core.versions import doc_status, latest_version, page_of_line, version_text
from marga.core.workspace import MargaError

DEMO = Path(__file__).resolve().parents[1] / "examples" / "demo"
NS = ('xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
      'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
      'xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006" '
      'xmlns:v="urn:schemas-microsoft-com:vml"')
REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def _docx_bytes(body: str, styles: str = "", footnotes: str = "") -> bytes:
    """Um .docx mínimo, escrito à mão, para os casos que o python-docx não gera (revisões, notas, caixas)."""
    rels = [f'<Relationship Id="rId1" Type="{REL}/styles" Target="styles.xml"/>',
            f'<Relationship Id="rId2" Type="{REL}/footnotes" Target="footnotes.xml"/>']
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("_rels/.rels", '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/'
                    f'relationships"><Relationship Id="rId1" Type="{REL}/officeDocument" '
                    'Target="word/document.xml"/></Relationships>')
        zf.writestr("word/_rels/document.xml.rels", '<Relationships xmlns="http://schemas.openxmlformats.org/'
                    f'package/2006/relationships">{"".join(rels)}</Relationships>')
        zf.writestr("word/document.xml", f"<w:document {NS}><w:body>{body}</w:body></w:document>")
        zf.writestr("word/styles.xml", f"<w:styles {NS}>{styles}</w:styles>")
        zf.writestr("word/footnotes.xml", f"<w:footnotes {NS}>{footnotes}</w:footnotes>")
    return buf.getvalue()


def _run(text: str) -> str:
    return f'<w:r><w:t xml:space="preserve">{text}</w:t></w:r>'


def _quote_anchor(ws, doc, phrase: str):
    """Âncora na primeira ocorrência de ``phrase`` em algum arquivo da versão atual (com a página/slide)."""
    version = latest_version(ws, doc.id)
    for path in sorted(version.files):
        text = version_text(ws, doc, version, path)
        if phrase in text:
            start = text.index(phrase)
            anchor = anchor_from_offsets(text, path, version.n, start, start + len(phrase))
            anchor.page = page_of_line(ws, doc, version, path, anchor.start_line)
            return anchor
    raise AssertionError(f"{phrase!r} não está em {doc.label}")


# ------------------------------------------------------------------ Word
def test_word_paragraphs_titles_lists_and_tables(tmp_path):
    d = docx.Document()
    d.add_heading("Relatório", 0)
    d.add_heading("Introdução", 1)
    d.add_paragraph("Primeiro parágrafo.")
    d.add_paragraph()
    d.add_paragraph()
    d.add_paragraph("Item um", style="List Bullet")
    d.add_heading("Detalhes", 2)
    table = d.add_table(rows=2, cols=2)
    for (r, c), text in {(0, 0): "Região", (0, 1): "Estações", (1, 0): "Centro", (1, 1): "70"}.items():
        table.cell(r, c).text = text
    path = tmp_path / "r.docx"
    d.save(path)

    got = extract("docx", path.read_bytes())
    assert got.text.split("\n") == ["# Relatório", "# Introdução", "Primeiro parágrafo.", "", "- Item um",
                                    "## Detalhes", "| Região | Estações |", "| Centro | 70 |"]
    assert [(s["level"], s["title"], s["line"]) for s in got.sections] == [
        (1, "Relatório", 1), (1, "Introdução", 2), (2, "Detalhes", 6)]


def test_word_follows_revisions_fields_footnotes_and_text_boxes():
    styles = ('<w:style w:type="paragraph" w:styleId="Ttulo1"><w:name w:val="heading 1"/></w:style>'
              '<w:style w:type="paragraph" w:styleId="Lista"><w:name w:val="List Number"/>'
              '<w:pPr><w:numPr><w:numId w:val="3"/></w:numPr></w:pPr></w:style>')
    box = '<w:txbxContent><w:p>' + _run("Caixa de texto") + '</w:p></w:txbxContent>'
    body = "".join([
        '<w:p><w:pPr><w:pStyle w:val="Ttulo1"/></w:pPr>' + _run("Introdução") + "</w:p>",
        '<w:p><w:pPr><w:outlineLvl w:val="1"/></w:pPr>' + _run("Por nível de tópico") + "</w:p>",
        '<w:p><w:pPr><w:tabs><w:tab w:val="left" w:pos="720"/></w:tabs></w:pPr>' + _run("Os dados de ")
        + '<w:del w:id="1" w:author="R"><w:r><w:delText>2019</w:delText></w:r></w:del>'
        + '<w:ins w:id="2" w:author="R">' + _run("2021") + "</w:ins>" + _run(" estão no ")
        + '<w:hyperlink r:id="rId9">' + _run("site") + "</w:hyperlink>"
        + '<w:r><w:footnoteReference w:id="5"/></w:r><w:r><w:tab/><w:t>fim.</w:t></w:r></w:p>',
        '<w:p><w:r><w:fldChar w:fldCharType="begin"/></w:r><w:r><w:instrText> PAGE </w:instrText></w:r>'
        '<w:r><w:fldChar w:fldCharType="separate"/></w:r>' + _run("7")
        + '<w:r><w:fldChar w:fldCharType="end"/></w:r>' + _run(" páginas")
        + '<w:r><w:footnoteReference w:id="2"/></w:r></w:p>',
        '<w:p><w:pPr><w:pStyle w:val="Lista"/><w:numPr><w:ilvl w:val="1"/><w:numId w:val="3"/></w:numPr></w:pPr>'
        + _run("Item aninhado") + "</w:p>",
        '<w:p><w:pPr><w:pStyle w:val="Lista"/><w:numPr><w:numId w:val="0"/></w:numPr></w:pPr>'
        + _run("Sem numeração") + "</w:p>",
        f'<w:p><w:r><mc:AlternateContent><mc:Choice Requires="wps"><w:drawing>{box}</w:drawing></mc:Choice>'
        f'<mc:Fallback><w:pict><v:textbox>{box}</v:textbox></w:pict></mc:Fallback></mc:AlternateContent></w:r>'
        + _run("Legenda") + "</w:p>",
        '<w:sdt><w:sdtPr><w:alias w:val="Resumo"/></w:sdtPr><w:sdtContent><w:p>' + _run("No controle")
        + "</w:p></w:sdtContent></w:sdt>",
        "<w:p/><w:p/>",
        "<w:tbl><w:tr><w:tc><w:p>" + _run("a") + "</w:p><w:p>" + _run("b") + "</w:p></w:tc>"
        "<w:tc><w:p/></w:tc></w:tr></w:tbl>",
    ])
    footnotes = ('<w:footnote w:type="separator" w:id="-1"><w:p><w:r><w:separator/></w:r></w:p></w:footnote>'
                 '<w:footnote w:id="2"><w:p><w:r><w:footnoteRef/></w:r>' + _run(" Nota dois") + "</w:p></w:footnote>"
                 '<w:footnote w:id="5"><w:p>' + _run("Nota cinco") + "</w:p></w:footnote>")

    got = extract("docx", _docx_bytes(body, styles, footnotes))
    assert got.text.split("\n") == [
        "# Introdução", "## Por nível de tópico", "Os dados de 2021 estão no site[^1] fim.", "7 páginas[^2]",
        "  - Item aninhado", "Sem numeração", "Legenda", "Caixa de texto", "No controle", "", "| a b |  |", "",
        "[^1]: Nota cinco", "[^2]: Nota dois",
    ]


# ------------------------------------------------------------ PowerPoint
def test_powerpoint_slides_follow_the_presentation_order(tmp_path):
    prs = pptx.Presentation()
    capa = prs.slides.add_slide(prs.slide_layouts[0])
    capa.shapes.title.text = "Capa"
    capa.placeholders[1].text = "Subtítulo"
    topicos = prs.slides.add_slide(prs.slide_layouts[1])
    topicos.shapes.title.text = "Tópicos"
    body = topicos.placeholders[1].text_frame
    body.text = "Nível zero"
    sub = body.add_paragraph()
    sub.text, sub.level = "Nível um", 1
    topicos.notes_slide.notes_text_frame.text = "Nota do slide"
    solto = prs.slides.add_slide(prs.slide_layouts[6])  # em branco
    group = solto.shapes.add_group_shape()
    group.shapes.add_textbox(Inches(1), Inches(1), Inches(3), Inches(1)).text_frame.text = "Dentro do grupo"
    table = solto.shapes.add_table(2, 2, Inches(1), Inches(3), Inches(4), Inches(1)).table
    table.cell(0, 0).text, table.cell(0, 1).text, table.cell(1, 0).text = "a", "b", "c"
    ids = prs.slides._sldIdLst  # o terceiro slide criado passa a ser o segundo da apresentação
    ids.insert(1, ids[2])
    capa._element.set("show", "0")
    path = tmp_path / "a.pptx"
    prs.save(path)

    got = extract("pptx", path.read_bytes())
    assert got.text.split("\n") == [
        "=== slide 1 · oculto ===", "# Capa", "Subtítulo", "",
        "=== slide 2 ===", "Dentro do grupo", "| a | b |", "| c |  |", "",
        "=== slide 3 ===", "# Tópicos", "- Nível zero", "  - Nível um", "Notas do apresentador:", "Nota do slide",
    ]
    assert [got.page_of_line(n) for n in (1, 5, 8, 15)] == [1, 2, 2, 3]
    assert [s["title"] for s in got.sections] == ["Slide 1 · Capa", "Slide 2", "Slide 3 · Tópicos"]


# ------------------------------------------------------------------ HTML
def test_html_text_keeps_structure_and_the_file_lines():
    page = "\n".join([
        "<!doctype html>",                                                   # 1
        "<html><head><title>Título da aba</title>",                          # 2
        "<style>p { color: red }</style></head>",                            # 3
        "<body><h1>Relatório &amp; notas</h1>",                              # 4
        "<p>Primeiro <b>parágrafo</b>,",                                     # 5
        "   em duas linhas.",                                                # 6
        "<ul><li>um<li>dois<ul><li>dois.a</ul></ul>",                        # 7
        "<table><tr><th>Região<th>Estações",                                 # 8
        "<tr><td><p>Centro</p><p>norte</p><td>70</table>",                   # 9
        "<pre>",                                                             # 10
        "x = 1",                                                             # 11
        "  y = 2</pre>",                                                     # 12
        "<script>var s = '<p>não</p>';</script>",                            # 13
        "<p>fim&nbsp;!<br>outra linha</p></body></html>",                    # 14
    ])
    got = extract("html", page.encode("utf-8"))
    assert got.text.split("\n") == [
        "# Relatório & notas", "Primeiro parágrafo, em duas linhas.", "- um", "- dois", "  - dois.a",
        "| Região | Estações |", "| Centro norte | 70 |", "x = 1", "  y = 2", "fim !", "outra linha",
    ]
    assert got.source_lines(2, 2) == (5, 6) and got.source_lines(7, 7) == (9, 9)
    assert got.source_lines(8, 9) == (11, 12) and got.source_lines(11, 11) == (14, 14)
    assert got.sections == [{"level": 1, "title": "Relatório & notas", "line": 1}]
    assert extract("html", "<p>coração</p>".encode("latin-1")).text == "coração"


def test_unreadable_files_give_clear_errors(monkeypatch):
    with pytest.raises(MargaError, match=r"x\.docx: não consegui abrir o arquivo como Word"):
        extract("docx", b"isto nao e um zip", "x.docx")
    with pytest.raises(MargaError, match=r"não é um PowerPoint \(\.pptx\): a parte principal é word/document.xml"):
        extract("pptx", _docx_bytes(""))  # um .docx renomeado para .pptx
    empty = io.BytesIO()
    zipfile.ZipFile(empty, "w").close()
    with pytest.raises(MargaError, match="falta a parte word/document.xml"):
        extract("docx", empty.getvalue())
    monkeypatch.setattr(formats, "MAX_PART_BYTES", 10)
    with pytest.raises(MargaError, match="grande demais"):
        extract("docx", _docx_bytes(_run("texto") * 10))


# ------------------------------------------------- versões e re-ancoragem
def test_word_document_is_versioned_and_reanchored_after_the_agent_edits_it(demo_ws):
    doc = actions.add_document(demo_ws, "relatorio.docx")
    assert (doc.kind, doc.kind_label, doc.single, doc.root) == ("docx", "Word", True, "relatorio.docx")
    v1 = latest_version(demo_ws, doc.id)
    assert list(v1.files) == list(v1.extracted) == ["relatorio.docx"] and not v1.encodings
    c1 = actions.create_annotation(demo_ws, doc.id, _quote_anchor(demo_ws, doc, "1.250 participantes"), "corrigir",
                                   "São 1.200 participantes, como no suplemento.")
    m1 = actions.create_annotation(demo_ws, doc.id, _quote_anchor(demo_ws, doc, "Taxa de resposta de 42%"), "marca", "")

    path = demo_ws.abspath(doc.root)  # o agente edita o próprio .docx, como pedem as regras do pacote
    word = docx.Document(path)
    for paragraph in word.paragraphs:
        for run in paragraph.runs:
            run.text = run.text.replace("1.250", "1.200")
    word.save(path)
    assert doc_status(demo_ws, doc).modified == ["relatorio.docx"]

    report = actions.register_version(demo_ws, doc.id)
    assert report.states == {"c1": "alterado", "m1": "inalterado"}
    c1, m1 = demo_ws.get_annotation(c1.id), demo_ws.get_annotation(m1.id)
    assert c1.anchor.version == 2 and "1.200 participantes" in c1.anchor.quote
    assert m1.anchor.quote == "Taxa de resposta de 42%"
    v2 = latest_version(demo_ws, doc.id)
    assert "1.200 participantes" in version_text(demo_ws, doc, v2, "relatorio.docx")
    assert "1.250 participantes" in version_text(demo_ws, doc, v1, "relatorio.docx")  # a v1 não muda


def test_powerpoint_annotation_follows_its_slide(demo_ws):
    doc = actions.add_document(demo_ws, "apresentacao.pptx")
    anchor = _quote_anchor(demo_ws, doc, "Aumento das viagens curtas")
    assert anchor.page == 3
    c1 = actions.create_annotation(demo_ws, doc.id, anchor, "corrigir", "Mostrar também o número absoluto.")

    path = demo_ws.abspath(doc.root)
    prs = pptx.Presentation(path)
    novo = prs.slides.add_slide(prs.slide_layouts[5])
    novo.shapes.title.text = "Discussão"
    ids = prs.slides._sldIdLst
    ids.insert(2, ids[-1])  # o slide novo entra antes de "Resultados"
    prs.save(path)

    report = actions.register_version(demo_ws, doc.id)
    c1 = demo_ws.get_annotation(c1.id)
    assert report.states == {c1.id: "inalterado"} and c1.anchor.page == 4
    text = version_text(demo_ws, doc, latest_version(demo_ws, doc.id), doc.root)
    assert "=== slide 3 ===\n# Discussão" in text


# ------------------------------------------------------ links e pacote
def test_links_connect_every_document_kind_and_reach_the_bundle(demo_ws):
    docs = {name: actions.add_document(demo_ws, name)
            for name in ("artigo", "parecer.pdf", "notas.md", "relatorio.docx", "apresentacao.pptx", "pagina.html")}

    def mark(name: str, phrase: str, type_: str = "marca", body: str = "") -> str:
        doc = docs[name]
        return actions.create_annotation(demo_ws, doc.id, _quote_anchor(demo_ws, doc, phrase), type_, body).id

    tex = mark("artigo", "1.250 participantes", "corrigir", "Conferir com o suplemento e a apresentação.")
    word = mark("relatorio.docx", "1.250 participantes")
    slides = mark("apresentacao.pptx", "1.200 participantes")
    page = mark("pagina.html", "1.250 participantes")
    review = mark("parecer.pdf", "período de coleta dos dados")
    notes = mark("notas.md", "março a junho de 2021")
    pairs = [(tex, word, "consistente"), (slides, tex, "contradiz"), (page, word, "propagar"),
             (word, review, "ver_tambem"), (notes, slides, "origem")]
    for source, target, relation in pairs:
        actions.create_link(demo_ws, source, target, relation)

    info = export.export_round(demo_ws)
    rdir = export.round_dir(demo_ws, info.n)
    bundle = json.loads((rdir / "bundle.json").read_text(encoding="utf-8"))
    kinds = {d["id"]: d["kind"] for d in bundle["documents"]}
    assert [(lk["source"], lk["target"], lk["relation"]) for lk in bundle["links"]] == pairs
    assert {(kinds[lk["source_doc"]], kinds[lk["target_doc"]]) for lk in bundle["links"]} == {
        ("latex", "docx"), ("pptx", "latex"), ("html", "docx"), ("docx", "pdf"), ("text", "pptx")}

    by_kind = {d["kind"]: d for d in bundle["documents"]}
    assert [by_kind[k]["editable"] for k in ("docx", "pptx", "html", "pdf")] == [True, True, True, False]
    assert by_kind["docx"]["extracted_text"] and not by_kind["latex"]["extracted_text"]
    assert "python-docx" in by_kind["docx"]["edit_hint"] and "python-pptx" in by_kind["pptx"]["edit_hint"]
    anns = {a["id"]: a for a in bundle["annotations"]}
    assert anns[slides]["slide"] == 2 and "slide" not in anns[word]
    first, last = anns[page]["file_lines"]
    html_lines = (demo_ws.root / "pagina.html").read_text(encoding="utf-8").split("\n")
    assert "1.250 participantes" in "\n".join(html_lines[first - 1:last])

    md = (rdir / "bundle.md").read_text(encoding="utf-8")
    assert "## Rastreio entre arquivos" in md and md.count("-->|") == len(pairs)
    assert f"`{demo_ws.root / 'relatorio.docx'}` (edite o próprio .docx, por exemplo com python-docx" in md
    assert "slide 2" in md and f"no arquivo: linha {first}" in md and "_Word: as linhas abaixo" not in md
    assert "Word (.docx) e PowerPoint (.pptx): edite o próprio arquivo" in md
    assert "python-pptx" in (rdir / "bundle.html").read_text(encoding="utf-8")
    prompt = export.agent_prompt(demo_ws, info)
    assert f"{demo_ws.root / 'apresentacao.pptx'} (edite o próprio .pptx" in prompt
    assert str(demo_ws.root / "parecer.pdf") not in prompt


# ------------------------------------------------- adicionar documentos
def test_discover_lists_word_powerpoint_and_html_but_not_lock_files(demo_ws):
    (demo_ws.root / "~$relatorio.docx").write_bytes(b"trava do Word")
    found = {c.path: c for c in actions.discover(demo_ws)}
    assert found["relatorio.docx"].kind == "docx" and found["relatorio.docx"].label == "relatorio.docx · Word"
    assert found["apresentacao.pptx"].label == "apresentacao.pptx · PowerPoint"
    assert found["pagina.html"].label == "pagina.html · HTML"
    assert "~$relatorio.docx" not in found


def test_upload_of_word_files(text_ws):
    doc = actions.import_upload(text_ws, "Relatório final.docx", (DEMO / "relatorio.docx").read_bytes())
    assert doc.kind == "docx" and doc.root == "uploads/Relatório final.docx"
    with pytest.raises(MargaError, match="não consegui abrir o arquivo como Word"):
        actions.import_upload(text_ws, "quebrado.docx", b"isto nao e um zip")
    assert not (text_ws.uploads / "quebrado.docx").exists() and [d.id for d in text_ws.docs()] == [doc.id]
    with pytest.raises(MargaError, match=r"use \.zip, \.pdf, \.tex, \.docx, \.pptx, \.html"):
        actions.import_upload(text_ws, "planilha.xlsx", b"x")
