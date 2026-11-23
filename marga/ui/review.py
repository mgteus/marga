"""Aba Revisão: duas colunas (fonte ou PDF), seleção, comentários, links e compilação paralela."""

from __future__ import annotations

import base64
import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path

import streamlit as st

from marga.core import actions, latex, synctex
from marga.core import pdf as pdfmod
from marga.core.anchors import anchor_from_offsets, lines_anchor, make_anchor
from marga.core.diffing import diff_rows
from marga.core.models import (
    AGENT_STATUSES,
    ANNOTATION_TYPES,
    COMMENT_TYPES,
    LINK_RELATIONS,
    MARK_TYPE,
    REGION_STATES,
    STATUSES,
    Anchor,
    Annotation,
    Document,
    Version,
)
from marga.core.textmap import find_phrase, latex_to_plain, line_end, line_starts
from marga.core.versions import blob_path, get_version, load_versions, previous_version, version_text
from marga.core.workspace import MargaError, Workspace
from marga.ui import state
from marga.ui.components import event, pdf_view, source_view
from marga.ui.render import short, short_block, suggestion_diff

SIDES = ("L", "R")
SOURCE_HEIGHT = 640
ZOOM = 2.0
KIND_ICON = {"latex": "📄", "pdf": "📕", "text": "📝"}


@dataclass
class View:
    side: str
    doc: Document
    mode: str  # "Fonte" | "PDF"
    versions: list[Version]
    version: Version

    @property
    def n(self) -> int:
        return self.version.n

    @property
    def latest(self) -> Version:
        return self.versions[-1]


# ------------------------------------------------------------- cache PDF
@st.cache_data(max_entries=64, show_spinner=False)
def _page_png(path: str, mtime: float, page: int, zoom: float) -> str:
    data = pdfmod.render_png(path, page, zoom)
    return "data:image/png;base64," + base64.b64encode(data).decode("ascii")


@st.cache_data(max_entries=256, show_spinner=False)
def _page_count(path: str, mtime: float) -> int:
    return pdfmod.page_count(path)


@st.cache_data(max_entries=512, show_spinner=False)
def _page_size(path: str, mtime: float, page: int) -> tuple[float, float]:
    return pdfmod.page_size(path, page)


def _rev(payload) -> str:
    return hashlib.sha1(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]


def _doc_rel(doc: Document, path: str) -> str:
    return path if doc.single else os.path.relpath(path, doc.root)


# ================================================================ entrada
def render(ws: Workspace) -> None:
    docs = ws.docs()
    if not docs:
        st.info("Adicione um documento na barra lateral (➕ Adicionar documento) para começar.")
        return
    ss = st.session_state
    ss.setdefault("show_diff", True)
    ss.setdefault("show_threads", True)
    bar = st.columns([2.3, 2.2, 2.4, 5.1], vertical_alignment="center")
    force = bar[0].button("▶ Compilar visíveis", type="primary", disabled=not latex.can_compile(),
                          help="Compila ao mesmo tempo os documentos LaTeX mostrados nas duas colunas.")
    bar[1].toggle("Diff vs anterior", key="show_diff")
    bar[2].toggle("Comentários no texto", key="show_threads")
    bar[3].caption("Fonte: arraste para selecionar, ou clique/arraste nos números de linha (shift estende). "
                   "PDF: clique = linha do fonte, arraste = trecho.")

    head = st.columns(2, gap="medium")
    views = [_controls(ws, side, docs, head[i]) for i, side in enumerate(SIDES)]
    _compile(ws, views, force)

    owners: dict[str, str] = {}
    for v in views:
        owners.setdefault(v.doc.id, v.side)
    body = st.columns(2, gap="medium")
    for i, v in enumerate(views):
        with body[i]:
            if v.mode == "PDF":
                _pdf_panel(ws, v)
            else:
                _source_panel(ws, v, ss["show_diff"], ss["show_threads"])
            if owners[v.doc.id] == v.side:
                _selection_panel(ws, v)
                _annotation_list(ws, v)
    st.divider()
    _link_form(ws, views)


