"""Abas Links, Comparar versões e Rodadas."""

from __future__ import annotations

import streamlit as st

from marga.core import actions, export
from marga.core.diffing import changes
from marga.core.models import AGENT_STATUSES, STATUSES, Annotation, Document
from marga.core.versions import get_version, load_versions, version_texts
from marga.core.workspace import MargaError, Workspace
from marga.ui import state
from marga.ui.render import anchored_lines, diff_html, links_dot, short
from marga.ui.review import go_to


# ================================================================= links
def _endpoint_md(a: Annotation | None) -> str:
    if a is None:
        return "_(anotação removida)_"
    icon = "🏷️" if a.is_mark else "💬"
    return f"{icon} **{a.id}** · {a.doc} · `{a.anchor.location}`  \n“{short(a.anchor.quote, 70)}”"


def _remove_link(ws: Workspace, link_id: str) -> None:
    actions.delete_link(ws, link_id)
    state.flash("ok", f"{link_id} removido")


def render_links(ws: Workspace) -> None:
    links = ws.links()
    anns = {a.id: a for a in ws.annotations()}
    docs = {d.id: d for d in ws.docs()}
    if not links:
        st.info("Nenhum link ainda. Na aba Revisão, selecione um trecho em cada coluna e use ↔ Criar link.")
        return
    st.graphviz_chart(links_dot(links, anns, docs))
    for lk in links:
        with st.container(border=True):
            row = st.columns([1, 5, 3, 5, 1], vertical_alignment="center")
            row[0].markdown(f"**{lk.id}**")
            row[1].markdown(_endpoint_md(anns.get(lk.source)))
            row[2].markdown(f"→ *{lk.relation_label}* →" + (f"  \n{lk.note}" if lk.note else ""))
            row[3].markdown(_endpoint_md(anns.get(lk.target)))
            row[4].button("🗑️", key=f"rmlk_{lk.id}", help="Remover link", on_click=_remove_link, args=(ws, lk.id))


# ======================================================= comparar versões
def _open_side_by_side(doc: Document, a: int, b: int) -> None:
    mode = "Fonte" if doc.kind == "text" else "PDF"
    for side, n in (("L", a), ("R", b)):
        state.request(f"{side}_doc", doc.id)
        state.request(f"{side}_mode_{doc.id}", mode)
        state.request(f"{side}_ver_{doc.id}", n)
    state.request("nav", "Revisão")


def _resolve(ws: Workspace, ann_id: str, status: str) -> None:
    actions.set_status(ws, ann_id, status)
    state.flash("ok", f"{ann_id}: {STATUSES[status].lower()}")


def _reopen_with(ws: Workspace, ann_id: str, key: str) -> None:
    text = st.session_state.get(key, "")
    if text.strip():
        actions.add_reply(ws, ann_id, text, reopen=True)
        st.session_state[key] = ""
        state.flash("ok", f"{ann_id} segue aberto com a nova instrução")


def _go(ws: Workspace, ann_id: str) -> None:
    go_to(ws, ann_id)
    state.request("nav", "Revisão")


def _between_state(ann: Annotation, a: int, b: int) -> str:
    before, after = ann.anchor_at(a), ann.anchor_at(b)
    if after is None:
        return "órfão" if ann.region_state == "orfao" else "sem âncora na versão nova"
    return "inalterado" if before.quote == after.quote and before.file == after.file else "alterado"


def _resolve_done(ws: Workspace, ids: list[str]) -> None:
    for ann_id in ids:
        actions.set_status(ws, ann_id, "resolvido")
    state.flash("ok", f"{len(ids)} comentário(s) resolvido(s)")


