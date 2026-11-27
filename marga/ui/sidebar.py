"""Barra lateral: workspace, documentos (com detecção de mudanças no disco) e ajustes."""

from __future__ import annotations

import shutil
from pathlib import Path

import streamlit as st

from marga.core import actions, folders, latex, office
from marga.core.pdf import page_count
from marga.core.versions import blob_path, doc_status, latest_version
from marga.core.workspace import MARGA_DIR, MargaError, Workspace
from marga.ui import state
from marga.ui.render import KIND_ICON, short

ADD_FROM = {"enviar": "Enviar", "workspace": "Pasta", "disco": "Disco"}
UPLOAD_TYPES = ["zip", "pdf", "tex", "docx", "pptx", "html", "htm", "md", "txt"]


def render(default_path: str) -> Workspace | None:
    ss = st.session_state
    ss.setdefault("ws_path", default_path)
    with st.sidebar:
        st.markdown("## 🖍️ marga")
        st.caption("Comente, ligue trechos entre arquivos, exporte para o agente e compare n × n+1.")
        st.text_input("Workspace (pasta)", key="ws_path")
        _workspace_shortcuts()
        root = Path(ss["ws_path"]).expanduser()
        if not root.is_dir():
            st.error(f"Pasta não encontrada: {root}")
            return None
        is_default = root.resolve() == Path(default_path).expanduser().resolve()
        if not (root / MARGA_DIR).exists() and not is_default:
            st.info("Esta pasta ainda não é um workspace do Marga.")
            if not st.button("Criar workspace aqui", type="primary", key="ws_create"):
                return None
        try:
            ws = Workspace.open(root)
        except MargaError as exc:
            st.error(str(exc))
            return None
        folders.remember_workspace(ws.root)
        tools = latex.tools()
        tools["LibreOffice"] = office.available()
        names = ["latexmk", "synctex", "LibreOffice"]
        if office.powerpoint():  # no Mac, o PowerPoint exporta o PDF fiel dos slides
            tools["PowerPoint"] = True
            names.append("PowerPoint")
        st.caption(" · ".join(f"{'✅' if tools[t] else '❌'} {t}" for t in names),
                   help="latexmk e synctex compilam o LaTeX; o PowerPoint (no Mac) ou o LibreOffice fazem o PDF "
                        "dos slides (modo PDF).")
        st.divider()
        _documents(str(ws.root))
        _add_document(ws)
        _settings(ws)
    return ws


def _workspace_shortcuts() -> None:
    """Botão que abre o Finder e lista dos workspaces recentes, para não precisar digitar o caminho."""
    ss = st.session_state
    current = Path(ss["ws_path"]).expanduser().resolve()
    recent = [str(p) for p in folders.recent_workspaces() if p != current]
    pick = folders.can_pick()
    if not (pick or recent):
        return
    cols = st.columns([1, 1.6]) if pick and recent else [st.container()]
    if pick:
        cols[0].button("📁 Finder" if recent else "📁 Escolher pasta…", key="ws_pick", on_click=_pick_workspace,
                       width="stretch", help="Abre o seletor de pastas do sistema para escolher o workspace.")
    if recent:
        home = str(Path.home())
        cols[-1].selectbox("Recentes", recent, index=None, key="ws_recent", placeholder="🕘 Recentes",
                           label_visibility="collapsed", on_change=_use_recent,
                           format_func=lambda p: f"{Path(p).name} · {short(str(Path(p).parent).replace(home, '~'), 28)}")


def _pick_workspace() -> None:
    ss = st.session_state
    current = Path(ss.get("ws_path") or ".").expanduser()
    try:
        chosen = folders.pick_folder(current.parent if current.is_dir() else None)
    except MargaError as exc:
        state.flash("err", str(exc))
        return
    if chosen:
        ss["ws_path"] = str(chosen).rstrip("/") or "/"


def _use_recent() -> None:
    ss = st.session_state
    if ss.get("ws_recent"):
        ss["ws_path"] = ss["ws_recent"]
    ss["ws_recent"] = None


@st.fragment(run_every=4)
def _documents(root: str) -> None:
    """Atualiza sozinho a cada 4 s para mostrar quando o agente (ou você) mudou arquivos no disco."""
    ws = Workspace(root)
    docs = ws.docs()
    st.markdown("**Documentos**")
    if not docs:
        st.caption("Nenhum documento ainda.")
        return
    for doc in docs:
        status = doc_status(ws, doc)
        n = status.latest.n if status.latest else 0
        extra = ""
        if doc.kind == "pdf" and status.latest and doc.root in status.latest.files:
            extra = f" · {_pdf_pages(root, status.latest.files[doc.root])} p."
        st.markdown(f"{KIND_ICON.get(doc.kind, '📄')} **{doc.id}** · {doc.name} · v{n}{extra}")
        if status.dirty:
            st.caption(f"⚠️ {len(status.changed)} arquivo(s) mudaram no disco desde a v{n}")
            if st.button(f"Registrar v{n + 1}", key=f"reg_{doc.id}", type="primary"):
                try:
                    report = actions.register_version(ws, doc.id)
                except MargaError as exc:
                    st.error(str(exc))
                else:
                    state.flash("ok", report.summary())
                    st.rerun(scope="app")


