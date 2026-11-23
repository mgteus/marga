from marga.core.diffing import changes, collapse, diff_rows
from marga.core.textmap import excerpt, find_phrase, line_starts, normalize, to_linecol, to_offset


def test_offset_roundtrip_with_unicode():
    text = "abc\ndéf 😀 x\n\nfim"
    starts = line_starts(text)
    for off in range(len(text) + 1):
        line, col = to_linecol(text, off, starts)
        assert to_offset(text, line, col, starts) == off


def test_to_offset_clamps():
    text = "ab\ncd"
    assert to_offset(text, 1, 99) == 2
    assert to_offset(text, 9, 0) == 3


def test_normalize_strips_latex_accents_and_comments():
    nt = normalize(r"A \emph{análise} em~2019 \cite{x} -- 35\% % comentário", latex=True)
    assert nt.text.strip() == "a analise em 2019 - 35%"


def test_find_phrase_exact_through_markup():
    src = r"Os dados foram \emph{coletados} em~2019 com 1.250 participantes \cite{prefeitura}."
    [m] = find_phrase(src, "coletados em 2019", latex=True)
    assert m.score == 1.0
    assert src[m.start : m.end] == r"\emph{coletados} em~2019"


def test_find_phrase_fuzzy_and_window():
    src = "primeira linha\nOs dados foram coletados em 2019 na região.\nterceira"
    [m] = find_phrase(src, "dados foram coletadas em 2019")
    assert 0.6 <= m.score < 1.0
    assert "coletados em 2019" in src[m.start : m.end]
    assert find_phrase(src, "região", start=0, end=14) == []


def test_excerpt_marks_anchored_lines():
    assert excerpt("a\nb\nc\nd\ne", 3, 3, context=1) == [(2, "b", False), (3, "c", True), (4, "d", False)]


def test_diff_rows_intraline_spans_and_collapse():
    old = "um\ndois tres\nquatro\ncinco\nseis\nsete\noito\nnove\ndez"
    new = "um\ndois TRES\nquatro\ncinco\nseis\nsete\noito\nnove\ndez"
    rows = diff_rows(old, new)
    kinds = [r.kind for r in rows]
    assert kinds.count("del") == 1 and kinds.count("add") == 1
    add = next(r for r in rows if r.kind == "add")
    assert add.new == 2 and add.text[add.spans[0][0] : add.spans[0][1]] == "TRES"
    compact = collapse(rows, context=1)
    assert compact[-1].kind == "gap"
    [change] = changes({"f": old}, {"f": new})
    assert (change.status, change.added, change.removed) == ("modificado", 1, 1)