def render_compare(ws: Workspace) -> None:
    ss = st.session_state
    docs = ws.docs()
    if not docs:
        st.info("Adicione um documento primeiro.")
        return
    ids = [d.id for d in docs]
    if ss.get("cmp_doc") not in ids:
        ss["cmp_doc"] = ids[0]
    row = st.columns([4, 2, 2, 3], vertical_alignment="bottom")
    doc_id = row[0].selectbox("Documento", ids, key="cmp_doc", format_func=lambda i: ws.get_doc(i).label)
    doc = ws.get_doc(doc_id)
    ns = [v.n for v in load_versions(ws, doc_id)]
    if len(ns) < 2:
        st.info(f"{doc.label} só tem a v{ns[-1]}. Exporte uma rodada, deixe o agente editar os arquivos e "
                "registre a nova versão na barra lateral.")
        return
    key_from, key_to = f"cmp_from_{doc_id}", f"cmp_to_{doc_id}"
    if ss.get(key_to) not in ns:
        ss[key_to] = ns[-1]
    if ss.get(key_from) not in ns:
        ss[key_from] = ns[-2]
    a = row[1].selectbox("De", ns, key=key_from, format_func=lambda n: f"v{n}")
    b = row[2].selectbox("Para", ns, key=key_to, format_func=lambda n: f"v{n}")
    row[3].button("🖼️ Abrir lado a lado na Revisão", on_click=_open_side_by_side, args=(doc, a, b),
                  help="Coluna esquerda com a versão 'De' e direita com a 'Para'")
    if a == b:
        st.warning("Escolha duas versões diferentes.")
        return
    va, vb = get_version(ws, doc_id, a), get_version(ws, doc_id, b)
    old, new = version_texts(ws, doc, va), version_texts(ws, doc, vb)
    file_changes = changes(old, new)
    tracked = [x for x in ws.annotations(doc_id) if x.anchor_at(a) is not None]
    between = {x.id: _between_state(x, a, b) for x in tracked}

    origin = f"v{b} · origem: {vb.origin}" + (f" (resposta da rodada {vb.round})" if vb.round else "")
    st.caption(origin)
    m = st.columns(5)
    m[0].metric("Arquivos alterados", len(file_changes))
    m[1].metric("Linhas +", sum(c.added for c in file_changes))
    m[2].metric("Linhas −", sum(c.removed for c in file_changes))
    m[3].metric("Trechos alterados", sum(1 for s in between.values() if s == "alterado"))
    m[4].metric("Trechos intocados", sum(1 for s in between.values() if s == "inalterado"))
    if vb.round:
        try:
            info = export.get_round(ws, vb.round)
        except MargaError:
            info = None
        if info and info.summary:
            st.info(f"Resumo do agente (rodada {info.n}): {info.summary}")

    st.markdown("#### Comentários: antes × depois")
    if not tracked:
        st.caption(f"Nenhuma anotação ancorada na v{a}.")
    done = [x.id for x in tracked if x.is_open and between[x.id] == "alterado"
            and (r := x.last_agent_reply()) and r.agent_status == "feito"]
    if done:
        st.button(f"✓ Resolver os {len(done)} comentário(s) feitos pelo agente e com trecho alterado",
                  on_click=_resolve_done, args=(ws, done))
    for ann in sorted(tracked, key=lambda x: (x.anchor_at(a).file, x.anchor_at(a).start_line)):
        _compare_card(ws, ann, a, b, old, new, between[ann.id])

    st.markdown("#### Diff por arquivo")
    if not file_changes:
        st.caption("Nenhum arquivo mudou entre as duas versões.")
    for change in file_changes:
        title = f"{change.path} · {change.status} · +{change.added} −{change.removed}"
        with st.expander(title, expanded=len(file_changes) <= 2):
            st.html(diff_html(old.get(change.path), new.get(change.path) or ""))


def _compare_card(ws: Workspace, ann: Annotation, a: int, b: int, old: dict, new: dict, between: str) -> None:
    before, after = ann.anchor_at(a), ann.anchor_at(b)
    icon = "🏷️" if ann.is_mark else {"aberto": "💬", "resolvido": "✅", "descartado": "🚫"}.get(ann.status, "💬")
    badge = {"alterado": "🟢 alterado", "inalterado": "⚪ inalterado"}.get(between, f"🟠 {between}")
    with st.container(border=True):
        st.markdown(f"{icon} **{ann.id}** · {ann.type_label} · `{before.location}` · {badge} · {STATUSES[ann.status]}")
        if ann.body:
            st.markdown(f"**Pedido:** {ann.body}")
        cols = st.columns(2)
        cols[0].caption(f"v{a}")
        cols[0].code(anchored_lines(old.get(before.file), before.start_line, before.end_line), language=None,
                     wrap_lines=True)
        cols[1].caption(f"v{b}" + (f" · `{after.location}`" if after else ""))
        if after:
            cols[1].code(anchored_lines(new.get(after.file), after.start_line, after.end_line), language=None,
                         wrap_lines=True)
        else:
            cols[1].caption("Trecho não encontrado nesta versão.")
        for r in ann.thread:
            if r.author == "agente":
                status = f" · {AGENT_STATUSES[r.agent_status]}" if r.agent_status else ""
                st.markdown(f"🤖 **agente** (rodada {r.round}{status}): {r.text}")
                if r.replacement:
                    st.code(r.replacement, language=None, wrap_lines=True)
        k = f"cmp_{ann.id}_{a}_{b}"
        row = st.columns([1.4, 1.4, 1.2, 4, 1.6], vertical_alignment="bottom")
        if ann.is_open:
            row[0].button("✓ Resolver", key=f"{k}_ok", on_click=_resolve, args=(ws, ann.id, "resolvido"))
            row[1].button("🚫 Descartar", key=f"{k}_no", on_click=_resolve, args=(ws, ann.id, "descartado"))
        else:
            row[0].button("↺ Reabrir", key=f"{k}_re", on_click=_resolve, args=(ws, ann.id, "aberto"))
        row[2].button("📍 Ir", key=f"{k}_go", on_click=_go, args=(ws, ann.id))
        row[3].text_input("Nova instrução", key=f"{k}_txt", label_visibility="collapsed",
                          placeholder="Manter aberto com nova instrução…")
        row[4].button("Enviar", key=f"{k}_send", on_click=_reopen_with, args=(ws, ann.id, f"{k}_txt"))


