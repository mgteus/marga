"""Pacotes do Office (.docx, .pptx): um .zip com partes XML ligadas por relações.

Usado pela extração de texto (``formats.py``) e pelo leitor de slides (``slides.py``). Só a biblioteca padrão.
Os arquivos são entrada não confiável: cada parte é lida com limite de tamanho descompactada, e qualquer falha
vira ``MargaError`` com mensagem legível.
"""

from __future__ import annotations

import io
import posixpath
import zipfile
from xml.etree import ElementTree as ET

from .workspace import MargaError

MAX_PART_BYTES = 64_000_000  # XML descompactado de uma parte
MAX_MEDIA_BYTES = 40_000_000  # imagem dentro do pacote

A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
P = "{http://schemas.openxmlformats.org/presentationml/2006/main}"
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
MC = "{http://schemas.openxmlformats.org/markup-compatibility/2006}"
R_ID = R + "id"
_PKG_REL = "{http://schemas.openxmlformats.org/package/2006/relationships}Relationship"


def local(tag: str) -> str:
    """Nome do elemento sem o namespace: ``{…}solidFill`` → ``solidFill``."""
    return tag.rsplit("}", 1)[-1]


def open_package(data: bytes, label: str) -> zipfile.ZipFile:
    try:
        return zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise MargaError(f"não consegui abrir o arquivo como {label}: ele está corrompido, protegido por senha "
                         "ou no formato antigo (.doc, .ppt). Salve-o de novo como .docx ou .pptx.") from exc


def read_bytes(zf: zipfile.ZipFile, name: str, limit: int) -> bytes | None:
    """Conteúdo de uma parte, ou ``None`` se ela não existe ou passa de ``limit`` bytes descompactada."""
    try:
        info = zf.getinfo(name)
    except KeyError:
        return None
    if info.file_size > limit:
        return None
    try:
        return zf.read(info)
    except Exception:  # noqa: BLE001 - parte corrompida: trata como ausente
        return None


def read_xml(zf: zipfile.ZipFile, name: str, required: bool = True) -> ET.Element | None:
    try:
        info = zf.getinfo(name)
    except KeyError:
        if required:
            raise MargaError(f"falta a parte {name}: o arquivo não parece um documento do Office.") from None
        return None
    if info.file_size > MAX_PART_BYTES:
        raise MargaError(f"a parte {name} tem {info.file_size // 1_000_000} MB descompactada, grande demais para ler.")
    try:
        return ET.fromstring(zf.read(info))
    except Exception as exc:  # noqa: BLE001 - qualquer falha de um arquivo não confiável vira erro legível
        raise MargaError(f"não consegui ler {name}: {exc}") from exc


def rels(zf: zipfile.ZipFile, part: str) -> dict[str, tuple[str, str]]:
    """Relações de uma parte do pacote: id → (tipo, caminho da parte de destino dentro do .zip)."""
    folder, name = posixpath.split(part)
    root = read_xml(zf, posixpath.join(folder, "_rels", f"{name}.rels"), required=False)
    out: dict[str, tuple[str, str]] = {}
    for rel in [] if root is None else root.iter(_PKG_REL):
        target = rel.get("Target") or ""
        if not target or rel.get("TargetMode") == "External":
            continue
        path = target.lstrip("/") if target.startswith("/") else posixpath.normpath(posixpath.join(folder, target))
        out[rel.get("Id", "")] = (rel.get("Type", ""), path)
    return out


def related(part_rels: dict[str, tuple[str, str]], rel_type: str) -> str | None:
    """Caminho da primeira parte com a relação ``…/rel_type`` (ex.: ``styles``, ``slideLayout``, ``theme``)."""
    return next((path for kind, path in part_rels.values() if kind.endswith("/" + rel_type)), None)


def main_part(zf: zipfile.ZipFile, default: str, root_tag: str, label: str) -> tuple[str, ET.Element]:
    """A parte principal do pacote (``word/document.xml``, ``ppt/presentation.xml``…), conferindo o tipo dela."""
    path = related(rels(zf, ""), "officeDocument") or default
    root = read_xml(zf, path)
    if root.tag != root_tag:
        raise MargaError(f"o arquivo não é um {label}: a parte principal é {path}.")
    return path, root