def _controls(ws: Workspace, side: str, docs: list[Document], container) -> View:
    ss = st.session_state
    by_id = {d.id: d for d in docs}
    ids = list(by_id)
    key_doc = f"{side}_doc"
    if ss.get(key_doc) not in ids:
        ss[key_doc] = ids[0] if side == "L" or len(ids) == 1 else ids[1]
    with container:
        c = st.columns([5, 3, 2.4], vertical_alignment="bottom")
        doc_id = c[0].selectbox("Documento", ids, key=key_doc,
                                format_func=lambda i: f"{KIND_ICON[by_id[i].kind]} {by_id[i].label}")
        doc = by_id[doc_id]
        modes = ["Fonte"] if doc.kind == "text" else ["Fonte", "PDF"]
        key_mode = f"{side}_mode_{doc_id}"
        if ss.get(key_mode) not in modes:
            ss[key_mode] = "PDF" if side == "R" and len(ids) == 1 and "PDF" in modes else "Fonte"
        mode = c[1].segmented_control("Modo", modes, key=key_mode) or modes[0]
        versions = load_versions(ws, doc_id)
        ns = [v.n for v in versions]
        key_ver = f"{side}_ver_{doc_id}"
        if ss.get(key_ver) not in ns:
            ss[key_ver] = ns[-1]
        n = c[2].selectbox("Versão", list(reversed(ns)), key=key_ver,
                           format_func=lambda k: f"v{k}" + (" · atual" if k == ns[-1] else ""))
    return View(side, doc, mode, versions, next(v for v in versions if v.n == n))


def _compile(ws: Workspace, views: list[View], force: bool) -> None:
    jobs: list[tuple[Document, Version]] = []
    seen: set[tuple[str, int]] = set()
    for v in views:
        if v.doc.kind != "latex" or (v.doc.id, v.n) in seen:
            continue
        if not force and (v.mode != "PDF" or latex.load_result(ws, v.doc.id, v.n) is not None):
            continue
        seen.add((v.doc.id, v.n))
        jobs.append((v.doc, v.version))
    if not jobs:
        return
    if not latex.can_compile():
        st.warning("latexmk não está no PATH; instale o TeX Live para compilar.")
        return
    names = " e ".join(f"{d.id} v{ver.n}" for d, ver in jobs)
    with st.spinner(f"Compilando {names}{' em paralelo' if len(jobs) > 1 else ''}…"):
        results = latex.compile_many(ws, jobs, force=force)
    for (doc, ver), res in zip(jobs, results):
        if isinstance(res, Exception):
            st.error(f"{doc.label} v{ver.n}: {res}")
        elif force:
            msg = "compilado" if res.ok else f"{len(res.errors)} erro(s)"
            state.flash("ok" if res.ok else "warn", f"{doc.id} v{ver.n}: {msg} em {res.duration:.1f} s")


# ================================================================ fonte
def _annotation_payload(ws: Workspace, doc_id: str, version_n: int, file: str) -> list[dict]:
    labels = _link_labels(ws)
    out = []
    for a in ws.annotations(doc_id):
        an = a.anchor_at(version_n)
        if an is None or an.file != file:
            continue
        last = a.last_agent_reply()
        out.append({
            "id": a.id, "kind": a.kind, "label": a.type_label, "status": a.status,
            "sl": an.start_line, "sc": an.start_col, "el": an.end_line, "ec": an.end_col,
            "body": short(a.body, 300),
            "agent": short(f"{AGENT_STATUSES.get(last.agent_status, '')} {last.text}".strip(), 220) if last else "",
            "links": labels.get(a.id, []),
            "state": REGION_STATES.get(a.region_state, "") if an is a.anchor and a.region_state else "",
        })
    return out


def _link_labels(ws: Workspace) -> dict[str, list[str]]:
    anns = {a.id: a for a in ws.annotations()}
    labels: dict[str, list[str]] = {}
    for lk in ws.links():
        src, dst = anns.get(lk.source), anns.get(lk.target)
        if not src or not dst:
            continue
        labels.setdefault(src.id, []).append(f"{lk.id} {lk.relation} → {dst.id} ({dst.doc} · {Path(dst.anchor.file).name}:{dst.anchor.start_line})")
        labels.setdefault(dst.id, []).append(f"{lk.id} {src.id} ({src.doc} · {Path(src.anchor.file).name}:{src.anchor.start_line}) {lk.relation} →")
    return labels


