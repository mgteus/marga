"""Versões: blobs endereçados por sha256, snapshots dos arquivos de texto e status do disco."""

from __future__ import annotations

import hashlib
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path

from .models import Document, Version, now_iso
from .workspace import MargaError, Workspace, read_json, write_bytes, write_json

TEXT_EXTS = {".tex", ".bib", ".sty", ".cls", ".bst", ".md", ".markdown", ".txt", ".rst"}
MAX_FILE_BYTES = 5_000_000
SKIP_DIRS = {".marga", ".git", ".hg", ".svn", "__pycache__", "node_modules", ".venv", "venv"}

_LOCK = threading.RLock()
_SHA_CACHE: dict[tuple[str, int, int], str] = {}


# ------------------------------------------------------------------ blobs
def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def blob_path(ws: Workspace, sha: str) -> Path:
    return ws.dir / "objects" / sha[:2] / sha


def store_blob(ws: Workspace, data: bytes) -> str:
    sha = sha256_bytes(data)
    path = blob_path(ws, sha)
    if not path.exists():
        write_bytes(path, data)
    return sha


def read_blob(ws: Workspace, sha: str) -> bytes:
    path = blob_path(ws, sha)
    if not path.exists():
        raise MargaError(f"Conteúdo {sha[:12]} não encontrado em {ws.dir / 'objects'}.")
    return path.read_bytes()


def decode_text(data: bytes) -> tuple[str, str]:
    """Decodifica UTF-8 (com fallback latin-1) e normaliza quebras de linha para ``\\n``."""
    try:
        text, encoding = data.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        text, encoding = data.decode("latin-1"), "latin-1"
    if text.startswith("﻿"):
        text = text[1:]
    return text.replace("\r\n", "\n").replace("\r", "\n"), encoding


def read_working_text(ws: Workspace, rel: str) -> str:
    return decode_text(ws.abspath(rel).read_bytes())[0]


# ----------------------------------------------------------- arquivos do doc
def list_files(ws: Workspace, doc: Document) -> list[str]:
    """Arquivos versionados do documento (caminhos relativos ao workspace)."""
    root = ws.abspath(doc.root)
    if doc.single or doc.kind == "pdf":
        return [doc.root] if root.is_file() else []
    out: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS and not d.startswith("."))
        for name in sorted(filenames):
            if name.startswith("."):
                continue
            path = Path(dirpath, name)
            if path.suffix.lower() not in TEXT_EXTS:
                continue
            try:
                if path.stat().st_size > MAX_FILE_BYTES:
                    continue
            except OSError:
                continue
            out.append(ws.rel(path))
    return out


def file_sha(path: Path) -> str:
    stat = path.stat()
    key = (str(path), stat.st_mtime_ns, stat.st_size)
    sha = _SHA_CACHE.get(key)
    if sha is None:
        sha = sha256_bytes(path.read_bytes())
        _SHA_CACHE[key] = sha
    return sha


def working_manifest(ws: Workspace, doc: Document) -> dict[str, str]:
    return {rel: file_sha(ws.abspath(rel)) for rel in list_files(ws, doc)}


def manifest_hash(files: dict[str, str]) -> str:
    return sha256_bytes("\n".join(f"{p}:{s}" for p, s in sorted(files.items())).encode())


# --------------------------------------------------------------- versões
def _versions_path(ws: Workspace, doc_id: str) -> Path:
    return ws.dir / "docs" / doc_id / "versions.json"


def load_versions(ws: Workspace, doc_id: str) -> list[Version]:
    return [Version.from_dict(v) for v in read_json(_versions_path(ws, doc_id), [])]


def latest_version(ws: Workspace, doc_id: str) -> Version | None:
    versions = load_versions(ws, doc_id)
    return versions[-1] if versions else None


def get_version(ws: Workspace, doc_id: str, n: int) -> Version:
    for version in load_versions(ws, doc_id):
        if version.n == n:
            return version
    raise MargaError(f"Versão v{n} do documento {doc_id} não existe.")


def previous_version(ws: Workspace, doc_id: str, n: int) -> Version | None:
    prev = [v for v in load_versions(ws, doc_id) if v.n < n]
    return prev[-1] if prev else None


@dataclass
class DocStatus:
    latest: Version | None
    modified: list[str] = field(default_factory=list)
    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)

    @property
    def dirty(self) -> bool:
        return bool(self.modified or self.added or self.removed)

    @property
    def changed(self) -> list[str]:
        return self.modified + self.added + self.removed


def doc_status(ws: Workspace, doc: Document) -> DocStatus:
    latest = latest_version(ws, doc.id)
    current = working_manifest(ws, doc)
    if latest is None:
        return DocStatus(None, added=sorted(current))
    old = latest.files
    return DocStatus(
        latest,
        modified=sorted(p for p in current if p in old and old[p] != current[p]),
        added=sorted(p for p in current if p not in old),
        removed=sorted(p for p in old if p not in current),
    )


def snapshot(ws: Workspace, doc: Document, origin: str, note: str = "", round_n: int | None = None) -> Version:
    rels = list_files(ws, doc)
    if not rels:
        raise MargaError(f"Nenhum arquivo versionável encontrado em {doc.root}.")
    files: dict[str, str] = {}
    encodings: dict[str, str] = {}
    for rel in rels:
        data = ws.abspath(rel).read_bytes()
        files[rel] = store_blob(ws, data)
        if doc.kind != "pdf":
            _, encoding = decode_text(data)
            if encoding != "utf-8":
                encodings[rel] = encoding
    with _LOCK:
        versions = load_versions(ws, doc.id)
        n = versions[-1].n + 1 if versions else 1
        version = Version(n=n, created_at=now_iso(), origin=origin, files=files, encodings=encodings,
                          note=note, round=round_n)
        versions.append(version)
        write_json(_versions_path(ws, doc.id), [v.to_dict() for v in versions])
    return version


# ------------------------------------------------------------ conteúdo
def version_text(ws: Workspace, doc: Document, version: Version, path: str) -> str:
    sha = version.files.get(path)
    if sha is None:
        raise MargaError(f"{path} não existe na v{version.n} de {doc.id}.")
    if doc.kind == "pdf":
        from .pdf import pdf_text

        return pdf_text(ws, sha).text
    return decode_text(read_blob(ws, sha))[0]


def version_texts(ws: Workspace, doc: Document, version: Version) -> dict[str, str]:
    return {path: version_text(ws, doc, version, path) for path in version.files}
