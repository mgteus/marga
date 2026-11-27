"""Compilações LaTeX em segundo plano: uma fila por processo do servidor, no máximo uma compilação por versão.

A interface chama ``submit`` ao registrar uma versão (ou ao abrir no modo PDF uma ainda não compilada) e consulta
``status`` para mostrar "compilando", a prévia sem figuras ou o PDF completo. A fila vale para todas as abas do
navegador; a trava no disco (``latex._BuildLock``) protege também contra outro servidor do Marga.
"""

from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass, replace

from . import latex
from .models import Document, Version
from .versions import manifest_hash
from .workspace import MargaError, Workspace

MAX_PARALLEL = 4
RUNNING = ("fila", "compilando", "previa")


@dataclass
class BuildStatus:
    root: str
    doc: str
    version: int
    state: str  # fila | compilando | previa (prévia pronta) | pronto | erro
    queued_at: float = 0.0
    started_at: float | None = None
    preview_at: float | None = None
    finished_at: float | None = None
    error: str | None = None
    result: latex.CompileResult | None = None

    @property
    def running(self) -> bool:
        return self.state in RUNNING

    @property
    def label(self) -> str:
        """Uma linha curta para a barra lateral."""
        return {
            "fila": f"⏳ v{self.version} na fila para compilar",
            "compilando": f"⏳ compilando a v{self.version}",
            "previa": f"👁️ v{self.version}: prévia pronta, compilando as figuras",
            "pronto": f"✅ v{self.version} compilada",
            "erro": f"❌ v{self.version} não compilou",
        }.get(self.state, self.state)


_lock = threading.Lock()
_jobs: dict[tuple[str, str, int], BuildStatus] = {}
_queue: queue.Queue = queue.Queue()
_workers: list[threading.Thread] = []


def _key(ws: Workspace, doc_id: str, n: int) -> tuple[str, str, int]:
    return str(ws.root.resolve()), doc_id, n


def _compiled(ws: Workspace, doc_id: str, version: Version) -> latex.CompileResult | None:
    try:
        result = latex.load_result(ws, doc_id, version.n)
    except MargaError:  # result.json estragado: conta como não compilado
        return None
    if result is None or not result.has_pdf or result.source_hash != manifest_hash(version.files):
        return None
    return result


def submit(ws: Workspace, doc: Document, version: Version, *, force: bool = False) -> BuildStatus | None:
    """Põe a versão na fila e volta na hora; devolve o estado dela, ou None se não há o que compilar.

    Não repete uma versão que já está na fila ou compilando, nem (sem ``force``) uma já compilada. Só documentos
    LaTeX com .tex principal, e só com o latexmk instalado.
    """
    if doc.kind != "latex" or not doc.main or not latex.can_compile():
        return None
    key = _key(ws, doc.id, version.n)
    with _lock:
        current = _jobs.get(key)
        if current is not None and current.running:
            return replace(current)
        if not force and _compiled(ws, doc.id, version) is not None:
            return None
        job = BuildStatus(key[0], doc.id, version.n, "fila", queued_at=time.time())
        _jobs[key] = job
        while len(_workers) < MAX_PARALLEL:
            worker = threading.Thread(target=_work, name=f"marga-latex-{len(_workers) + 1}", daemon=True)
            _workers.append(worker)
            worker.start()
        _queue.put((ws, doc, version, force, job))
        return replace(job)


def _work() -> None:
    while True:
        ws, doc, version, force, job = _queue.get()
        try:
            _run(ws, doc, version, force, job)
        finally:
            _queue.task_done()


def _set(job: BuildStatus, **changes) -> None:
    with _lock:
        for name, value in changes.items():
            setattr(job, name, value)


def _run(ws: Workspace, doc: Document, version: Version, force: bool, job: BuildStatus) -> None:
    _set(job, state="compilando", started_at=time.time())
    try:
        result = latex.compile_version(ws, doc, version, force=force,
                                       on_preview=lambda _p: _set(job, state="previa", preview_at=time.time()))
    except Exception as exc:  # noqa: BLE001 - vira o estado "erro", que a interface mostra
        _set(job, state="erro", error=str(exc) or type(exc).__name__, finished_at=time.time())
    else:
        _set(job, state="pronto", result=result, finished_at=time.time())


def status(ws: Workspace, doc_id: str, n: int) -> BuildStatus | None:
    """Estado da compilação da versão: da fila deste servidor ou, sem ela, do resultado no disco (None: nunca)."""
    key = _key(ws, doc_id, n)
    with _lock:
        job = _jobs.get(key)
        if job is not None:
            return replace(job)
    try:
        result = latex.load_result(ws, doc_id, n)
    except MargaError:
        result = None
    if result is None:
        return None
    return BuildStatus(key[0], doc_id, n, "pronto", result=result)


def active(ws: Workspace, doc_id: str | None = None) -> list[BuildStatus]:
    """Compilações na fila ou em andamento no workspace (de um documento, se ``doc_id``), da versão mais baixa."""
    root = str(ws.root.resolve())
    with _lock:
        found = [replace(j) for (r, d, _n), j in _jobs.items()
                 if r == root and j.running and (doc_id is None or d == doc_id)]
    return sorted(found, key=lambda j: (j.doc, j.version))


def wait(ws: Workspace, doc_id: str, n: int, timeout: float = 120.0) -> BuildStatus | None:
    """Espera a compilação da versão sair da fila e terminar (testes e scripts)."""
    end = time.monotonic() + timeout
    current = status(ws, doc_id, n)
    while current is not None and current.running and time.monotonic() < end:
        time.sleep(0.05)
        current = status(ws, doc_id, n)
    return current


def forget() -> None:
    """Esquece o estado das compilações já terminadas (testes); as em andamento continuam."""
    with _lock:
        for key in [k for k, j in _jobs.items() if not j.running]:
            del _jobs[key]