def _source_panel(ws: Workspace, v: View, show_diff: bool, show_threads: bool) -> None:
    ss = st.session_state
    doc = v.doc
    files = sorted(v.version.files)
    file = files[0]
    if len(files) > 1:
        key_file = f"{v.side}_file_{doc.id}_{v.n}"
        if ss.get(key_file) not in files:
            ss[key_file] = doc.main if doc.main in files else files[0]
        counts: dict[str, int] = {}
        for a in ws.annotations(doc.id):
            an = a.anchor_at(v.n)
            if an and a.is_open:
                counts[an.file] = counts.get(an.file, 0) + 1
        file = st.selectbox("Arquivo", files, key=key_file, label_visibility="collapsed",
                            format_func=lambda p: _doc_rel(doc, p) + (f"  · {counts[p]} 💬" if counts.get(p) else ""))
    text = version_text(ws, doc, v.version, file)
    prev = previous_version(ws, doc.id, v.n) if show_diff else None
    old = None
    if prev is not None:
        old = version_text(ws, doc, prev, file) if file in prev.files else ""
    rows = diff_rows(old, text)

    sel = state.get_selection(doc.id)
    sel_payload = None
    if sel and sel[0].file == file and sel[0].version == v.n:
        a = sel[0]
        sel_payload = {"sl": a.start_line, "sc": a.start_col, "el": a.end_line, "ec": a.end_col}
    data = {
        "rows": [[{"ctx": "c", "add": "a", "del": "d"}[r.kind], r.new, r.text, r.spans] for r in rows],
        "anns": _annotation_payload(ws, doc.id, v.n, file),
        "sel": sel_payload,
        "threads": show_threads,
        "focused": ss.get("focus_ann"),
        "height": SOURCE_HEIGHT,
    }
    data["rev"] = _rev(data)
    focus = ss.get("focus")
    if focus and focus["doc"] == doc.id and focus["file"] == file:
        data["focus"] = {"line": focus["line"], "nonce": focus["nonce"]}
    res = source_view(data, key=f"src_{v.side}_{doc.id}_{v.n}_{file}")

    added = sum(1 for r in rows if r.kind == "add")
    removed = sum(1 for r in rows if r.kind == "del")
    info = [f"v{v.n}", f"{len(files)} arquivo(s)"]
    if prev is not None:
        info.append(f"diff vs v{prev.n}: +{added} −{removed}")
    if v.n != v.latest.n:
        info.append("versão antiga: só leitura")
    st.caption(" · ".join(info))

    ev = event(res, "select")
    if ev and state.fresh(ev.get("nonce")):
        anchor = make_anchor(text, file, v.n, int(ev["sl"]), int(ev["sc"]), int(ev["el"]), int(ev["ec"]))
        if doc.kind == "pdf":
            anchor.page = _pdf_text(ws, v).page_of_line(anchor.start_line)
        if anchor.quote.strip():
            state.set_selection(doc.id, anchor, v.side)
        st.rerun()
    ev = event(res, "focus")
    if ev and state.fresh(ev.get("nonce")):
        ss["focus_ann"] = ev.get("id")
        st.rerun()


# ================================================================== PDF
def _pdf_text(ws: Workspace, v: View) -> pdfmod.PdfText:
    return pdfmod.pdf_text(ws, v.version.files[v.doc.root])


def _shift_page(key: str, delta: int, total: int) -> None:
    st.session_state[key] = max(1, min(total, int(st.session_state.get(key, 1)) + delta))