@st.cache_data(max_entries=256, show_spinner=False)
def _pdf_pages(root: str, sha: str) -> int:
    return page_count(blob_path(Workspace(root), sha))


def _size(nbytes: int) -> str:
    return f"{nbytes / 1024**2:.1f} MB".replace(".", ",") if nbytes >= 1024**2 else f"{max(1, nbytes // 1024)} KB"


def _added(doc, clear: list[str] | None = None) -> None:
    """Depois de adicionar: limpa os campos, mostra o documento na coluna da esquerda e avisa."""
    for key in clear or []:
        state.request(key, "")
    state.request("L_doc", doc.id)
    state.flash("ok", f"{doc.label} adicionado · {doc.kind_label} · v1")
    st.rerun()


def _add_document(ws: Workspace) -> None:
    with st.expander("➕ Adicionar documento"):
        source = st.segmented_control("Origem", list(ADD_FROM), key="add_from", default="enviar",
                                      format_func=ADD_FROM.get, label_visibility="collapsed") or "enviar"
        if source == "enviar":
            _add_upload(ws)
        elif source == "workspace":
            _add_found(ws)
        else:
            _add_from_disk(ws)


def _zip_target(ws: Workspace, filename: str, key: str) -> str | None:
    """Para um .zip: documento novo (None) ou a próxima versão de um documento enviado antes."""
    targets = actions.zip_targets(ws) if filename.lower().endswith(".zip") else []
    if not targets:
        return None
    labels = {"": "Documento novo", **{d.id: f"Nova versão de {d.label}" for d in targets}}
    return st.selectbox("Adicionar como", list(labels), key=key, format_func=labels.get) or None


def _update_from_zip(ws: Workspace, doc_id: str, source, filename: str, size: int, key: str) -> bool:
    """Botão que registra o .zip como a próxima versão; devolve True se registrou."""
    doc = ws.get_doc(doc_id)
    latest = latest_version(ws, doc_id)
    nxt = latest.n + 1 if latest else 1
    st.caption(f"Os arquivos do .zip substituem a cópia em {doc.root}/. Textos que não vierem nele saem da cópia; "
               f"a v{nxt - 1} continua guardada no Marga.")
    if not st.button(f"Registrar como v{nxt} de {doc.id}" + (f" · {_size(size)}" if size else ""), type="primary",
                     key=key, width="stretch"):
        return False
    with st.spinner(f"Registrando {filename} como v{nxt} de {doc.label}…"):
        try:
            update = actions.update_from_zip(ws, doc_id, source, filename)
        except MargaError as exc:
            st.error(str(exc))
            return False
    state.request("L_doc", doc_id)
    state.flash("ok", update.summary())
    return True


def _add_upload(ws: Workspace) -> None:
    ss = st.session_state
    n = ss.setdefault("add_up_n", 0)  # trocar a chave limpa o campo de envio depois de adicionar
    upload = st.file_uploader("Arquivo", type=UPLOAD_TYPES, key=f"add_upload_{n}", label_visibility="collapsed")
    if upload is None:
        st.caption("📦 Um .zip baixado do Overleaf vira um documento LaTeX com todos os .tex, a bibliografia "
                   "e as figuras, ou a próxima versão de um documento enviado antes. Também aceita .pdf, .tex, "
                   "Word (.docx), PowerPoint (.pptx), .html, .md e .txt.")
        return
    target = _zip_target(ws, upload.name, f"add_up_as_{n}")
    if target:
        if _update_from_zip(ws, target, upload, upload.name, upload.size, f"add_up_ver_{n}"):
            ss["add_up_n"] = n + 1
            st.rerun()
        return
    name = st.text_input("Nome no Marga (opcional)", key=f"add_up_name_{n}", placeholder=upload.name)
    if st.button(f"Adicionar {short(upload.name, 34)} · {_size(upload.size)}", type="primary",
                 key=f"add_up_go_{n}", width="stretch"):
        with st.spinner(f"Importando {upload.name}…"):
            try:
                doc = actions.import_upload(ws, upload.name, upload, name=name.strip() or None)
            except MargaError as exc:
                st.error(str(exc))
                return
        ss["add_up_n"] = n + 1
        _added(doc)