# ================================================================ rodadas
def _import(ws: Workspace, n: int) -> None:
    try:
        count = export.import_response(ws, n)
    except MargaError as exc:
        state.flash("err", str(exc))
    else:
        state.flash("ok", f"Rodada {n}: {count} nota(s) do agente importada(s)")


def render_rounds(ws: Workspace) -> None:
    ss = st.session_state
    st.markdown("#### Exportar rodada para o agente")
    problems = export.check_ready(ws)
    for problem in problems:
        st.warning(problem, icon="⚠️")
    if "instr" not in ss:
        ss["instr"] = ws.get_setting("instructions", "")
    instructions = st.text_area("Instruções gerais (valem para a rodada inteira)", key="instr", height=100,
                                placeholder="Ex.: mantenha o tom formal e não altere a bibliografia.")
    if instructions != ws.get_setting("instructions", ""):
        ws.set_setting("instructions", instructions)
    include = st.checkbox("Incluir o conteúdo completo dos arquivos no pacote (para LLMs sem acesso ao disco)",
                          key="incl_src")
    anns = ws.annotations()
    n_comments = sum(1 for a in anns if a.is_open and not a.is_mark)
    n_marks = sum(1 for a in anns if a.is_open and a.is_mark)
    st.caption(f"Entram no pacote: {n_comments} comentário(s) aberto(s), {n_marks} marca(s) e {len(ws.links())} link(s).")
    next_n = ws.peek_counter("round") + 1
    if st.button(f"📦 Exportar rodada {next_n}", type="primary", disabled=bool(problems)):
        try:
            info = export.export_round(ws, instructions, include)
        except MargaError as exc:
            st.error(str(exc))
        else:
            state.flash("ok", f"Rodada {info.n} exportada em .marga/rounds/{info.dirname}/")
            st.rerun()

    rounds = export.load_rounds(ws)
    if not rounds:
        st.caption("Nenhuma rodada exportada ainda.")
        return
    st.markdown("#### Rodadas")
    for info in reversed(rounds):
        _round_card(ws, info, latest=info.n == rounds[-1].n)


def _round_card(ws: Workspace, info, latest: bool) -> None:
    rdir = export.round_dir(ws, info.n)
    response = export.response_path(ws, info.n)
    if info.response_imported:
        status = "✅ resposta importada"
    elif response.exists():
        status = "📥 response.json disponível"
    else:
        status = "⏳ aguardando o agente"
    with st.container(border=True):
        docs = ", ".join(f"{d} v{n}" for d, n in info.docs.items())
        when = info.created_at[:16].replace("T", " ")
        st.markdown(f"**Rodada {info.n}** · {when} · {docs} · {len(info.annotations)} anotação(ões) · {status}")
        if info.summary:
            st.info(f"Resumo do agente: {info.summary}")
        if latest:
            st.caption("Prompt para o agente (por exemplo, o Claude Code aberto nesta pasta):")
            st.code(export.agent_prompt(ws, info), language=None, wrap_lines=True)
        row = st.columns(4)
        for i, (name, mime) in enumerate((("bundle.md", "text/markdown"), ("bundle.json", "application/json"),
                                          ("bundle.html", "text/html"))):
            path = rdir / name
            if path.exists():
                row[i].download_button(name, path.read_bytes(), file_name=f"marga-rodada-{info.dirname}-{name}",
                                       mime=mime, key=f"dl_{info.n}_{name}")
        if response.exists() and not info.response_imported:
            row[3].button("📥 Importar resposta", key=f"imp_{info.n}", on_click=_import, args=(ws, info.n))
        st.caption(f"`{rdir}`")
        if st.toggle("Ver o pacote em Markdown", key=f"show_{info.n}"):
            with st.container(height=520):
                st.markdown((rdir / "bundle.md").read_text(encoding="utf-8"))