def _pdf_panel(ws: Workspace, v: View) -> None:
    ss = st.session_state
    doc = v.doc
    result = None
    if doc.kind == "text":
        st.info("Documento de texto: não há PDF.")
        return
    if doc.kind == "pdf":
        pdf_path = blob_path(ws, v.version.files[doc.root])
    else:
        result = latex.load_result(ws, doc.id, v.n)
        if result is None:
            st.info("Ainda não compilado. Clique em ▶ Compilar visíveis.")
            return
        _compile_status(ws, v, result)
        if not result.has_pdf:
            return
        pdf_path = Path(result.pdf)

    mtime = pdf_path.stat().st_mtime
    total = _page_count(str(pdf_path), mtime)
    key_page = f"{v.side}_page_{doc.id}_{v.n}"
    if not isinstance(ss.get(key_page), int) or not 1 <= ss[key_page] <= total:
        ss[key_page] = 1
    nav = st.columns([1, 2, 1, 4], vertical_alignment="center")
    nav[0].button("◀", key=f"{key_page}_prev", on_click=_shift_page, args=(key_page, -1, total),
                  disabled=ss[key_page] <= 1)
    nav[1].number_input("Página", min_value=1, max_value=total, step=1, key=key_page, label_visibility="collapsed")
    nav[2].button("▶", key=f"{key_page}_next", on_click=_shift_page, args=(key_page, 1, total),
                  disabled=ss[key_page] >= total)
    nav[3].caption(f"de {total} · v{v.n}" + (" · PDF avulso" if doc.kind == "pdf" else ""))
    page = int(ss[key_page])

    width, height = _page_size(str(pdf_path), mtime, page)
    rects = _pdf_rects(ws, v, pdf_path, result, page)
    img_key = f"{pdf_path}:{mtime}:{page}:{ZOOM}"
    data = {"img": _page_png(str(pdf_path), mtime, page, ZOOM), "img_key": img_key, "w": width, "h": height,
            "rects": rects, "rev": _rev([rects, img_key])}
    res = pdf_view(data, key=f"pdf_{v.side}_{doc.id}_{v.n}")

    ev = event(res, "click")
    if ev and state.fresh(ev.get("nonce")):
        _pdf_pick(ws, v, pdf_path, result, page, (float(ev["x"]), float(ev["y"])), None)
    ev = event(res, "rect")
    if ev and state.fresh(ev.get("nonce")):
        rect = (float(ev["x0"]), float(ev["y0"]), float(ev["x1"]), float(ev["y1"]))
        _pdf_pick(ws, v, pdf_path, result, page, None, rect)
    ev = event(res, "focus")
    if ev and state.fresh(ev.get("nonce")):
        ss["focus_ann"] = ev.get("id")
        st.rerun()


def _merge(boxes: list[tuple]) -> list[tuple]:
    return list(dict.fromkeys(tuple(round(x, 1) for x in b) for b in boxes))


def _anchor_boxes(ws: Workspace, v: View, pdf_path: Path, result, anchor: Anchor, page: int) -> list[tuple]:
    if v.doc.kind == "pdf":
        pt = _pdf_text(ws, v)
        line_boxes = pt.line_boxes(page, anchor.start_line, anchor.end_line)
        if line_boxes and "\n" not in anchor.quote and anchor.quote.strip():
            top, bottom = min(b[1] for b in line_boxes) - 2, max(b[3] for b in line_boxes) + 2
            hits = [h for h in pdfmod.search_rects(pdf_path, page, anchor.quote) if h[1] >= top and h[3] <= bottom]
            if hits:
                return _merge(hits)
        return _merge(line_boxes)
    if result is None or not synctex.available():
        return []
    src = Path(result.src_dir) / os.path.relpath(anchor.file, v.doc.root)
    region = []
    for line in range(anchor.start_line, min(anchor.end_line, anchor.start_line + 30) + 1):
        region += [b.rect for b in synctex.forward(pdf_path, src, line, result.src_dir) if b.page == page]
    if not region:
        return []
    plain = latex_to_plain(anchor.quote)
    if 3 <= len(plain) <= 300:
        top, bottom = min(b[1] for b in region) - 4, max(b[3] for b in region) + 4
        hits = [h for h in pdfmod.search_rects(pdf_path, page, plain) if h[1] >= top and h[3] <= bottom]
        if hits:
            return _merge(hits)
    return _merge(region)


def _pdf_rects(ws: Workspace, v: View, pdf_path: Path, result, page: int) -> list[dict]:
    focus_ann = st.session_state.get("focus_ann")
    items: list[tuple[str, str, Anchor]] = []
    for a in ws.annotations(v.doc.id):
        an = a.anchor_at(v.n)
        if an is not None:
            items.append((a.id, "m" if a.is_mark else ("a" if a.is_open else "c"), an))
    sel = state.get_selection(v.doc.id)
    if sel and sel[0].version == v.n:
        items.append(("", "sel", sel[0]))
    out = []
    for ident, kind, anchor in items:
        for i, box in enumerate(_anchor_boxes(ws, v, pdf_path, result, anchor, page)):
            out.append({"x0": box[0], "y0": box[1], "x1": box[2], "y1": box[3], "id": ident,
                        "kind": kind + (" mg-focus" if ident and ident == focus_ann else ""),
                        "label": ident if i == 0 else ""})
    return out


def _hit_to_file(v: View, result, hits: list[tuple[Path, int]]) -> tuple[str, int] | None:
    src_dir = os.path.normpath(result.src_dir)
    for path, line in hits:
        rel = os.path.relpath(path, src_dir)
        if rel.startswith(".."):
            continue
        ws_rel = Path(os.path.normpath(Path(v.doc.root) / rel)).as_posix()
        if ws_rel in v.version.files:
            return ws_rel, line
    return None


