"""Entrada do Streamlit: ``streamlit run marga/app.py -- --workspace <pasta>`` (ou ``marga <pasta>``)."""

import os
import sys

import streamlit as st

st.set_page_config(page_title="marga", page_icon="🖍️", layout="wide")

from marga.ui.main import run  # noqa: E402  (set_page_config precisa vir antes)


def _workspace_from_args() -> str:
    args = sys.argv[1:]
    if "--workspace" in args:
        i = args.index("--workspace")
        if i + 1 < len(args):
            return args[i + 1]
    return os.environ.get("MARGA_WORKSPACE") or os.getcwd()


run(_workspace_from_args())
