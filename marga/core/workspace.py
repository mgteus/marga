"""Workspace: a pasta do usuário e o estado do Marga em ``<workspace>/.marga/``."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import threading
from pathlib import Path
from typing import Any

from .models import Annotation, Document, Link, now_iso

MARGA_DIR = ".marga"
PROJECT_SCHEMA = "marga.project/v1"

_LOCK = threading.RLock()


class MargaError(Exception):
    """Erro esperado, com mensagem pronta para mostrar ao usuário."""


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise MargaError(
            f"Arquivo corrompido: {path} ({exc}). Corrija-o ou remova-o; o Marga não vai sobrescrevê-lo."
        ) from exc


def _umask() -> int:
    current = os.umask(0)
    os.umask(current)
    return current


_FILE_MODE = 0o666 & ~_umask()


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
    try:
        os.fchmod(fd, _FILE_MODE)  # mkstemp cria 0600; use as permissões normais da pasta
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def write_json(path: Path, data: Any) -> None:
    _atomic_write(path, json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8"))


def write_text(path: Path, text: str) -> None:
    _atomic_write(path, text.encode("utf-8"))


def write_bytes(path: Path, data: bytes) -> None:
    _atomic_write(path, data)


def _doc_letters(n: int) -> str:
    """1 -> A, 26 -> Z, 27 -> AA."""
    out = ""
    while n > 0:
        n, rem = divmod(n - 1, 26)
        out = chr(ord("A") + rem) + out
    return out


class Workspace:
    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().resolve()
        self.dir = self.root / MARGA_DIR

    # ------------------------------------------------------------------ abrir
    @classmethod
    def open(cls, root: str | Path, create: bool = True) -> Workspace:
        ws = cls(root)
        if not ws.root.is_dir():
            raise MargaError(f"Pasta não encontrada: {ws.root}")
        if not ws.dir.exists():
            if not create:
                raise MargaError(f"{ws.root} ainda não é um workspace do Marga.")
            ws.dir.mkdir()
            write_json(ws.project_path, ws._empty_project())
        return ws

    @property
    def project_path(self) -> Path:
        return self.dir / "marga.json"

    @staticmethod
    def _empty_project() -> dict[str, Any]:
        return {"schema": PROJECT_SCHEMA, "created_at": now_iso(), "docs": [], "counters": {}, "settings": {}}

    def load_project(self) -> dict[str, Any]:
        return read_json(self.project_path, self._empty_project())

    def save_project(self, project: dict[str, Any]) -> None:
        write_json(self.project_path, project)

    # --------------------------------------------------------------- caminhos
    def abspath(self, rel: str | Path) -> Path:
        return (self.root / rel).resolve()

    def rel(self, path: str | Path) -> str:
        path = Path(path).resolve()
        try:
            return path.relative_to(self.root).as_posix() or "."
        except ValueError as exc:
            raise MargaError(f"{path} está fora do workspace {self.root}") from exc

    def contains(self, path: str | Path) -> bool:
        try:
            Path(path).resolve().relative_to(self.root)
            return True
        except ValueError:
            return False

    # -------------------------------------------------------------------- ids
    def next_id(self, prefix: str) -> str:
        with _LOCK:
            project = self.load_project()
            counters = project.setdefault("counters", {})
            counters[prefix] = counters.get(prefix, 0) + 1
            self.save_project(project)
            return f"{prefix}{counters[prefix]}"

    def next_counter(self, name: str) -> int:
        with _LOCK:
            project = self.load_project()
            counters = project.setdefault("counters", {})
            counters[name] = counters.get(name, 0) + 1
            self.save_project(project)
            return counters[name]

    def peek_counter(self, name: str) -> int:
        return self.load_project().get("counters", {}).get(name, 0)

    # ------------------------------------------------------------- settings
    def get_setting(self, key: str, default: Any = None) -> Any:
        return self.load_project().get("settings", {}).get(key, default)

    def set_setting(self, key: str, value: Any) -> None:
        with _LOCK:
            project = self.load_project()
            project.setdefault("settings", {})[key] = value
            self.save_project(project)

    # ------------------------------------------------------------ documentos
    def docs(self) -> list[Document]:
        return [Document.from_dict(d) for d in self.load_project().get("docs", [])]

    def get_doc(self, doc_id: str) -> Document:
        for doc in self.docs():
            if doc.id == doc_id:
                return doc
        raise MargaError(f"Documento {doc_id} não existe neste workspace.")

    def new_doc_id(self) -> str:
        return _doc_letters(self.next_counter("doc"))

    def save_doc(self, doc: Document) -> None:
        with _LOCK:
            project = self.load_project()
            docs = [d for d in project.get("docs", []) if d["id"] != doc.id]
            docs.append(doc.to_dict())
            docs.sort(key=lambda d: (len(d["id"]), d["id"]))
            project["docs"] = docs
            self.save_project(project)

    def delete_doc(self, doc_id: str) -> None:
        with _LOCK:
            project = self.load_project()
            project["docs"] = [d for d in project.get("docs", []) if d["id"] != doc_id]
            self.save_project(project)
            for ann in self.annotations(doc_id):
                self.delete_annotation(ann.id)
            shutil.rmtree(self.dir / "docs" / doc_id, ignore_errors=True)
            shutil.rmtree(self.dir / "build" / doc_id, ignore_errors=True)

    # ------------------------------------------------------------- anotações
    @property
    def annotations_path(self) -> Path:
        return self.dir / "annotations.json"

    def annotations(self, doc_id: str | None = None) -> list[Annotation]:
        items = [Annotation.from_dict(a) for a in read_json(self.annotations_path, [])]
        if doc_id is not None:
            items = [a for a in items if a.doc == doc_id]
        return items

    def get_annotation(self, ann_id: str) -> Annotation | None:
        for ann in self.annotations():
            if ann.id == ann_id:
                return ann
        return None

    def save_annotations(self, changed: list[Annotation]) -> None:
        if not changed:
            return
        with _LOCK:
            current = {a["id"]: a for a in read_json(self.annotations_path, [])}
            stamp = now_iso()
            for ann in changed:
                ann.updated_at = stamp
                current[ann.id] = ann.to_dict()
            ordered = sorted(current.values(), key=lambda a: (a["id"][0], int(a["id"][1:] or 0)))
            write_json(self.annotations_path, ordered)

    def save_annotation(self, ann: Annotation) -> None:
        self.save_annotations([ann])

    def delete_annotation(self, ann_id: str) -> None:
        with _LOCK:
            items = [a for a in read_json(self.annotations_path, []) if a["id"] != ann_id]
            write_json(self.annotations_path, items)
            links = [lk for lk in read_json(self.links_path, []) if ann_id not in (lk["source"], lk["target"])]
            write_json(self.links_path, links)

    # ----------------------------------------------------------------- links
    @property
    def links_path(self) -> Path:
        return self.dir / "links.json"

    def links(self) -> list[Link]:
        return [Link.from_dict(lk) for lk in read_json(self.links_path, [])]

    def save_link(self, link: Link) -> None:
        with _LOCK:
            items = [lk for lk in read_json(self.links_path, []) if lk["id"] != link.id]
            items.append(link.to_dict())
            items.sort(key=lambda lk: int(lk["id"][1:] or 0))
            write_json(self.links_path, items)

    def delete_link(self, link_id: str) -> None:
        with _LOCK:
            items = [lk for lk in read_json(self.links_path, []) if lk["id"] != link_id]
            write_json(self.links_path, items)