def _pdf_pick(ws: Workspace, v: View, pdf_path: Path, result, page: int, point, rect) -> None:
    """Clique (point) ou retângulo (rect) no PDF → seleção no fonte."""
    doc, n = v.doc, v.n
    anchor: Anchor | None = None
    if doc.kind == "pdf":
        pt = _pdf_text(ws, v)
        text, file, latex_mode = pt.text, doc.root, False
        if point:
            line = pt.line_at(page, *point)
            lines = [line] if line else []
        else:
            lines = pt.lines_in_rect(page, rect)
    else:
        if not synctex.available():
            state.flash("warn", "SyncTeX indisponível: use a seleção no fonte.")
            st.rerun()
        probes = [point] if point else [(rect[0] + 2, rect[1] + 2), (rect[2] - 2, rect[3] - 2),
                                        ((rect[0] + rect[2]) / 2, (rect[1] + rect[3]) / 2)]
        hits = []
        for px, py in probes:
            target = _hit_to_file(v, result, synctex.inverse(pdf_path, page, px, py, cwd=result.src_dir))
            if target:
                hits.append(target)
        file = hits[0][0] if hits else None
        lines = [line for f, line in hits if f == file]
        text = version_text(ws, doc, v.version, file) if file else ""
        latex_mode = True
    if not lines:
        state.flash("warn", "Não encontrei texto do fonte nesse ponto do PDF.")
        st.rerun()

    first, last = min(lines), max(lines)
    page_attr = page if doc.kind == "pdf" else None
    if rect:
        phrase = pdfmod.text_in_rect(pdf_path, page, rect)
        if phrase.strip():
            starts = line_starts(text)
            lo = starts[max(0, first - 2)]
            hi = line_end(text, min(len(starts), last + 1), starts)
            found = find_phrase(text, phrase, latex=latex_mode, start=lo, end=hi, max_results=1, min_score=0.55)
            if found:
                anchor = anchor_from_offsets(text, file, n, found[0].start, found[0].end, page_attr)
    if anchor is None:
        anchor = lines_anchor(text, file, n, first, last, page_attr)
    state.set_selection(doc.id, anchor, v.side)
    state.set_focus(doc.id, anchor.file, anchor.start_line)
    _follow(doc.id, n, anchor.file)
    st.rerun()


def _follow(doc_id: str, n: int, file: str) -> None:
    """Colunas que mostram o fonte do mesmo documento passam a exibir o arquivo da seleção."""
    ss = st.session_state
    for side in SIDES:
        if ss.get(f"{side}_doc") == doc_id and ss.get(f"{side}_ver_{doc_id}") == n:
            state.request(f"{side}_file_{doc_id}_{n}", file)


def _error_to_comment(ws: Workspace, doc_id: str, n: int, file: str, line: int, message: str) -> None:
    doc = ws.get_doc(doc_id)
    text = version_text(ws, doc, get_version(ws, doc_id, n), file)
    nonce = state.set_selection(doc_id, lines_anchor(text, file, n, line, line), "L")
    state.request(f"ty_{nonce}", "erro_compilacao")
    state.request(f"bd_{nonce}", f"Erro de compilação: {message}. Corrija para o documento compilar.")
    state.set_focus(doc_id, file, line)
    _follow(doc_id, n, file)


def _compile_status(ws: Workspace, v: View, result) -> None:
    errs, warns = result.errors, result.warnings
    if result.ok:
        st.caption(f"✅ PDF da v{v.n} · {result.engine} · {result.duration:.1f} s · {len(warns)} aviso(s)")
    else:
        st.caption(f"❌ v{v.n} · {len(errs)} erro(s) · {len(warns)} aviso(s)" + ("" if result.has_pdf else " · sem PDF"))
    if not errs and not warns:
        return
    with st.expander(f"Log da compilação: {len(errs)} erro(s), {len(warns)} aviso(s)", expanded=not result.has_pdf):
        for i, err in enumerate(errs[:30]):
            row = st.columns([7, 2], vertical_alignment="center")
            row[0].markdown(f"🔴 `{err.location}` {err.message}")
            if err.file and err.line and err.file in v.version.files and v.n == v.latest.n:
                row[1].button("→ comentar", key=f"err_{v.side}_{v.doc.id}_{v.n}_{i}", on_click=_error_to_comment,
                              args=(ws, v.doc.id, v.n, err.file, err.line, err.message))
        for warn in warns[:40]:
            where = f"linha {warn.line} · " if warn.line else ""
            st.markdown(f"🟡 {where}{warn.message}")
        if not result.ok and result.output_tail:
            st.code(result.output_tail, language=None)


