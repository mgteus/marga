"""PowerPoint: o leitor de slides (HTML sem conversão) e o PDF pelo LibreOffice."""

import io
import re
import subprocess
import zipfile
from html.parser import HTMLParser
from pathlib import Path

import pptx
import pymupdf
import pytest
from pptx.dml.color import RGBColor
from pptx.oxml import parse_xml
from pptx.util import Inches, Pt

from marga.core import actions, office, slides
from marga.core.formats import Extracted, extract
from marga.core.versions import latest_version, page_of_line
from marga.core.workspace import MargaError

DEMO = Path(__file__).resolve().parents[1] / "examples" / "demo"
A_NS = 'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"'
needs_libreoffice = pytest.mark.skipif(not office.available(), reason="LibreOffice não instalado")


class _Paragraphs(HTMLParser):
    """Texto de cada parágrafo ligado a uma linha (o .mg-t), com data-pre e data-lead."""

    def __init__(self) -> None:
        super().__init__()
        self.found: list[tuple[int, int, int, str]] = []
        self.rows: list[int] = []
        self._para: dict | None = None
        self._depth = 0
        self._stack: list[str] = []
        self._buf: list[str] = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        self._stack.append(tag)
        if "data-row" in a and "data-line" in a:
            self.rows.append(int(a["data-line"]))
        elif "data-line" in a:
            self._para, self._buf = a, []
        elif a.get("class") == "mg-t" and self._para is not None:
            self._depth = len(self._stack)

    def handle_endtag(self, tag):
        if self._para is not None and self._depth and len(self._stack) == self._depth:
            p = self._para
            self.found.append((int(p["data-line"]), int(p["data-pre"]), int(p["data-lead"]), "".join(self._buf)))
            self._para, self._depth = None, 0
        self._stack.pop()

    def handle_data(self, data):
        if self._para is not None and self._depth:
            self._buf.append(data)


def _bound(data: bytes, ext: Extracted, n: int) -> str:
    return slides.bind_lines(slides.render_slide(data, n).html, slides.key_lines(ext, n), ext.text)


def test_every_paragraph_on_a_slide_points_to_its_line_of_text():
    data = (DEMO / "apresentacao.pptx").read_bytes()
    ext = extract("pptx", data)
    lines = ext.text.split("\n")
    seen, rows = set(), []
    with slides.Deck(data) as deck:
        assert deck.count == 3
        first = deck.render(1)
        assert (first.width, first.height) == (960, 720) and not first.hidden
    for n in (1, 2, 3):
        parser = _Paragraphs()
        parser.feed(_bound(data, ext, n))
        for line, pre, lead, text in parser.found:
            assert lines[line - 1][pre:] == text[lead:], (n, line)  # a seleção no slide vira linha/coluna exatas
            assert ext.page_of_line(line) == n
            seen.add(line)
        rows += parser.rows
    expected = {n for n, info in enumerate(ext.lines, start=1) if info.get("key") and "r" not in info["key"]}
    assert seen == expected and rows == [15, 16, 17]


def test_keys_of_a_version_saved_before_the_keys_existed():
    data = (DEMO / "apresentacao.pptx").read_bytes()
    ext = extract("pptx", data)
    old = Extracted(ext.text, [{"page": info.get("page")} for info in ext.lines], ext.sections)  # sem as chaves
    assert slides.key_lines(old, 2) == {} and slides.key_lines(old, 2, data) == slides.key_lines(ext, 2)
    changed = Extracted(ext.text + "\nmudou", old.lines, old.sections)
    assert slides.key_lines(changed, 2, data) == {}  # texto diferente: as chaves novas não valem