def _add_found(ws: Workspace) -> None:
    candidates = actions.discover(ws)
    if not candidates:
        st.caption("Nenhuma pasta LaTeX, PDF, Word, PowerPoint, HTML ou arquivo de texto novo nesta pasta.")
        return
    idx = st.selectbox("Encontrados no workspace", range(len(candidates)), key="add_choice",
                       format_func=lambda i: candidates[i].label)
    cand = candidates[min(idx or 0, len(candidates) - 1)]
    name = st.text_input("Nome no Marga (opcional)", key="add_ws_name", placeholder=Path(cand.path).name)
    if st.button(f"Adicionar {short(cand.path, 40)}", type="primary", key="add_ws_go", width="stretch"):
        try:
            doc = actions.add_document(ws, cand.path, kind=cand.kind, name=name.strip() or None, main=cand.main)
        except MargaError as exc:
            st.error(str(exc))
            return
        _added(doc, clear=["add_ws_name"])


def _add_from_disk(ws: Workspace) -> None:
    raw = st.text_input("Caminho do arquivo", key="add_disk_path", placeholder="~/Downloads/projeto-overleaf.zip")
    path = actions.parse_path(ws, raw) if raw.strip() else None
    target = _zip_target(ws, path.name, "add_disk_as") if path else None
    if target:
        if not path.is_file():
            st.caption(f"Não encontrei {path}.")
        elif _update_from_zip(ws, target, path, path.name, path.stat().st_size, "add_disk_ver"):
            state.request("add_disk_path", "")
            st.rerun()
        return
    name = st.text_input("Nome no Marga (opcional)", key="add_disk_name")
    st.caption("Para arquivos grandes: o Marga lê direto do disco. Um .zip é extraído em uploads/; "
               ".pdf, .tex, .docx, .pptx, .html, .md e .txt de fora do workspace são copiados para lá.")
    if st.button("Importar", type="primary", key="add_disk_go", disabled=not raw.strip(), width="stretch"):
        with st.spinner("Importando…"):
            try:
                doc = actions.import_path(ws, raw, name=name.strip() or None)
            except MargaError as exc:
                st.error(str(exc))
                return
        _added(doc, clear=["add_disk_path", "add_disk_name"])


def _settings(ws: Workspace) -> None:
    docs = ws.docs()
    if not docs:
        return
    with st.expander("⚙️ Ajustes de documento"):
        ids = [d.id for d in docs]
        doc_id = st.selectbox("Documento", ids, key="cfg_doc", format_func=lambda i: ws.get_doc(i).label)
        doc = ws.get_doc(doc_id)
        name = st.text_input("Nome", value=doc.name, key=f"cfg_name_{doc_id}")
        engine, main = doc.engine, doc.main
        if doc.kind == "latex":
            engines = list(latex.ENGINES)
            engine = st.selectbox("Motor", engines, index=engines.index(doc.engine) if doc.engine in engines else 0,
                                  key=f"cfg_eng_{doc_id}")
            latest = latest_version(ws, doc_id)
            mains = sorted(p for p in latest.files if p.endswith(".tex")) if latest else [doc.main]
            main = st.selectbox("Arquivo principal", mains, index=mains.index(doc.main) if doc.main in mains else 0,
                                key=f"cfg_main_{doc_id}")
        elif doc.kind == "pptx":
            options = {"auto": "Automático (PowerPoint, se instalado)", **office.ENGINES}
            current = doc.engine if doc.engine in office.ENGINES else "auto"
            engine = st.selectbox("PDF dos slides feito com", list(options), index=list(options).index(current),
                                  format_func=options.get, key=f"cfg_conv_{doc_id}",
                                  help="O PowerPoint (no Mac) mostra os slides exatamente como ele; o LibreOffice "
                                       "funciona também no Linux, mas troca fontes e efeitos que não tem.")
        if st.button("Salvar ajustes", key=f"cfg_save_{doc_id}"):
            changed_build = doc.kind == "latex" and (engine, main) != (doc.engine, doc.main)  # o PDF de cada conversor fica
            doc.name, doc.engine, doc.main = name.strip() or doc.name, engine, main
            ws.save_doc(doc)
            if changed_build:
                shutil.rmtree(ws.dir / "build" / doc_id, ignore_errors=True)
            state.flash("ok", f"{doc.label} atualizado")
            st.rerun()
        st.divider()
        sure = st.checkbox(f"Remover {doc.id} e as anotações dele", key=f"cfg_rm_ok_{doc_id}")
        if st.button("Remover documento", key=f"cfg_rm_{doc_id}", disabled=not sure):
            actions.remove_document(ws, doc_id)
            state.flash("ok", f"{doc.label} removido (seus arquivos continuam no disco)")
            st.rerun()
