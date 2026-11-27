"""Alterações sem pedido: detecção (vN → vN+1), gravação no registro, decisões e "Pedir para refazer"."""

import pytest

from marga.core import actions, export, unrequested
from marga.core.anchors import lines_anchor, make_anchor
from marga.core.models import UnrequestedCheck
from marga.core.unrequested import Region, detect, draft_comment, highlights
from marga.core.versions import latest_version, version_text
from marga.core.workspace import MargaError

F = "doc/a.tex"


def lines(text: str, ann: str, type_: str, role: str, first: int, last: int | None = None) -> Region:
    return Region(ann, type_, role, lines_anchor(text, F, 1, first, last or first))


def exact(text: str, ann: str, type_: str, role: str, quote: str) -> Region:
    start = text.index(quote)
    line = text.count("\n", 0, start) + 1
    col = start - (text.rfind("\n", 0, start) + 1)
    return Region(ann, type_, role, make_anchor(text, F, 1, line, col, line, col + len(quote)))


def run(old: str, new: str, *regions: Region):
    return detect({F: old}, {F: new}, list(regions), previous=1, version=2, round_n=1)


def check_of(items) -> UnrequestedCheck:
    return UnrequestedCheck(version=2, previous=1, round=1, items=items)


# ---------------------------------------------------------------- detecção pura
def test_change_on_a_requested_line_is_not_flagged():
    old = "Primeira linha com erro.\nSegunda linha certa.\n"
    new = "Primeira linha sem erro.\nSegunda linha certa.\n"
    assert run(old, new, lines(old, "c1", "corrigir", "pedido", 1)) == []


def test_change_outside_the_comment_lines_is_flagged():
    old = "Primeira linha com erro.\nSegunda linha certa.\n"
    new = "Primeira linha sem erro.\nSegunda linha mudada.\n"
    [item] = run(old, new, lines(old, "c1", "corrigir", "pedido", 1))
    assert (item.n, item.kind, item.file) == (1, "sem_pedido", F)
    assert (item.old.quote, item.new.quote, item.old.start_line, item.new.version) == ("certa", "mudada", 2, 2)
    assert item.reason == "fora dos comentários da rodada 1" and item.related == []
    assert item.status == "pendente" and item.is_pending


def test_reflowed_paragraph_and_spaces_are_not_changes():
    old = "Um parágrafo longo que o agente\nreformatou sem mudar palavras.\n"
    new = "Um parágrafo longo que o agente reformatou\nsem   mudar\npalavras.\n"
    assert run(old, new) == []


def test_block_with_a_requested_line_flags_only_the_other_line():
    old = "alfa beta\ngama delta\n"
    new = "alfa BETA\ngama DELTA\n"
    [item] = run(old, new, lines(old, "c1", "reescrever", "pedido", 1))
    assert (item.old.quote, item.new.quote, item.old.start_line) == ("delta", "DELTA", 2)


def test_insertion_next_to_an_insert_comment_is_requested_even_after_a_blank_line():
    old = "Intro.\n\nMétodos.\n"
    asked = lines(old, "c1", "inserir", "pedido", 1)
    assert run(old, "Intro.\n\nParágrafo novo pedido.\n\nMétodos.\n", asked) == []
    [item] = run(old, "Intro.\n\nMétodos.\nTexto inventado.\n", asked)
    assert item.old.quote == "" and item.new.quote == "Texto inventado."
    draft = draft_comment(check_of([item]), item)
    assert (draft.type, draft.suggestion) == ("remover", None) and "inserido sem pedido" in draft.body


def test_protected_span_is_flagged_even_on_a_requested_line():
    old = "O valor é 10 \\cite{silva}.\n"
    new = "O valor é 12 \\cite{souza}.\n"
    [item] = run(old, new, lines(old, "c1", "corrigir", "pedido", 1),
                 exact(old, "c2", "nao_alterar", "protegido", "\\cite{silva}"))
    assert (item.kind, item.old.quote, item.new.quote) == ("protegido", "silva", "souza")
    assert item.reason == "trecho protegido por c2 (não alterar)" and item.related == ["c1", "c2"]
    draft = draft_comment(check_of([item]), item)
    assert draft.body.startswith("Este trecho estava protegido por c2 (não alterar). Alteração sem pedido")
    assert (draft.type, draft.suggestion) == ("corrigir", "silva")


def test_reasons_name_the_question_the_note_the_mark_and_the_partial_request():
    old = "um\ndois\ntrês\nquatro\ncinco seis\nsete oito\nnove\n"
    new = "UM\ndois\nTRÊS\nquatro\ncinco DEZ oito\nnove\n"
    items = run(old, new, lines(old, "c1", "pergunta", "contexto", 1), lines(old, "c2", "nota", "contexto", 2),
                lines(old, "m1", "marca", "contexto", 3), lines(old, "c3", "corrigir", "pedido", 5))
    assert [i.reason for i in items] == [
        "c1 era uma pergunta: responder sem editar",
        "m1 é só uma marca",
        "vai além das linhas de c3",
    ]
    note = run(old, "um\nDOIS\ntrês\nquatro\ncinco seis\nsete oito\nnove\n", lines(old, "c2", "nota", "contexto", 2))
    assert note[0].reason == "c2 é só contexto (nota)"


