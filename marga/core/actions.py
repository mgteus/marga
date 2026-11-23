"""Operações de alto nível (adicionar documentos, comentar, linkar, registrar versões).

A interface chama só estas funções; elas também são o que os testes exercitam.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from . import anchors as anc
from .latex import DOCCLASS_RE, detect_engine, find_main_candidates
from .models import (
    COMMENT_TYPES,
    LINK_RELATIONS,
    MARK_TYPE,
    STATUSES,
    Anchor,
    Annotation,
    Document,
    Link,
    Reply,
    Version,
)
from .versions import SKIP_DIRS, doc_status, read_working_text, snapshot, version_texts
from .workspace import MargaError, Workspace

TEXT_DOC_EXTS = {".md", ".markdown", ".txt", ".rst"}


# ------------------------------------------------------------- documentos
@dataclass
class Candidate:
    path: str
    kind: str
    main: str | None
    label: str


def discover(ws: Workspace, max_depth: int = 3) -> list[Candidate]:
    """Pastas LaTeX, PDFs avulsos e arquivos de texto do workspace ainda não registrados."""
    registered = {d.root for d in ws.docs()}
    latex: dict[str, list[str]] = {}
    pdfs: list[Path] = []
    texts: list[str] = []
    for dirpath, dirnames, filenames in os.walk(ws.root):
        depth = len(Path(dirpath).relative_to(ws.root).parts)
        dirnames[:] = (
            sorted(d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".") and d != "uploads")
            if depth < max_depth
            else []
        )
        for name in sorted(filenames):
            if name.startswith("."):
                continue
            path = Path(dirpath, name)
            ext = path.suffix.lower()
            if ext == ".tex":
                try:
                    head = path.read_text(encoding="utf-8", errors="replace")[:50_000]
                except OSError:
                    continue
                if DOCCLASS_RE.search(head):
                    latex.setdefault(ws.rel(dirpath), []).append(ws.rel(path))
            elif ext == ".pdf":
                pdfs.append(path)
            elif ext in TEXT_DOC_EXTS:
                texts.append(ws.rel(path))
    if (ws.root / "uploads").is_dir():
        for path in sorted((ws.root / "uploads").iterdir()):
            if path.suffix.lower() == ".pdf":
                pdfs.append(path)
            elif path.suffix.lower() in TEXT_DOC_EXTS:
                texts.append(ws.rel(path))
            elif path.is_dir():
                mains = [ws.rel(p) for p in find_main_candidates(path)]
                if mains:
                    latex.setdefault(ws.rel(path), mains)

    out: list[Candidate] = []
    for folder, mains in sorted(latex.items()):
        if folder in registered:
            continue
        mains.sort(key=lambda m: (Path(m).name != "main.tex", m))
        shown = "./" if folder == "." else f"{folder}/"
        out.append(Candidate(folder, "latex", mains[0], f"{shown} · LaTeX ({Path(mains[0]).name})"))
    for path in pdfs:
        rel = ws.rel(path)
        if rel in registered or path.with_suffix(".tex").exists():
            continue  # PDF ao lado de um .tex homônimo é saída de compilação
        out.append(Candidate(rel, "pdf", None, f"{rel} · PDF avulso"))
    for rel in texts:
        if rel not in registered:
            out.append(Candidate(rel, "text", None, f"{rel} · texto"))
    return out


def add_document(ws: Workspace, path: str, kind: str | None = None, name: str | None = None,
                 main: str | None = None, engine: str | None = None) -> Document:
    target = ws.abspath(path)
    if not ws.contains(target):
        raise MargaError(f"{path} está fora do workspace.")
    if not target.exists():
        raise MargaError(f"{path} não existe.")
    rel = ws.rel(target)
    if kind is None:
        if target.suffix.lower() == ".pdf":
            kind = "pdf"
        elif target.suffix.lower() == ".tex" or (target.is_dir() and find_main_candidates(target)):
            kind = "latex"
        else:
            kind = "text"

    single = target.is_file()
    main_rel: str | None = None
    if kind == "latex":
        if target.is_file():
            main_rel, root_rel, single = rel, ws.rel(target.parent), False
        else:
            root_rel = rel
            if main:
                main_rel = ws.rel(ws.abspath(main))
            else:
                candidates = find_main_candidates(target)
                if not candidates:
                    raise MargaError(f"Nenhum .tex com \\documentclass em {rel}/.")
                main_rel = ws.rel(candidates[0])
        engine = engine or detect_engine(read_working_text(ws, main_rel)) or "pdflatex"
        default_name = target.name if target.is_dir() else target.parent.name
    elif kind == "pdf":
        if not single:
            raise MargaError("Para PDF avulso, escolha um arquivo .pdf.")
        root_rel, main_rel = rel, rel
        default_name = target.name
    else:
        root_rel = rel
        main_rel = rel if single else None
        default_name = target.name

    if any(d.root == root_rel for d in ws.docs()):
        raise MargaError(f"{root_rel} já está registrado.")
    if root_rel == ".":
        default_name = ws.root.name

    doc = Document(id=ws.new_doc_id(), name=(name or default_name).strip() or default_name, kind=kind,
                   root=root_rel, main=main_rel, engine=engine or "pdflatex", single=single)
    ws.save_doc(doc)
    try:
        snapshot(ws, doc, "inicial")
    except Exception:
        ws.delete_doc(doc.id)
        raise
    return doc


def save_upload(ws: Workspace, filename: str, data: bytes) -> str:
    """Grava um upload como arquivo novo em ``uploads/`` (nunca sobrescreve) e devolve o caminho relativo."""
    safe = Path(filename).name.replace(os.sep, "_") or "arquivo"
    folder = ws.root / "uploads"
    if Path(safe).suffix.lower() == ".tex":
        folder = folder / Path(safe).stem
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / safe
    stem, suffix, k = target.stem, target.suffix, 1
    while target.exists():
        k += 1
        target = folder / f"{stem}-{k}{suffix}"
    target.write_bytes(data)
    return ws.rel(target)


def remove_document(ws: Workspace, doc_id: str) -> None:
    ws.delete_doc(doc_id)


# ------------------------------------------------------------- versões
@dataclass
class RegisterReport:
    doc: str
    version: Version
    previous: Version | None
    states: dict[str, str] = field(default_factory=dict)
    imported: int = 0
    round: int | None = None

    def summary(self) -> str:
        counts: dict[str, int] = {}
        for state in self.states.values():
            counts[state] = counts.get(state, 0) + 1
        parts = [f"{self.doc} v{self.version.n} registrada"]
        if self.round:
            parts.append(f"resposta da rodada {self.round}")
        if counts:
            parts.append(", ".join(f"{n} {s}" for s, n in sorted(counts.items())))
        if self.imported:
            parts.append(f"{self.imported} nota(s) do agente importada(s)")
        return " · ".join(parts)


def register_version(ws: Workspace, doc_id: str, note: str = "") -> RegisterReport:
    """Registra o estado do disco como v(n+1) e re-ancora as anotações abertas."""
    from .export import import_response, pending_round_for

    doc = ws.get_doc(doc_id)
    status = doc_status(ws, doc)
    prev = status.latest
    if prev is not None and not status.dirty:
        raise MargaError(f"Nada mudou no disco desde a v{prev.n} de {doc.label}.")
    rnd = pending_round_for(ws, doc_id, prev.n) if prev else None
    origin = "inicial" if prev is None else ("agente" if rnd else "manual")
    new = snapshot(ws, doc, origin, note=note, round_n=rnd.n if rnd else None)

    report = RegisterReport(doc=doc_id, version=new, previous=prev, round=rnd.n if rnd else None)
    if prev is not None:
        old_texts = version_texts(ws, doc, prev)
        new_texts = version_texts(ws, doc, new)
        changed: list[Annotation] = []
        for ann in ws.annotations(doc_id):
            if not ann.is_open or ann.anchor.version != prev.n:
                continue
            res = anc.reanchor(ann.anchor, old_texts, new_texts, new.n)
            if res.state != "orfao":
                ann.anchor_history.append(ann.anchor)
                ann.anchor = res.anchor
            ann.region_state = res.state
            report.states[ann.id] = res.state
            changed.append(ann)
        ws.save_annotations(changed)
    if rnd:
        report.imported = import_response(ws, rnd.n, only_if_present=True)
    return report


def can_annotate(ws: Workspace, doc: Document, version_n: int) -> tuple[bool, str]:
    status = doc_status(ws, doc)
    if status.latest is None:
        return False, "Documento sem versões registradas."
    if version_n != status.latest.n:
        return False, f"Só é possível comentar na versão mais recente (v{status.latest.n})."
    if status.dirty:
        return False, (f"{len(status.changed)} arquivo(s) mudaram no disco desde a v{status.latest.n}. "
                       f"Registre a v{status.latest.n + 1} antes de comentar.")
    return True, ""


# ------------------------------------------------------------ anotações
def create_annotation(ws: Workspace, doc_id: str, anchor: Anchor, type_: str, body: str,
                      suggestion: str | None = None) -> Annotation:
    doc = ws.get_doc(doc_id)
    ok, why = can_annotate(ws, doc, anchor.version)
    if not ok:
        raise MargaError(why)
    kind = "mark" if type_ == MARK_TYPE else "comment"
    if kind == "comment" and type_ not in COMMENT_TYPES:
        raise MargaError(f"Tipo de comentário desconhecido: {type_}")
    body = (body or "").strip()
    if kind == "comment" and not body and type_ != "remover":
        raise MargaError("Escreva o que o agente deve fazer com este trecho.")
    if suggestion is not None and (suggestion == anchor.quote or kind == "mark"):
        suggestion = None
    ann = Annotation(id=ws.next_id("m" if kind == "mark" else "c"), doc=doc_id, kind=kind, type=type_,
                     body=body, anchor=anchor, suggestion=suggestion)
    ws.save_annotation(ann)
    return ann


def _require(ws: Workspace, ann_id: str) -> Annotation:
    ann = ws.get_annotation(ann_id)
    if ann is None:
        raise MargaError(f"Anotação {ann_id} não existe.")
    return ann


def update_annotation(ws: Workspace, ann_id: str, *, body: str | None = None, type_: str | None = None,
                      suggestion: str | None | bool = False) -> Annotation:
    ann = _require(ws, ann_id)
    if body is not None:
        ann.body = body.strip()
    if type_ is not None and not ann.is_mark:
        if type_ not in COMMENT_TYPES:
            raise MargaError(f"Tipo de comentário desconhecido: {type_}")
        ann.type = type_
    if suggestion is not False:
        ann.suggestion = suggestion if suggestion and suggestion != ann.anchor.quote else None
    ws.save_annotation(ann)
    return ann


def set_status(ws: Workspace, ann_id: str, status: str) -> Annotation:
    if status not in STATUSES:
        raise MargaError(f"Status desconhecido: {status}")
    ann = _require(ws, ann_id)
    ann.status = status
    ws.save_annotation(ann)
    return ann


def add_reply(ws: Workspace, ann_id: str, text: str, reopen: bool = False) -> Annotation:
    text = text.strip()
    if not text:
        raise MargaError("A resposta está vazia.")
    ann = _require(ws, ann_id)
    ann.thread.append(Reply(author="voce", text=text))
    if reopen:
        ann.status = "aberto"
    ws.save_annotation(ann)
    return ann


def delete_annotation(ws: Workspace, ann_id: str) -> None:
    _require(ws, ann_id)
    ws.delete_annotation(ann_id)


# ---------------------------------------------------------------- links
def create_link(ws: Workspace, source: str, target: str, relation: str, note: str = "") -> Link:
    if source == target:
        raise MargaError("Origem e destino são o mesmo trecho.")
    _require(ws, source)
    _require(ws, target)
    if relation not in LINK_RELATIONS:
        raise MargaError(f"Relação desconhecida: {relation}")
    for link in ws.links():
        if (link.source, link.target, link.relation) == (source, target, relation):
            raise MargaError(f"Esse link já existe ({link.id}).")
    link = Link(id=ws.next_id("L"), source=source, target=target, relation=relation, note=note.strip())
    ws.save_link(link)
    return link


def delete_link(ws: Workspace, link_id: str) -> None:
    ws.delete_link(link_id)
