"""Componentes de fonte e de PDF (Streamlit components v2, JS puro, sem build)."""

from __future__ import annotations

from pathlib import Path

from streamlit.components.v2 import component

_FRONTEND = Path(__file__).with_name("frontend")


def _asset(name: str) -> str:
    return (_FRONTEND / name).read_text(encoding="utf-8")


_SOURCE = component(
    "marga_source_view", css=_asset("source_view.css"), js=_asset("source_view.js"), isolate_styles=False
)
_PDF = component("marga_pdf_view", css=_asset("pdf_view.css"), js=_asset("pdf_view.js"), isolate_styles=False)
_SLIDES = component("marga_slide_view", css=_asset("slide_view.css"), js=_asset("slide_view.js"),
                    isolate_styles=False)


def _noop() -> None:
    pass


def slide_view(data: dict, key: str):
    return _SLIDES(key=key, data=data, on_select_change=_noop, on_focus_change=_noop)


def source_view(data: dict, key: str):
    return _SOURCE(key=key, data=data, on_select_change=_noop, on_focus_change=_noop)


def pdf_view(data: dict, key: str):
    return _PDF(key=key, data=data, on_click_change=_noop, on_rect_change=_noop, on_focus_change=_noop)


def event(result, name: str) -> dict | None:
    """Valor de um trigger do componente (ou None)."""
    value = None
    try:
        value = result.get(name)
    except AttributeError:
        value = getattr(result, name, None)
    return value if isinstance(value, dict) else None