def _rich_deck(tmp_path: Path) -> bytes:
    prs = pptx.Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[6])  # em branco
    slide.background.fill.solid()
    slide.background.fill.fore_color.rgb = RGBColor(0x12, 0x34, 0x56)
    box = slide.shapes.add_textbox(Inches(0.5), Inches(0.3), Inches(6), Inches(0.8))
    box.text_frame.text = "<script>alert(1)</script> & cia"
    box.text_frame.paragraphs[0].runs[0].font.size = Pt(24)
    box.text_frame.paragraphs[0].runs[0].font.bold = True
    hidden = slide.shapes.add_textbox(Inches(0.5), Inches(1.2), Inches(3), Inches(0.5))
    hidden.text_frame.text = "texto escondido"
    hidden._element.nvSpPr.cNvPr.set("hidden", "1")
    png = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 40, 30), False)
    png.clear_with(200)
    pic = slide.shapes.add_picture(io.BytesIO(png.tobytes("png")), Inches(6), Inches(1), Inches(2), Inches(1.5))
    pic._element.blipFill.blip.append(parse_xml(f'<a:alphaModFix {A_NS} amt="30000"/>'))
    free = slide.shapes.build_freeform(Inches(1), Inches(4), scale=1.0)
    free.add_line_segments([(Inches(2), Inches(4)), (Inches(1.5), Inches(5))], close=True)
    free.convert_to_shape()
    group = slide.shapes.add_group_shape()
    piece = group.shapes.add_shape(1, Inches(3), Inches(4), Inches(1), Inches(1))  # retângulo
    group._element.grpSpPr.append(parse_xml(f'<a:solidFill {A_NS}><a:srgbClr val="00FF00"/></a:solidFill>'))
    sp_pr = piece._element.spPr
    for child in list(sp_pr):
        if child.tag.endswith("}solidFill"):
            sp_pr.remove(child)
    sp_pr.insert(2, parse_xml(f'<a:grpFill {A_NS}/>'))
    piece._element.remove(piece._element.find("{http://schemas.openxmlformats.org/presentationml/2006/main}style"))
    table = slide.shapes.add_table(2, 2, Inches(5), Inches(3), Inches(4), Inches(1)).table
    table.cell(0, 0).text, table.cell(1, 0).text = "Cabeçalho", "Corpo"
    from pptx.chart.data import CategoryChartData
    from pptx.enum.chart import XL_CHART_TYPE
    chart = CategoryChartData()
    chart.categories = ["a", "b"]
    chart.add_series("s", (1, 2))
    slide.shapes.add_chart(XL_CHART_TYPE.COLUMN_CLUSTERED, Inches(0.5), Inches(5.5), Inches(3), Inches(1.5), chart)
    path = tmp_path / "rico.pptx"
    prs.save(path)
    return path.read_bytes()


def test_slide_draws_backgrounds_pictures_freeforms_groups_tables_and_charts(tmp_path):
    data = _rich_deck(tmp_path)
    page = slides.render_slide(data, 1).html
    assert "background:rgb(18,52,86)" in page  # fundo do slide
    assert "&lt;script&gt;alert(1)&lt;/script&gt; &amp; cia" in page and "<script>" not in page
    assert "font-weight:700" in page and "font-size:32px" in page  # 24 pt em negrito
    assert "texto escondido" not in page
    assert re.search(r'<img alt="" src="data:image/png;base64,[^"]+" style="[^"]*opacity:0\.3', page)
    assert re.search(r'<path d="M0 0L96 0L48 96Z"', page)  # forma livre, em px da forma
    assert re.search(r"class=\"mg-fill\" style=\"background:linear-gradient\([^\"]+clip-path:path\(evenodd,'M0 0L96 0L48 96Z'\)",
                     page)  # o degradê do tema, recortado na forma
    assert "background:rgb(0,255,0)" in page  # grpFill: a cor do grupo
    accent = "rgb(79,129,189)"  # accent1 do tema padrão do python-pptx (Office 2007)
    assert f"background:{accent}" in page and "color:rgb(0,0,0)" in page  # cabeçalho na cor do tema, corpo escuro
    assert "📊 Gráfico · veja no modo PDF" in page


def test_annotation_on_a_powerpoint_knows_its_slide_and_comes_back_from_the_slide(demo_ws):
    doc = actions.add_document(demo_ws, "apresentacao.pptx")
    assert doc.modes == ["Fonte", "Slides", "PDF"] and doc.has_pdf
    version = latest_version(demo_ws, doc.id)
    assert page_of_line(demo_ws, doc, version, doc.root, 7) == 2
    with pytest.raises(MargaError, match="não tem o slide 9"):
        slides.render_slide((DEMO / "apresentacao.pptx").read_bytes(), 9)