# ========================================================= seleção/form
def _selection_panel(ws: Workspace, v: View) -> None:
    ss = st.session_state
    doc = v.doc
    sel = state.get_selection(doc.id)
    if sel is None:
        st.caption(f"💡 Selecione um trecho de {doc.id} (no fonte ou no PDF) para comentar ou marcar.")
        return
    anchor, nonce = sel
    with st.container(border=True):
        head = st.columns([9, 1], vertical_alignment="center")
        where = f"`{anchor.file}:{anchor.lines_label}`" + (f" · pág. {anchor.page}" if anchor.page else "")
        head[0].markdown(f"**Seleção em {doc.id}** · {where} · v{anchor.version}")
        head[1].button("✕", key=f"clr_{nonce}", help="Limpar seleção", on_click=state.clear_selection, args=(doc.id,))
        st.code(short_block(anchor.quote, 700), language=None, wrap_lines=True)
        ok, why = actions.can_annotate(ws, doc, anchor.version)
        if not ok:
            st.warning(why)
            return
        types = [*COMMENT_TYPES, MARK_TYPE]
        kind = st.selectbox("Tipo", types, key=f"ty_{nonce}", format_func=lambda t: ANNOTATION_TYPES[t])
        is_mark = kind == MARK_TYPE
        body = st.text_area("Nota (opcional)" if is_mark else "Instrução para o agente", key=f"bd_{nonce}", height=90,
                            placeholder="Ex.: o ano correto é 2021; ajuste também no suplemento.")
        suggestion = None
        if kind in ("corrigir", "reescrever", "inserir"):
            if st.toggle("Sugerir texto substituto", key=f"sgon_{nonce}"):
                ss.setdefault(f"sg_{nonce}", anchor.quote)
                new_text = st.text_area("Texto substituto", key=f"sg_{nonce}", height=110)
                if new_text != anchor.quote:
                    st.code(suggestion_diff(anchor.quote, new_text), language="diff")
                    suggestion = new_text
        label = "🏷️ Salvar marca" if is_mark else "💬 Salvar comentário"
        if st.button(label, type="primary", key=f"save_{nonce}"):
            try:
                ann = actions.create_annotation(ws, doc.id, anchor, kind, body, suggestion)
            except MargaError as exc:
                st.error(str(exc))
            else:
                state.clear_selection(doc.id)
                ss["focus_ann"] = ann.id
                state.flash("ok", f"{ann.id} criado em {anchor.location}")
                st.rerun()


# ============================================================ anotações
def _set_status(ws: Workspace, ann_id: str, status: str) -> None:
    actions.set_status(ws, ann_id, status)
    state.flash("ok", f"{ann_id}: {STATUSES[status].lower()}")


def page_of_anchor(ws: Workspace, doc: Document, anchor: Anchor) -> int | None:
    if doc.kind == "pdf":
        version = get_version(ws, doc.id, anchor.version)
        return anchor.page or pdfmod.pdf_text(ws, version.files[doc.root]).page_of_line(anchor.start_line)
    if doc.kind != "latex" or not synctex.available():
        return None
    result = latex.load_result(ws, doc.id, anchor.version)
    if result is None or not result.has_pdf:
        return None
    src = Path(result.src_dir) / os.path.relpath(anchor.file, doc.root)
    for line in range(anchor.start_line, anchor.end_line + 1):
        boxes = synctex.forward(result.pdf, src, line, result.src_dir)
        if boxes:
            return boxes[0].page
    return None


def go_to(ws: Workspace, ann_id: str) -> None:
    """Mostra a anotação: escolhe arquivo, versão e página nas colunas que exibem o documento."""
    ss = st.session_state
    ann = ws.get_annotation(ann_id)
    if ann is None:
        return
    doc = ws.get_doc(ann.doc)
    an = ann.anchor
    state.set_focus(ann.doc, an.file, an.start_line, ann_id)
    showing = [side for side in SIDES if ss.get(f"{side}_doc") == ann.doc]
    if not showing:
        state.request("L_doc", ann.doc)
        state.request(f"L_mode_{ann.doc}", "Fonte")
        showing = ["L"]
    page = None
    for side in showing:
        state.request(f"{side}_ver_{ann.doc}", an.version)
        state.request(f"{side}_file_{ann.doc}_{an.version}", an.file)
        if ss.get(f"{side}_mode_{ann.doc}") == "PDF":
            page = page or page_of_anchor(ws, doc, an)
            if page:
                state.request(f"{side}_page_{ann.doc}_{an.version}", page)


