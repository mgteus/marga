"""Montagem da página: barra lateral, navegação e abas."""

from __future__ import annotations

import streamlit as st

from marga.core.workspace import MargaError
from marga.ui import pages, review, sidebar, state

NAV = ["Revisão", "Links", "Comparar versões", "Rodadas"]

_CSS = """<style>
[data-testid="stMainBlockContainer"], .block-container {padding-top: 4rem; padding-bottom: 3rem;}
</style>"""


def run(default_workspace: str) -> None:
    state.init()
    state.keep_widget_state()
    state.apply_pending()
    st.markdown(_CSS, unsafe_allow_html=True)
    ws = sidebar.render(default_workspace)

    ss = st.session_state
    if ss.get("nav") not in NAV:
        ss["nav"] = NAV[0]
    head = st.columns([2, 8], vertical_alignment="center")
    head[0].markdown("#### 🖍️ marga")
    nav = head[1].segmented_control("Seção", NAV, key="nav", label_visibility="collapsed") or NAV[0]
    state.show_flash()
    if ws is None:
        st.info("Escolha a pasta do workspace na barra lateral.")
        return
    try:
        if nav == "Revisão":
            review.render(ws)
        elif nav == "Links":
            pages.render_links(ws)
        elif nav == "Comparar versões":
            pages.render_compare(ws)
        else:
            pages.render_rounds(ws)
    except MargaError as exc:
        st.error(str(exc))