@needs_libreoffice
def test_libreoffice_converts_every_slide_hidden_ones_included(demo_ws, tmp_path):
    prs = pptx.Presentation(DEMO / "apresentacao.pptx")
    prs.slides[1]._element.set("show", "0")
    prs.save(demo_ws.root / "oculto.pptx")
    doc = actions.add_document(demo_ws, "oculto.pptx")
    version = latest_version(demo_ws, doc.id)
    assert office.engine_for(doc) == "libreoffice"  # sem PowerPoint (conftest), o automático usa o LibreOffice
    pdf = office.to_pdf(demo_ws, doc, version)
    assert pdf == office.pdf_path(demo_ws, doc, version, "libreoffice") and pdf.is_relative_to(demo_ws.dir)
    with pymupdf.open(pdf) as converted:
        assert converted.page_count == 3  # a página N é o slide N
        assert "Metodologia" in converted[1].get_text()
    stamp = pdf.stat().st_mtime_ns
    assert office.to_pdf(demo_ws, doc, version) == pdf and pdf.stat().st_mtime_ns == stamp  # não refaz


@needs_libreoffice
def test_libreoffice_failure_is_a_clear_error(demo_ws, monkeypatch):
    doc = actions.add_document(demo_ws, "apresentacao.pptx")
    version = latest_version(demo_ws, doc.id)
    monkeypatch.setattr(office, "PDF_FILTER", "formato_que_nao_existe")
    with pytest.raises(MargaError, match="O LibreOffice não converteu A · apresentacao.pptx em PDF"):
        office.to_pdf(demo_ws, doc, version)
    assert not office.pdf_path(demo_ws, doc, version, "libreoffice").exists()


def test_without_libreoffice_the_pdf_mode_explains_how_to_install(demo_ws, monkeypatch):
    doc = actions.add_document(demo_ws, "apresentacao.pptx")
    monkeypatch.setattr(office, "soffice", lambda: None)
    assert office.engine_for(doc) is None
    with pytest.raises(MargaError, match="instale o LibreOffice"):
        office.to_pdf(demo_ws, doc, latest_version(demo_ws, doc.id))


def test_powerpoint_exports_a_copy_with_every_slide_and_is_the_default(demo_ws, monkeypatch, tmp_path):
    prs = pptx.Presentation(DEMO / "apresentacao.pptx")
    prs.slides[1]._element.set("show", "0")
    prs.save(demo_ws.root / "oculto.pptx")
    doc = actions.add_document(demo_ws, "oculto.pptx")
    monkeypatch.setattr(office, "POWERPOINT_APP", tmp_path)  # "instalado"
    monkeypatch.setattr(office.sys, "platform", "darwin")
    calls = []

    def fake_osascript(cmd, input, **kwargs):  # o PowerPoint de mentira: confere a cópia e "exporta"
        src, out = Path(cmd[2]), Path(cmd[3])
        calls.append(input)
        assert src.parent == office.exchange_dir() and 'show="0"' not in extract_slide2(src)
        out.write_bytes(b"%PDF-1.4 exportado")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(office.subprocess, "run", fake_osascript)
    assert office.engine_for(doc) == "powerpoint"
    pdf = office.to_pdf(demo_ws, doc, latest_version(demo_ws, doc.id))
    assert pdf.name == "slides-powerpoint.pdf" and pdf.read_bytes().startswith(b"%PDF")
    assert "save deck in pdfFile as save as PDF" in calls[0] and not list(office.exchange_dir().iterdir())
    doc.engine = "libreoffice"
    assert office.engine_for(doc) == ("libreoffice" if office.available() else "powerpoint")


def extract_slide2(path: Path) -> str:
    with zipfile.ZipFile(path) as zf:
        return zf.read("ppt/slides/slide2.xml").decode()


