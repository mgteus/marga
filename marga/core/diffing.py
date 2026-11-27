"""Diff entre versões: linhas para as views (com destaque intra-linha), diff unificado e estatísticas."""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field

_TOKEN = re.compile(r"\w+|\s+|[^\w\s]")


@dataclass
class DiffRow:
    kind: str  # "ctx" | "add" | "del" | "gap"
    old: int | None
    new: int | None
    text: str
    spans: list[tuple[int, int]] = field(default_factory=list)  # trechos alterados (code points)


def changed_spans(a: str, b: str) -> tuple[list[tuple[int, int]], list[tuple[int, int]]]:
    """Trechos que diferem entre dois textos, por palavra (offsets em ``a`` e em ``b``)."""
    ta = [m.span() for m in _TOKEN.finditer(a)]
    tb = [m.span() for m in _TOKEN.finditer(b)]
    sm = difflib.SequenceMatcher(None, [a[s:e] for s, e in ta], [b[s:e] for s, e in tb], autojunk=False)
    spans_a: list[tuple[int, int]] = []
    spans_b: list[tuple[int, int]] = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        if i2 > i1:
            spans_a.append((ta[i1][0], ta[i2 - 1][1]))
        if j2 > j1:
            spans_b.append((tb[j1][0], tb[j2 - 1][1]))
    return spans_a, spans_b


def line_opcodes(old: str, new: str):
    return difflib.SequenceMatcher(None, old.split("\n"), new.split("\n"), autojunk=False).get_opcodes()


def diff_rows(old: str | None, new: str) -> list[DiffRow]:
    """Todas as linhas de ``new`` com as removidas de ``old`` intercaladas (estilo diff unificado)."""
    new_lines = new.split("\n")
    if old is None:
        return [DiffRow("ctx", None, i + 1, t) for i, t in enumerate(new_lines)]
    old_lines = old.split("\n")
    rows: list[DiffRow] = []
    for tag, i1, i2, j1, j2 in line_opcodes(old, new):
        if tag == "equal":
            rows.extend(DiffRow("ctx", i1 + k + 1, j1 + k + 1, new_lines[j1 + k]) for k in range(i2 - i1))
            continue
        dels = [DiffRow("del", i + 1, None, old_lines[i]) for i in range(i1, i2)]
        adds = [DiffRow("add", None, j + 1, new_lines[j]) for j in range(j1, j2)]
        if tag == "replace":
            for k in range(min(len(dels), len(adds))):
                dels[k].spans, adds[k].spans = changed_spans(dels[k].text, adds[k].text)
        rows.extend(dels)
        rows.extend(adds)
    return rows


def collapse(rows: list[DiffRow], context: int = 3) -> list[DiffRow]:
    """Esconde trechos inalterados longos, deixando ``context`` linhas em volta das mudanças."""
    changed = [i for i, r in enumerate(rows) if r.kind in ("add", "del")]
    if not changed:
        return []
    keep = set()
    for i in changed:
        keep.update(range(max(0, i - context), min(len(rows), i + context + 1)))
    out: list[DiffRow] = []
    hidden = 0
    for i, row in enumerate(rows):
        if i in keep:
            if hidden:
                out.append(DiffRow("gap", None, None, f"{hidden} linha(s) sem alteração"))
                hidden = 0
            out.append(row)
        else:
            hidden += 1
    if hidden:
        out.append(DiffRow("gap", None, None, f"{hidden} linha(s) sem alteração"))
    return out


def unified(old: str, new: str, old_label: str, new_label: str, context: int = 3) -> str:
    lines = difflib.unified_diff(
        old.split("\n"), new.split("\n"), fromfile=old_label, tofile=new_label, n=context, lineterm=""
    )
    return "\n".join(lines)


@dataclass
class FileChange:
    path: str
    status: str  # "modificado" | "novo" | "removido"
    added: int
    removed: int


def changes(old_texts: dict[str, str], new_texts: dict[str, str]) -> list[FileChange]:
    out: list[FileChange] = []
    for path in sorted(set(old_texts) | set(new_texts)):
        old, new = old_texts.get(path), new_texts.get(path)
        if old == new:
            continue
        if old is None:
            out.append(FileChange(path, "novo", line_count(new), 0))
        elif new is None:
            out.append(FileChange(path, "removido", 0, line_count(old)))
        else:
            added = removed = 0
            for tag, i1, i2, j1, j2 in line_opcodes(old, new):
                if tag != "equal":
                    removed += i2 - i1
                    added += j2 - j1
            out.append(FileChange(path, "modificado", added, removed))
    return out


def line_count(text: str) -> int:
    return text.count("\n") + 1