def _send_reply(ws: Workspace, ann_id: str, key: str) -> None:
    text = st.session_state.get(key, "")
    if text.strip():
        actions.add_reply(ws, ann_id, text, reopen=True)
        st.session_state[key] = ""
        state.flash("ok", f"{ann_id}: instrução adicionada (comentário aberto)")


def _annotation_list(ws: Workspace, v: View) -> None:
    ss = st.session_state
    doc = v.doc
    anns = ws.annotations(doc.id)
    if not anns:
        return
    key = f"flt_{v.side}_{doc.id}"
    if ss.get(key) not in ("Abertas", "Todas"):
        ss[key] = "Abertas"
    head = st.columns([3, 2], vertical_alignment="center")
    open_count = sum(a.is_open for a in anns)
    head[0].markdown(f"**Anotações de {doc.id}** · {open_count} aberta(s) de {len(anns)}")
    flt = head[1].segmented_control("Filtro", ["Abertas", "Todas"], key=key, label_visibility="collapsed") or "Abertas"
    links = ws.links()
    by_id = {a.id: a for a in ws.annotations()}
    for a in sorted(anns, key=lambda x: (x.anchor.file, x.anchor.start_line, x.id)):
        if flt == "Abertas" and not a.is_open:
            continue
        icon = "🏷️" if a.is_mark else {"aberto": "💬", "resolvido": "✅", "descartado": "🚫"}.get(a.status, "💬")
        title = f"{icon} {a.id} · {a.type_label} · {_doc_rel(doc, a.anchor.file)}:{a.anchor.lines_label}"
        if a.body:
            title += f" — {short(a.body, 46)}"
        with st.expander(title, expanded=a.id == ss.get("focus_ann")):
            _annotation_detail(ws, a, links, by_id, v)


def _annotation_detail(ws: Workspace, a: Annotation, links, by_id: dict[str, Annotation], v: View) -> None:
    an = a.anchor
    meta = [f"`{an.location}`", f"v{an.version}", STATUSES[a.status]]
    if a.region_state:
        meta.append(REGION_STATES.get(a.region_state, a.region_state))
    if a.rounds:
        meta.append("rodada " + ", ".join(map(str, a.rounds)))
    st.caption(" · ".join(meta))
    st.code(short_block(an.quote, 500), language=None, wrap_lines=True)
    if a.body:
        st.markdown(a.body)
    if a.suggestion is not None:
        st.code(suggestion_diff(an.quote, a.suggestion), language="diff")
    for lk in links:
        if a.id not in (lk.source, lk.target):
            continue
        other = by_id.get(lk.target if lk.source == a.id else lk.source)
        if other is None:
            continue
        ref = f"{other.id} ({other.doc} · `{other.anchor.location}`)"
        arrow = (f"este → *{lk.relation_label}* → {ref}" if lk.source == a.id
                 else f"{ref} → *{lk.relation_label}* → este")
        st.markdown(f"↔ **{lk.id}** · {arrow}" + (f" — {lk.note}" if lk.note else ""))
    for r in a.thread:
        extra = (f" · rodada {r.round}" if r.round else "") + (f" · {AGENT_STATUSES[r.agent_status]}" if r.agent_status else "")
        st.markdown(f"**{r.author_label}**{extra}: {r.text}")
        if r.replacement:
            st.code(r.replacement, language=None, wrap_lines=True)

    k = f"{v.side}_{a.id}"
    row = st.columns(4)
    row[0].button("📍 Ir", key=f"go_{k}", on_click=go_to, args=(ws, a.id), help="Mostrar no fonte e no PDF")
    if a.is_open:
        row[1].button("✓ Resolver", key=f"res_{k}", on_click=_set_status, args=(ws, a.id, "resolvido"))
        row[2].button("🚫 Descartar", key=f"dis_{k}", on_click=_set_status, args=(ws, a.id, "descartado"))
    else:
        row[1].button("↺ Reabrir", key=f"reo_{k}", on_click=_set_status, args=(ws, a.id, "aberto"))
    with row[3].popover("✏️"):
        new_type = None
        if not a.is_mark:
            types = list(COMMENT_TYPES)
            new_type = st.selectbox("Tipo", types, index=types.index(a.type) if a.type in types else 0,
                                    format_func=COMMENT_TYPES.get, key=f"ety_{k}")
        new_body = st.text_area("Texto", value=a.body, key=f"ebd_{k}")
        if st.button("Salvar alterações", key=f"esv_{k}"):
            actions.update_annotation(ws, a.id, body=new_body, type_=new_type)
            state.flash("ok", f"{a.id} atualizado")
            st.rerun()
        st.divider()
        if st.button("🗑️ Excluir anotação", key=f"del_{k}"):
            actions.delete_annotation(ws, a.id)
            state.flash("ok", f"{a.id} excluído")
            st.rerun()
    reply_key = f"rp_{k}"
    st.text_input("Nova instrução / resposta", key=reply_key, placeholder="Ex.: falta ajustar também o resumo",
                  label_visibility="collapsed")
    st.button("Enviar instrução", key=f"rps_{k}", on_click=_send_reply, args=(ws, a.id, reply_key))