def test_freeform_filled_with_a_cropped_picture_and_font_weights(tmp_path):
    prs = pptx.Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    free = slide.shapes.build_freeform(Inches(1), Inches(1), scale=1.0)
    free.add_line_segments([(Inches(3), Inches(1)), (Inches(2), Inches(3))], close=True)
    shape = free.convert_to_shape()
    png = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 8, 8), False)
    _, rid = slide.part.get_or_add_image_part(io.BytesIO(png.tobytes("png")))
    sp_pr = shape._element.spPr
    for child in list(sp_pr):
        if child.tag.endswith("Fill"):
            sp_pr.remove(child)
    sp_pr.insert(2, parse_xml(f'<a:blipFill {A_NS} xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/'
                              f'relationships"><a:blip r:embed="{rid}"/><a:stretch><a:fillRect t="-100000"/></a:stretch>'
                              "</a:blipFill>"))
    box = slide.shapes.add_textbox(Inches(4), Inches(4), Inches(3), Inches(1))
    box.text_frame.text = "título"
    box.text_frame.paragraphs[0].runs[0].font.name = "Bai Jamjuree Medium"
    path = tmp_path / "livre.pptx"
    prs.save(path)
    page = slides.render_slide(path.read_bytes(), 1).html
    assert re.search(r"class=\"mg-fill\" style=\"background:url\('data:image/png;base64,[^']+'\) 0% 100% / 100% 200% "
                     r"no-repeat;clip-path:path\(evenodd,'M0 0L192 0L96 192Z'\)", page)  # imagem recortada na forma
    assert "font-family:'Bai Jamjuree Medium', 'Bai Jamjuree'," in page and "font-weight:500" in page


# ---------------------------------------------- comentário no PDF → lugar exato no .pptx
def _twins(path: Path) -> dict:
    """A mesma frase em duas caixas e numa tabela, mais um grupo (com quebra de linha) e notas."""
    prs = pptx.Presentation()
    prs.slide_width, prs.slide_height = Inches(10), Inches(5.625)
    slide = prs.slides.add_slide(prs.slide_layouts[5])  # só título
    slide.shapes.title.text = "Receita do trimestre"
    boxes = []
    for top in (1.3, 2.5):
        box = slide.shapes.add_textbox(Inches(0.5), Inches(top), Inches(4), Inches(0.8))
        box.text_frame.text = "Receita cresceu 12%"
        boxes.append(box)
    group = slide.shapes.add_group_shape()
    inner = group.shapes.add_textbox(Inches(5.5), Inches(1.3), Inches(4), Inches(0.8))
    inner.text_frame.paragraphs[0].text = "  Meta\vrevisada"  # espaços no começo e uma quebra de linha
    table = slide.shapes.add_table(2, 2, Inches(5.5), Inches(2.6), Inches(4), Inches(1)).table
    for (r, c), text in {(0, 0): "Região", (0, 1): "Receita", (1, 0): "Sul", (1, 1): "Receita cresceu 12%"}.items():
        table.cell(r, c).text = text
    slide.notes_slide.notes_text_frame.text = "Falar devagar"
    prs.save(path)
    return {"boxes": [b.shape_id for b in boxes], "group": group.name, "inner": inner.shape_id, "box2": boxes[1]}


def _find_shape(shapes, sid):
    for shape in shapes:
        if shape.shape_id == sid:
            return shape
        if shape.shape_type == pptx.enum.shapes.MSO_SHAPE_TYPE.GROUP and (found := _find_shape(shape.shapes, sid)):
            return found
    return None


