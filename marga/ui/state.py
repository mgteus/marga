"""Estado da sessão: seleções por documento, foco, mensagens e mudanças de widgets adiadas."""

from __future__ import annotations

import re
import uuid

import streamlit as st

from marga.core.models import Anchor

SIDES = ("L", "R")


def init() -> None:
    ss = st.session_state
    ss.setdefault("sel", {})  # doc_id -> {"anchor": dict, "side": "L"|"R", "nonce": str}
    ss.setdefault("focus", None)  # {"doc", "file", "line", "nonce"}
    ss.setdefault("focus_ann", None)
    ss.setdefault("flash", [])
    ss.setdefault("seen", set())
    ss.setdefault("_pending", {})


def new_nonce() -> str:
    return uuid.uuid4().hex[:10]


# ---------------------------------------------- widgets alterados por código
def request(key: str, value) -> None:
    """Agenda ``session_state[key] = value`` para o início da próxima execução (antes dos widgets)."""
    st.session_state["_pending"][key] = value


def apply_pending() -> None:
    pending = st.session_state.get("_pending", {})
    for key, value in pending.items():
        st.session_state[key] = value
    pending.clear()


# Widgets cujo valor deve sobreviver à troca de aba (o Streamlit apaga o estado de widgets não desenhados).
_KEEP = re.compile(
    r"^(?:[LR]_doc|[LR]_(?:mode|ver)_[^_]+|[LR]_(?:file|page)_[^_]+_\d+|show_diff|show_threads|flt_[LR]_[^_]+"
    r"|cmp_doc|cmp_(?:from|to)_[^_]+|lk_(?:src|dst|rel|note)|instr|incl_src)$"
)


def keep_widget_state() -> None:
    ss = st.session_state
    for key in list(ss.keys()):
        if isinstance(key, str) and _KEEP.match(key):
            ss[key] = ss[key]


# -------------------------------------------------------------- mensagens
def flash(kind: str, message: str) -> None:
    st.session_state["flash"].append((kind, message))


def show_flash() -> None:
    for kind, message in st.session_state["flash"]:
        st.toast(message, icon={"ok": "✅", "warn": "⚠️", "err": "❌"}.get(kind, "ℹ️"))
    st.session_state["flash"] = []


def fresh(nonce: str | None) -> bool:
    """True só na primeira vez que um evento de componente (identificado pelo nonce) aparece."""
    seen = st.session_state["seen"]
    if not nonce or nonce in seen:
        return False
    seen.add(nonce)
    return True


# --------------------------------------------------------------- seleção
def set_selection(doc_id: str, anchor: Anchor, side: str) -> str:
    nonce = new_nonce()
    st.session_state["sel"][doc_id] = {"anchor": anchor.to_dict(), "side": side, "nonce": nonce}
    if side == "L":
        request("lk_src", f"sel:{doc_id}")
    elif side == "R":
        request("lk_dst", f"sel:{doc_id}")
    return nonce


def get_selection(doc_id: str) -> tuple[Anchor, str] | None:
    item = st.session_state["sel"].get(doc_id)
    if not item:
        return None
    return Anchor.from_dict(item["anchor"]), item["nonce"]


def clear_selection(doc_id: str) -> None:
    st.session_state["sel"].pop(doc_id, None)


# ------------------------------------------------------------------ foco
def set_focus(doc_id: str, file: str, line: int, ann_id: str | None = None) -> None:
    st.session_state["focus"] = {"doc": doc_id, "file": file, "line": int(line), "nonce": new_nonce()}
    if ann_id is not None:
        st.session_state["focus_ann"] = ann_id
