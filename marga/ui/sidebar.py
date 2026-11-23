"""Barra lateral: workspace, documentos (com detecção de mudanças no disco) e ajustes."""

from __future__ import annotations

import shutil
from pathlib import Path

import streamlit as st

from marga.core import actions, latex
from marga.core.versions import doc_status, latest_version
from marga.core.workspace import MARGA_DIR, MargaError, Workspace
from marga.ui import state

KIND_ICON = {"latex": "📄", "pdf": "📕", "text": "📝"}


def render(default_path: str) -> Workspace | None:
    ss = st.session_state
    ss.setdefault("ws_path", default_path)
    with st.sidebar:
        st.markdown("## 🖍️ marga")
        st.caption("Comente, ligue trechos entre arquivos, exporte para o agente e compare n × n+1.")
        st.text_input("Workspace (pasta)", key="ws_path")
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
        tools = latex.tools()
        st.caption(" · ".join(f"{'✅' if tools[t] else '❌'} {t}" for t in ("latexmk", "synctex")))
        st.divider()
        _documents(str(ws.root))
        _add_document(ws)
        _settings(ws)
    return ws


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
        st.markdown(f"{KIND_ICON.get(doc.kind, '📄')} **{doc.id}** · {doc.name} · v{n}")
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


def _add_document(ws: Workspace) -> None:
    with st.expander("➕ Adicionar documento"):
        candidates = actions.discover(ws)
        if candidates:
            idx = st.selectbox("Encontrados no workspace", range(len(candidates)),
                               format_func=lambda i: candidates[i].label, key="add_choice")
            name = st.text_input("Nome (opcional)", key="add_name")
            if st.button("Adicionar", type="primary", key="add_go"):
                cand = candidates[idx]
                try:
                    doc = actions.add_document(ws, cand.path, kind=cand.kind, name=name or None, main=cand.main)
                except MargaError as exc:
                    st.error(str(exc))
                else:
                    state.request("add_name", "")
                    state.flash("ok", f"{doc.label} adicionado (v1)")
                    st.rerun()
        else:
            st.caption("Nenhuma pasta LaTeX, PDF ou arquivo de texto novo encontrado.")
        upload = st.file_uploader("…ou envie um arquivo", type=["pdf", "tex", "md", "txt"], key="add_upload")
        if upload is not None and st.button("Adicionar arquivo enviado", key="add_up_go"):
            try:
                rel = actions.save_upload(ws, upload.name, upload.getvalue())
                doc = actions.add_document(ws, rel)
            except MargaError as exc:
                st.error(str(exc))
            else:
                state.flash("ok", f"{doc.label} adicionado a partir de {rel}")
                st.rerun()


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
        if st.button("Salvar ajustes", key=f"cfg_save_{doc_id}"):
            changed_build = (engine, main) != (doc.engine, doc.main)
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