def test_neighbouring_changes_become_one_alert_and_distant_ones_two():
    [item] = run("um dois três quatro\n", "UM dois TRÊS quatro\n")
    assert (item.old.quote, item.new.quote) == ("um dois três", "UM dois TRÊS")
    assert highlights(item) == ([(0, 2), (8, 12)], [(0, 2), (8, 12)])
    items = run("a\nb\nc\nd\ne\n", "A\nb\nc\nd\nE\n")
    assert [(i.n, i.old.start_line) for i in items] == [(1, 1), (2, 5)]


def test_pure_removal_is_widened_to_a_neighbour_word():
    [item] = run("Os dados foram coletados em 2019 na região.\n", "Os dados foram coletados na região.\n")
    assert (item.old.quote, item.new.quote) == ("coletados em 2019", "coletados")
    draft = draft_comment(check_of([item]), item)
    assert (draft.type, draft.suggestion) == ("corrigir", "coletados em 2019") and "removido sem pedido" in draft.body
    [first] = run("Removida no começo da linha.\n", "no começo da linha.\n")
    assert (first.old.quote, first.new.quote) == ("Removida no", "no")


def test_replacement_draft_asks_to_go_back_to_the_previous_text():
    [item] = run("O ano é 2019.\n", "O ano é 2021.\n")
    draft = draft_comment(check_of([item]), item)
    assert draft.type == "corrigir" and draft.suggestion == "2019"
    assert draft.body == "Alteração sem pedido na rodada 1 (v1 → v2): volte ao texto da v1."


def test_new_and_removed_files():
    items = detect({"a.tex": "x\n", "b.tex": "y\n"}, {"a.tex": "x\n", "c.tex": "novo\n"}, [],
                   previous=1, version=2, round_n=3)
    assert [(i.n, i.kind, i.file) for i in items] == [(1, "arquivo_removido", "b.tex"), (2, "arquivo_novo", "c.tex")]
    assert items[0].new is None and items[1].old is None and items[1].new.quote == "novo"
    assert items[1].reason == "arquivo criado na rodada 3 sem comentário que o peça"
    assert draft_comment(check_of(items), items[0]) is None
    assert draft_comment(check_of(items), items[1]).type == "remover"
    remove_all = Region("c1", "remover", "pedido", lines_anchor("y\n", "b.tex", 1, 1, 1))
    items = detect({"b.tex": "y\n"}, {}, [remove_all], previous=1, version=2, round_n=3)
    assert items == []


def test_huge_block_becomes_a_single_change(monkeypatch):
    monkeypatch.setattr(unrequested, "MAX_TOKENS", 3)
    [item] = run("um dois três quatro\n", "UM dois três QUATRO\n")
    assert (item.old.quote, item.new.quote) == ("um dois três quatro", "UM dois três QUATRO")


def test_words_added_to_the_end_of_a_line_belong_to_that_line():
    old = "# Título\n\nParágrafo pedido.\n"
    asked = lines(old, "c1", "inserir", "pedido", 3)
    [item] = run(old, "# Título novo\n\nParágrafo pedido.\n", asked)
    assert (item.old.start_line, item.new.quote) == (1, "novo")
    assert run(old, "# Título\n\nAntes. Parágrafo pedido.\n", asked) == []  # na mesma linha do pedido


# ---------------------------------------------------------- fluxo no workspace
def _round_with_change(ws, new_text: str):
    """A v1 de doc/a.txt com c1 em "com erro" (linha 2), a rodada 1 exportada e o agente escrevendo ``new_text``."""
    doc = actions.add_document(ws, "doc")
    text = version_text(ws, doc, latest_version(ws, doc.id), "doc/a.txt")
    actions.create_annotation(ws, doc.id, make_anchor(text, "doc/a.txt", 1, 2, 11, 2, 19), "corrigir", "tirar o erro")
    export.export_round(ws, "")
    (ws.root / "doc" / "a.txt").write_text(new_text, encoding="utf-8")
    return doc, actions.register_version(ws, doc.id)


def test_register_records_the_unrequested_changes_of_the_round(text_ws, monkeypatch):
    doc, report = _round_with_change(text_ws, "linha um\nlinha dois sem erro\nlinha 3\n")
    assert (report.round, report.unrequested) == (1, 1)
    assert "⚠️ 1 alteração(ões) sem pedido" in report.summary()
    check = unrequested.get_check(text_ws, doc.id, 2)
    [item] = check.items
    assert (check.previous, check.round, item.old.quote, item.new.quote) == (1, 1, "três", "3")

    monkeypatch.setattr(unrequested, "detect", lambda *a, **k: pytest.fail("verificou de novo"))
    assert unrequested.check_version(text_ws, doc, report.version).items == check.items  # lida do disco
    monkeypatch.undo()
    (text_ws.dir / "docs" / doc.id / "unrequested.json").unlink()  # versão registrada antes do recurso
    assert len(unrequested.check_version(text_ws, doc, report.version).items) == 1