def test_comments_tell_the_agent_the_exact_shape_paragraph_and_chars_in_the_pptx(demo_ws):
    from marga.core import export
    from marga.core.anchors import anchor_from_offsets, lines_anchor
    from marga.core.versions import extracted_text

    info = _twins(demo_ws.root / "gemeos.pptx")
    doc = actions.add_document(demo_ws, "gemeos.pptx")
    v = latest_version(demo_ws, doc.id)
    text = extracted_text(demo_ws, doc, v, doc.root).text
    second = text.find("Receita cresceu 12%", text.find("Receita cresceu 12%") + 1)  # a segunda caixa
    row = text.index("| Sul |")
    lines = text.split("\n")
    anchors = {
        "segunda": anchor_from_offsets(text, doc.root, v.n, second + 8, second + 19, 1),  # "cresceu 12%"
        "grupo": lines_anchor(text, doc.root, v.n, lines.index("Meta revisada") + 1, lines.index("Meta revisada") + 1),
        "tabela": anchor_from_offsets(text, doc.root, v.n, row + 2, row + 15),  # "Sul | Receita"
        "notas": lines_anchor(text, doc.root, v.n, lines.index("Falar devagar") + 1, lines.index("Falar devagar") + 1),
    }
    ids = {name: actions.create_annotation(demo_ws, doc.id, a, "corrigir", name).id for name, a in anchors.items()}
    bundle = export.build_bundle(demo_ws, 1)
    entries = {a["id"]: a for a in bundle["annotations"]}

    deck = pptx.Presentation(demo_ws.root / "gemeos.pptx")
    for entry in entries.values():
        assert entry["pptx"]
        for t in entry["pptx"]:  # o que o agente faz com o python-pptx acha exatamente o trecho comentado
            slide = deck.slides[t["slide"] - 1]
            if t.get("notes"):
                frame = slide.notes_slide.notes_text_frame
            else:
                shape = _find_shape(slide.shapes, t["shape_id"])
                frame = shape.table.cell(*t["table_cell"]).text_frame if "table_cell" in t else shape.text_frame
            assert frame.paragraphs[t["paragraph"]].text[slice(*t["chars"])].replace("\v", " ") == t["text"]
    [segunda] = entries[ids["segunda"]]["pptx"]
    assert segunda["shape_id"] == info["boxes"][1] and segunda["chars"] == [8, 19]  # a segunda caixa, não a primeira
    [grupo] = entries[ids["grupo"]]["pptx"]
    assert grupo["groups"] == [info["group"]] and grupo["chars"] == [2, 15]  # depois dos espaços do começo
    assert [t["table_cell"] for t in entries[ids["tabela"]]["pptx"]] == [[1, 0], [1, 1]]
    assert entries[ids["notas"]]["pptx"][0]["notes"] is True
    md = export.render_markdown(bundle)
    assert f"slide 1 · forma {info['boxes'][1]} “TextBox" in md and "caracteres 8–19: `cresceu 12%`" in md


@needs_libreoffice
def test_a_click_on_a_repeated_phrase_in_the_pdf_selects_the_shape_that_was_clicked(demo_ws):
    from marga.core import slidepdf
    from marga.core.anchors import anchor_offsets, lines_anchor
    from marga.core.versions import extracted_text
    from marga.core.textmap import to_linecol

    info = _twins(demo_ws.root / "gemeos.pptx")
    doc = actions.add_document(demo_ws, "gemeos.pptx")
    v = latest_version(demo_ws, doc.id)
    ext = extracted_text(demo_ws, doc, v, doc.root)
    linked = slidepdf.slide_map(office.to_pdf(demo_ws, doc, v), 1, ext)
    box = info["box2"]
    left, top, bottom = box.left / 12700, box.top / 12700, (box.top + box.height) / 12700  # pontos do PDF
    start, _ = linked.at(left + 30, top + 14)
    line = to_linecol(ext.text, start)[0]
    assert ext.lines[line - 1]["key"].split(":")[0] == str(box.shape_id)  # a frase repetida, na caixa clicada
    boxes = linked.boxes(*anchor_offsets(ext.text, lines_anchor(ext.text, doc.root, v.n, line, line)))
    assert boxes and all(top - 2 <= b[1] and b[3] <= bottom + 2 for b in boxes)  # a marca só nela
    # O PDF juntou palavras de formas vizinhas ("12meses"): cada uma continua ligada ao seu trecho.
    pieces = slidepdf.align([(0, 0, 70, 10, "12meses", 0, 0, 0)], [(5, 7, "12"), (20, 25, "meses")])
    assert [(p[4], p[5]) for p in pieces] == [(5, 7), (20, 25)] and pieces[0][2] == pieces[1][0] == 20
