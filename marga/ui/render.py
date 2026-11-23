"""Pequenos utilitários de apresentação (texto curto, diffs em HTML, grafo de links)."""

from __future__ import annotations

import html
from pathlib import Path

from marga.core.diffing import collapse, diff_rows
from marga.core.models import Annotation, Document, Link


def short(text: str | None, limit: int = 80) -> str:
    flat = " ".join((text or "").split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


def short_block(text: str, limit: int = 600) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def suggestion_diff(quote: str, suggestion: str) -> str:
    return "\n".join([f"- {line}" for line in quote.split("\n")] + [f"+ {line}" for line in suggestion.split("\n")])


def anchored_lines(text: str | None, start: int, end: int, limit: int = 14) -> str:
    if text is None:
        return ""
    lines = text.split("\n")[start - 1 : end]
    if len(lines) > limit:
        lines = lines[:limit] + [f"… (+{len(lines) - limit} linhas)"]
    return "\n".join(lines)


_DIFF_CSS = """<style>
.mgd{font:12.5px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace;border:1px solid rgba(128,128,128,.35);border-radius:8px;overflow:hidden}
.mgd-r{display:flex}
.mgd-n{flex:0 0 3em;text-align:right;padding-right:.5em;color:rgba(128,128,128,.9);user-select:none}
.mgd-t{flex:1;min-width:0;white-space:pre-wrap;overflow-wrap:anywhere;padding:0 .6em}
.mgd-add{background:rgba(46,160,67,.14)} .mgd-del{background:rgba(248,81,73,.12)}
.mgd-add mark{background:rgba(46,160,67,.42);color:inherit;border-radius:2px}
.mgd-del mark{background:rgba(248,81,73,.42);color:inherit;border-radius:2px}
.mgd-gap{background:rgba(128,128,128,.1);color:rgba(128,128,128,.95);font-style:italic;padding:2px .8em}
</style>"""


def _with_spans(text: str, spans: list[tuple[int, int]]) -> str:
    if not spans:
        return html.escape(text)
    out, pos = [], 0
    for s, e in spans:
        out.append(html.escape(text[pos:s]))
        out.append(f"<mark>{html.escape(text[s:e])}</mark>")
        pos = e
    out.append(html.escape(text[pos:]))
    return "".join(out)


def diff_html(old: str | None, new: str, context: int = 3) -> str:
    rows = collapse(diff_rows(old if old is not None else "", new), context)
    parts = [_DIFF_CSS, '<div class="mgd">']
    if not rows:
        parts.append('<div class="mgd-gap">sem diferenças</div>')
    for row in rows:
        if row.kind == "gap":
            parts.append(f'<div class="mgd-gap">⋯ {html.escape(row.text)}</div>')
            continue
        cls = {"add": "mgd-add", "del": "mgd-del"}.get(row.kind, "")
        sign = {"add": "+", "del": "−"}.get(row.kind, " ")
        parts.append(
            f'<div class="mgd-r {cls}"><span class="mgd-n">{row.old or ""}</span>'
            f'<span class="mgd-n">{row.new or ""}</span>'
            f'<span class="mgd-t">{sign} {_with_spans(row.text, row.spans)}</span></div>'
        )
    parts.append("</div>")
    return "".join(parts)


def _dot_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace('"', '\\"')


def links_dot(links: list[Link], anns: dict[str, Annotation], docs: dict[str, Document]) -> str:
    used = [i for lk in links for i in (lk.source, lk.target) if i in anns]
    lines = [
        "digraph marga {",
        '  rankdir=LR; bgcolor="transparent";',
        '  node [shape=box, style="rounded,filled", fontname="Helvetica", fontsize=11, color="#8c959f"];',
        '  edge [fontname="Helvetica", fontsize=10, color="#8c959f", fontcolor="#8c959f"];',
    ]
    for doc_id, doc in docs.items():
        members = sorted({i for i in used if anns[i].doc == doc_id})
        if not members:
            continue
        lines.append(f'  subgraph cluster_{doc_id} {{ label="{_dot_escape(doc.label)}"; style="rounded"; '
                     f'color="#8c959f"; fontcolor="#8c959f"; fontname="Helvetica"; fontsize=12;')
        for ann_id in members:
            a = anns[ann_id]
            fill = "#e7dcfb" if a.is_mark else ("#fff1b8" if a.is_open else "#eaeef2")
            label = f"{a.id} · {Path(a.anchor.file).name}:{a.anchor.start_line}\\n{_dot_escape(short(a.anchor.quote, 34))}"
            lines.append(f'    {a.id} [label="{label}", fillcolor="{fill}"];')
        lines.append("  }")
    for lk in links:
        if lk.source in anns and lk.target in anns:
            lines.append(f'  {lk.source} -> {lk.target} [label="{_dot_escape(lk.relation)}"];')
    lines.append("}")
    return "\n".join(lines)
