"""Extração segura de .zip (por exemplo, um projeto baixado do Overleaf) para dentro do workspace."""

from __future__ import annotations

import re
import shutil
import stat
import zipfile
from pathlib import Path
from typing import BinaryIO

from .workspace import MargaError

MAX_TOTAL_BYTES = 8 * 1024**3
MAX_FILES = 50_000
_JUNK = {"__MACOSX", ".DS_Store", "Thumbs.db", "desktop.ini"}


def _member_name(info: zipfile.ZipInfo) -> str:
    name = info.filename
    if not info.flag_bits & 0x800:  # sem a flag UTF-8, o zipfile decodifica os nomes como cp437
        try:
            name = name.encode("cp437").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            pass
    return name.replace("\\", "/")


def _is_symlink(info: zipfile.ZipInfo) -> bool:
    return stat.S_ISLNK(info.external_attr >> 16)


def extract_zip(source: str | Path | BinaryIO, dest: Path) -> list[str]:
    """Extrai ``source`` em ``dest`` (que ainda não pode existir) e devolve os caminhos gravados.

    Ignora lixo do macOS/Windows e links simbólicos, recusa caminhos absolutos ou com ``..`` e,
    quando tudo está dentro de uma única pasta, extrai o conteúdo dela (sem a pasta).
    """
    try:
        archive = zipfile.ZipFile(source)
    except (zipfile.BadZipFile, OSError) as exc:
        raise MargaError(f"Arquivo .zip inválido ou corrompido: {exc}") from exc
    with archive:
        members: list[tuple[zipfile.ZipInfo, list[str]]] = []
        total = 0
        for info in archive.infolist():
            if info.is_dir() or _is_symlink(info):
                continue
            name = _member_name(info)
            parts = [p for p in name.split("/") if p not in ("", ".")]
            if not parts or any(p in _JUNK or p.startswith("._") for p in parts):
                continue
            if name.startswith("/") or ".." in parts or re.match(r"^[A-Za-z]:$", parts[0]):
                raise MargaError(f"O .zip tem um caminho inseguro: {name}")
            members.append((info, parts))
            total += info.file_size
        if not members:
            raise MargaError("O .zip não tem arquivos.")
        if len(members) > MAX_FILES or total > MAX_TOTAL_BYTES:
            raise MargaError(f"O .zip é grande demais ({len(members)} arquivos, {total / 1024**3:.1f} GB descompactado).")
        strip = len({parts[0] for _, parts in members}) == 1 and all(len(parts) > 1 for _, parts in members)

        dest.mkdir(parents=True)
        root = dest.resolve()
        written: list[str] = []
        try:
            for info, parts in members:
                rel = parts[1:] if strip else parts
                target = dest.joinpath(*rel)
                if not target.resolve().is_relative_to(root):
                    raise MargaError(f"O .zip tem um caminho inseguro: {'/'.join(parts)}")
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info) as src, open(target, "wb") as out:
                    shutil.copyfileobj(src, out, 1 << 20)
                written.append("/".join(rel))
        except BaseException:
            shutil.rmtree(dest, ignore_errors=True)
            raise
    return written
