"""PowerPoint → PDF com o LibreOffice (opcional), para o modo PDF dos documentos .pptx.

A conversão roda em segundo plano (``soffice --headless``), com um perfil do LibreOffice só do Marga, em
``.marga/cache/libreoffice/``: funciona com o LibreOffice aberto e não mexe nos ajustes dele. O PDF de cada
versão fica em ``.marga/build/<doc>/v<n>/slides.pdf`` e não é refeito, porque versões são imutáveis. Slides
ocultos também vão para o PDF, para a página N ser sempre o slide N.

Os arquivos são entrada não confiável: o LibreOffice converte sem rodar macros, e a conversão tem tempo limite.
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import tempfile
import threading
from pathlib import Path

from .models import Document, Version
from .versions import read_blob
from .workspace import MargaError, Workspace

SOFFICE = ("soffice", "libreoffice", "/Applications/LibreOffice.app/Contents/MacOS/soffice")
PDF_FILTER = 'pdf:impress_pdf_Export:{"ExportHiddenSlides":{"type":"boolean","value":"true"}}'
TIMEOUT = 300
INSTALL_HINT = "instale o LibreOffice (no macOS: brew install --cask libreoffice; no Debian/Ubuntu: apt install libreoffice-impress)"

_LOCK = threading.Lock()  # uma conversão por vez: o perfil do LibreOffice não aceita duas instâncias


def soffice() -> str | None:
    """Caminho do LibreOffice (``soffice``), ou ``None`` se não está instalado."""
    for name in SOFFICE:
        found = shutil.which(name) if "/" not in name else (name if os.access(name, os.X_OK) else None)
        if found:
            return found
    return None


def available() -> bool:
    return soffice() is not None


def pdf_path(ws: Workspace, doc: Document, version: Version) -> Path:
    return ws.dir / "build" / doc.id / f"v{version.n}" / "slides.pdf"


def to_pdf(ws: Workspace, doc: Document, version: Version, timeout: int = TIMEOUT) -> Path:
    """PDF da versão (uma página por slide), convertido na primeira vez e reaproveitado depois."""
    target = pdf_path(ws, doc, version)
    if target.exists():
        return target
    exe = soffice()
    if exe is None:
        raise MargaError(f"Para ver {doc.label} como PDF, {INSTALL_HINT}.")
    if doc.root not in version.files:
        raise MargaError(f"{doc.root} não existe na v{version.n} de {doc.id}.")
    with _LOCK:
        if target.exists():
            return target
        target.parent.mkdir(parents=True, exist_ok=True)
        profile = ws.dir / "cache" / "libreoffice"
        with tempfile.TemporaryDirectory(dir=ws.dir) as tmp:
            src = Path(tmp) / f"slides{Path(doc.root).suffix.lower() or '.pptx'}"
            src.write_bytes(read_blob(ws, version.files[doc.root]))
            cmd = [exe, f"-env:UserInstallation={profile.resolve().as_uri()}", "--headless", "--norestore",
                   "--nolockcheck", "--nodefault", "--nologo", "--convert-to", PDF_FILTER, "--outdir", tmp, str(src)]
            proc = subprocess.Popen(cmd, cwd=tmp, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, start_new_session=True)
            try:
                raw, _ = proc.communicate(timeout=timeout)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                proc.communicate()
                raise MargaError(f"O LibreOffice não terminou de converter {doc.label} em {timeout} s.") from None
            out = src.with_suffix(".pdf")
            if not out.exists():
                lines = [ln for ln in raw.decode("utf-8", errors="replace").splitlines()
                         if ln.strip() and "Fontconfig" not in ln and "cachedir" not in ln]
                detail = " ".join(lines[-3:]) or f"código {proc.returncode}"
                raise MargaError(f"O LibreOffice não converteu {doc.label} em PDF ({detail}).")
            os.replace(out, target)
    return target
