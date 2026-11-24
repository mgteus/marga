"""Gera os exemplos Word e PowerPoint do demo: ``demo/relatorio.docx`` e ``demo/apresentacao.pptx``.

Usa python-docx e python-pptx, do grupo dev (o ``uv sync`` já instala):

    uv run python examples/gerar_demo_office.py

Os textos repetem números do ``artigo/`` e do ``suplemento/``, alguns divergentes de propósito (1.250 × 1.200
participantes, 2019 × 2021), para treinar comentários e links entre os tipos de documento.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

from docx import Document
from pptx import Presentation
from pptx.util import Inches

DEMO = Path(__file__).resolve().parent / "demo"
AUTHOR = "Equipe Marga"
WHEN = dt.datetime(2026, 11, 24, 9, 0, 0)


def relatorio(path: Path) -> None:
    doc = Document()
    props = doc.core_properties
    props.author = props.last_modified_by = AUTHOR
    props.title = "Relatório técnico: bicicletas compartilhadas"
    props.created = props.modified = WHEN
    props.revision = 1
    doc.add_heading("Relatório técnico: bicicletas compartilhadas", 0)
    doc.add_paragraph("Equipe Marga · novembro de 2026")
    doc.add_heading("Resumo", 1)
    doc.add_paragraph("Entre 2019 e 2021, o sistema de bicicletas compartilhadas de Curitiba passou de 80 para 120 "
                      "estações. As viagens curtas aumentaram 35%, sobretudo nos bairros centrais.")
    doc.add_heading("Amostra", 1)
    doc.add_paragraph("A pesquisa ouviu 1.250 participantes entre março e junho de 2021, por meio de um "
                      "questionário online.")
    doc.add_paragraph("Questionário com 18 perguntas fechadas e duas abertas", style="List Bullet")
    doc.add_paragraph("Taxa de resposta de 42%", style="List Bullet")
    doc.add_heading("Estações por região", 1)
    table = doc.add_table(rows=1, cols=2)
    table.style = "Table Grid"
    table.rows[0].cells[0].text, table.rows[0].cells[1].text = "Região", "Estações"
    for region, count in [("Centro", "70"), ("Batel", "30"), ("Água Verde", "20")]:
        cells = table.add_row().cells
        cells[0].text, cells[1].text = region, count
    doc.add_paragraph("Os números consideram apenas as estações ativas em dezembro de 2021.")
    doc.save(path)


def apresentacao(path: Path) -> None:
    prs = Presentation()
    props = prs.core_properties
    props.author = props.last_modified_by = AUTHOR
    props.title = "Bicicletas compartilhadas em Curitiba"
    props.created = props.modified = WHEN
    props.revision = 1

    capa = prs.slides.add_slide(prs.slide_layouts[0])  # Título
    capa.shapes.title.text = "Bicicletas compartilhadas em Curitiba"
    capa.placeholders[1].text = "Equipe Marga · novembro de 2026"

    metodo = prs.slides.add_slide(prs.slide_layouts[1])  # Título e conteúdo
    metodo.shapes.title.text = "Metodologia"
    body = metodo.placeholders[1].text_frame
    body.text = "Questionário online com 1.200 participantes"
    sub = body.add_paragraph()
    sub.text, sub.level = "Coleta entre março e junho de 2021", 1
    body.add_paragraph().text = "Regressão linear: viagens diárias × número de estações"
    metodo.notes_slide.notes_text_frame.text = "Conferir o número de participantes: o artigo fala em 1.250."

    resultados = prs.slides.add_slide(prs.slide_layouts[5])  # Só título
    resultados.shapes.title.text = "Resultados"
    rows = [("Indicador", "Valor"), ("Estações", "120"), ("Aumento das viagens curtas", "35%")]
    table = resultados.shapes.add_table(len(rows), 2, Inches(0.8), Inches(2.0), Inches(8.4), Inches(1.5)).table
    for r, (name, value) in enumerate(rows):
        table.cell(r, 0).text, table.cell(r, 1).text = name, value
    fonte = resultados.shapes.add_textbox(Inches(0.8), Inches(4.2), Inches(8.4), Inches(0.5))
    fonte.text_frame.text = "Fonte: Prefeitura de Curitiba (2021)"
    prs.save(path)


if __name__ == "__main__":
    relatorio(DEMO / "relatorio.docx")
    apresentacao(DEMO / "apresentacao.pptx")
    print(f"Gerados {DEMO / 'relatorio.docx'} e {DEMO / 'apresentacao.pptx'}")