# ================================================================ links
def _endpoint(ws: Workspace, key: str) -> str:
    if key.startswith("ann:"):
        return key[4:]
    doc_id = key[4:]
    sel = state.get_selection(doc_id)
    if sel is None:
        raise MargaError(f"A seleção em {doc_id} não existe mais.")
    return actions.create_annotation(ws, doc_id, sel[0], MARK_TYPE, "").id


def _link_form(ws: Workspace, views: list[View]) -> None:
    ss = st.session_state
    st.markdown("##### ↔ Linkar trechos (rastreio entre arquivos)")
    options: dict[str, str] = {}
    for v in views:
        sel = state.get_selection(v.doc.id)
        if sel and f"sel:{v.doc.id}" not in options:
            anchor = sel[0]
            options[f"sel:{v.doc.id}"] = (f"✂️ Seleção em {v.doc.id} · {Path(anchor.file).name}:{anchor.lines_label} "
                                          f"“{short(anchor.quote, 36)}”")
    for a in ws.annotations():
        if a.is_open:
            options[f"ann:{a.id}"] = (f"{'🏷️' if a.is_mark else '💬'} {a.id} · {a.doc} · "
                                      f"{Path(a.anchor.file).name}:{a.anchor.lines_label} — {short(a.body or a.anchor.quote, 36)}")
    if len(options) < 2:
        st.caption("Selecione um trecho em cada coluna (ou use anotações existentes) para criar um link de A para B.")
        return
    keys = list(options)
    left, right = f"sel:{views[0].doc.id}", f"sel:{views[1].doc.id}"
    focus = f"ann:{ss.get('focus_ann')}"

    def pick(preferred: list[str], avoid: str | None) -> str:
        return next(k for k in [*preferred, *keys] if k in options and k != avoid)

    if ss.get("lk_src") not in keys or ss.get("lk_src") == ss.get("lk_dst"):
        ss["lk_src"] = pick([left, focus], ss.get("lk_dst"))
    if ss.get("lk_dst") not in keys or ss.get("lk_dst") == ss["lk_src"]:
        ss["lk_dst"] = pick([right], ss["lk_src"])
    row = st.columns([5, 3, 5], vertical_alignment="bottom")
    src = row[0].selectbox("Origem", keys, key="lk_src", format_func=options.get)
    relation = row[1].selectbox("Relação", list(LINK_RELATIONS), key="lk_rel", format_func=LINK_RELATIONS.get)
    dst = row[2].selectbox("Destino", keys, key="lk_dst", format_func=options.get)
    row2 = st.columns([8, 2], vertical_alignment="bottom")
    note = row2[0].text_input("Nota do link (opcional)", key="lk_note",
                              placeholder="Ex.: o número de participantes precisa ser o mesmo nos dois")
    if row2[1].button("↔ Criar link", type="primary", key="lk_go"):
        if src == dst:
            st.error("Origem e destino são o mesmo trecho.")
            return
        try:
            link = actions.create_link(ws, _endpoint(ws, src), _endpoint(ws, dst), relation, note)
        except MargaError as exc:
            st.error(str(exc))
            return
        for k in (src, dst):
            if k.startswith("sel:"):
                state.clear_selection(k[4:])
        state.request("lk_note", "")
        state.flash("ok", f"{link.id}: {link.source} → {link.relation_label} → {link.target}")
        st.rerun()