def test_manual_version_is_not_checked(text_ws):
    doc = actions.add_document(text_ws, "doc")
    (text_ws.root / "doc" / "a.txt").write_text("outra coisa\n", encoding="utf-8")
    report = actions.register_version(text_ws, doc.id)
    assert report.version.origin == "manual" and report.unrequested == 0 and "sem pedido" not in report.summary()
    assert unrequested.check_version(text_ws, doc, report.version) is None


def test_link_endpoints_are_requested_but_see_also_is_not(text_ws):
    a = actions.add_document(text_ws, "doc")
    b = actions.add_document(text_ws, "outro.md")
    ta = version_text(text_ws, a, latest_version(text_ws, a.id), "doc/a.txt")
    tb = version_text(text_ws, b, latest_version(text_ws, b.id), "outro.md")
    c1 = actions.create_annotation(text_ws, a.id, lines_anchor(ta, "doc/a.txt", 1, 2, 2), "corrigir", "o erro")
    m1 = actions.create_annotation(text_ws, b.id, lines_anchor(tb, "outro.md", 1, 3, 3), "marca", "")
    m2 = actions.create_annotation(text_ws, b.id, lines_anchor(tb, "outro.md", 1, 1, 1), "marca", "")
    actions.create_link(text_ws, c1.id, m1.id, "propagar")
    actions.create_link(text_ws, c1.id, m2.id, "ver_tambem")
    export.export_round(text_ws, "")
    (text_ws.root / "outro.md").write_text("# Outro título\n\nTexto do outro documento corrigido.\n", encoding="utf-8")
    report = actions.register_version(text_ws, b.id)
    [item] = unrequested.get_check(text_ws, b.id, report.version.n).items
    assert (item.old.start_line, item.new.quote, item.reason) == (1, "título", "m2 é só uma marca")


def test_decisions_accept_ignore_and_reopen(text_ws):
    doc, _ = _round_with_change(text_ws, "linha UM\nlinha dois sem erro\nlinha 3\n")
    assert [i.n for i in unrequested.get_check(text_ws, doc.id, 2).pending] == [1, 2]
    [item] = actions.decide_unrequested(text_ws, doc.id, 2, 1, "aceita")
    assert item.status == "aceita" and item.decided_at
    actions.decide_unrequested(text_ws, doc.id, 2, [1, 2], "ignorada")
    assert [i.status_label for i in unrequested.get_check(text_ws, doc.id, 2).items] == ["ignorada", "ignorada"]
    actions.decide_unrequested(text_ws, doc.id, 2, 1, "pendente")
    item = unrequested.get_check(text_ws, doc.id, 2).get(1)
    assert item.is_pending and item.decided_at is None
    for args in ((1, "comentada"), (1, "talvez"), (9, "aceita")):
        with pytest.raises(MargaError):
            actions.decide_unrequested(text_ws, doc.id, 2, *args)


def test_redo_creates_a_comment_on_the_current_version(text_ws):
    doc, _ = _round_with_change(text_ws, "linha um\nlinha dois sem erro\nlinha 3\n")
    check = unrequested.get_check(text_ws, doc.id, 2)
    draft = draft_comment(check, check.get(1))
    ann = actions.comment_unrequested(text_ws, doc.id, 2, 1, draft.type, draft.body, draft.suggestion)
    assert (ann.type, ann.anchor.version, ann.anchor.quote, ann.suggestion) == ("corrigir", 2, "3", "três")
    item = unrequested.get_check(text_ws, doc.id, 2).get(1)
    assert (item.status, item.comment, item.status_label) == ("comentada", ann.id, f"virou {ann.id}")
    assert ann.id in export.export_round(text_ws, "").annotations  # vai na próxima rodada


def test_redo_after_a_newer_version_follows_the_passage(text_ws):
    doc, _ = _round_with_change(text_ws, "linha UM\nlinha dois sem erro\nlinha 3\n")
    (text_ws.root / "doc" / "a.txt").write_text("linha UM\nlinha dois sem erro\nlinha 3\nlinha nova\n", encoding="utf-8")
    actions.register_version(text_ws, doc.id)  # v3 manual: as linhas 1 e 3 não mudaram
    ann = actions.comment_unrequested(text_ws, doc.id, 2, 2, "corrigir", "volte ao texto da v1", "três")
    assert (ann.anchor.version, ann.anchor.start_line, ann.anchor.quote) == (3, 3, "3")

    (text_ws.root / "doc" / "a.txt").write_text("linha Um\nlinha dois sem erro\nlinha 3\nlinha nova\n", encoding="utf-8")
    with pytest.raises(MargaError, match="Registre a v4"):  # disco alterado: o mesmo bloqueio de comentar
        actions.comment_unrequested(text_ws, doc.id, 2, 1, "corrigir", "volte", "um")
    actions.register_version(text_ws, doc.id)  # v4: a linha 1 mudou de novo
    with pytest.raises(MargaError, match="mudou de novo na v4"):
        actions.comment_unrequested(text_ws, doc.id, 2, 1, "corrigir", "volte", "um")
    assert unrequested.get_check(text_ws, doc.id, 2).get(1).is_pending
