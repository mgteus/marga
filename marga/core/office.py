"""PowerPoint → PDF, para o modo PDF dos documentos .pptx: pelo próprio PowerPoint (Mac) ou pelo LibreOffice.

- **PowerPoint** (macOS, se instalado; o padrão): o PDF sai exatamente como no PowerPoint, com as fontes. O
  Marga põe uma cópia da versão numa pasta de troca fixa (``exchange_dir()``, na configuração do Marga), pede
  por AppleScript para o PowerPoint exportar o PDF e fecha a cópia. O PowerPoint é isolado pelo macOS: na
  primeira vez, ele pede acesso a essa pasta (uma vez só, para todos os workspaces), e o macOS pergunta se o app
  que roda o Marga pode controlar o PowerPoint.
- **LibreOffice**: em segundo plano (``soffice --headless``), com um perfil só do Marga em
  ``.marga/cache/libreoffice/``. Fontes e efeitos que ele não tem ficam diferentes.

O PDF de cada versão fica em ``.marga/build/<doc>/v<n>/slides-<conversor>.pdf`` e não é refeito, porque versões
são imutáveis. Slides ocultos também vão para o PDF, para a página N ser sempre o slide N. Os arquivos são
entrada não confiável: nenhum dos dois roda macros, e a conversão tem tempo limite.
"""

from __future__ import annotations

import io
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import uuid
import zipfile
from pathlib import Path

from .models import Document, Version
from .versions import read_blob
from .workspace import MargaError, Workspace

ENGINES = {"powerpoint": "PowerPoint", "libreoffice": "LibreOffice"}
SOFFICE = ("soffice", "libreoffice", "/Applications/LibreOffice.app/Contents/MacOS/soffice")
POWERPOINT_APP = Path("/Applications/Microsoft PowerPoint.app")
PDF_FILTER = 'pdf:impress_pdf_Export:{"ExportHiddenSlides":{"type":"boolean","value":"true"}}'
TIMEOUT = 300
INSTALL_HINT = ("instale o LibreOffice (no macOS: brew install --cask libreoffice; no Debian/Ubuntu: apt install "
                "libreoffice-impress) ou, no Mac, use o Microsoft PowerPoint")

_LOCK = threading.Lock()  # uma conversão por vez: o perfil do LibreOffice e a janela do PowerPoint são únicos

# Abre a cópia, exporta o PDF e fecha só ela; se o PowerPoint não estava aberto, fecha o app no fim.
# Os caminhos são convertidos antes do "tell": dentro dele quem converte é o PowerPoint, que, isolado pelo
# macOS, corta o caminho na pasta dele (…/com.microsoft.Powerpoint/Data.pdf) e trava pedindo acesso.
_POWERPOINT_SCRIPT = """
on run argv
    set src to item 1 of argv
    set srcFile to POSIX file src
    set pdfFile to (POSIX file (item 2 of argv)) as string
    set wasRunning to application "Microsoft PowerPoint" is running
    with timeout of {timeout} seconds
        tell application "Microsoft PowerPoint"
            open srcFile
            set deck to missing value
            repeat with p in (get presentations)
                if (full name of p) is src then set deck to contents of p
            end repeat
            if deck is missing value then set deck to active presentation
            try
                save deck in pdfFile as save as PDF
            on error errText number errNumber
                close deck saving no
                error errText number errNumber
            end try
            close deck saving no
            if not wasRunning and (count of presentations) is 0 then quit
        end tell
    end timeout
end run
"""


def soffice() -> str | None:
    """Caminho do LibreOffice (``soffice``), ou ``None`` se não está instalado."""
    for name in SOFFICE:
        found = shutil.which(name) if "/" not in name else (name if os.access(name, os.X_OK) else None)
        if found:
            return found
    return None


def available() -> bool:
    return soffice() is not None


def powerpoint() -> bool:
    """O Microsoft PowerPoint está instalado (só no Mac: a exportação é por AppleScript)."""
    return sys.platform == "darwin" and POWERPOINT_APP.exists()


def exchange_dir() -> Path:
    """Pasta onde o Marga deixa a cópia para o PowerPoint e de onde pega o PDF (o PowerPoint precisa de acesso a ela)."""
    from .folders import config_dir

    return config_dir() / "powerpoint"


def engine_for(doc: Document) -> str | None:
    """Conversor do documento: o escolhido nos ajustes ou, no automático, o PowerPoint (fiel) e depois o LibreOffice."""
    installed = {"powerpoint": powerpoint(), "libreoffice": available()}
    if doc.engine in ENGINES and installed[doc.engine]:
        return doc.engine
    return next((name for name in ENGINES if installed[name]), None)


def pdf_path(ws: Workspace, doc: Document, version: Version, engine: str) -> Path:
    return ws.dir / "build" / doc.id / f"v{version.n}" / f"slides-{engine}.pdf"


def to_pdf(ws: Workspace, doc: Document, version: Version, engine: str | None = None, timeout: int = TIMEOUT) -> Path:
    """PDF da versão (uma página por slide), convertido na primeira vez e reaproveitado depois."""
    engine = engine or engine_for(doc)
    if engine not in ENGINES:
        raise MargaError(f"Para ver {doc.label} como PDF, {INSTALL_HINT}.")
    target = pdf_path(ws, doc, version, engine)
    if target.exists():
        return target
    if doc.root not in version.files:
        raise MargaError(f"{doc.root} não existe na v{version.n} de {doc.id}.")
    with _LOCK:
        if target.exists():
            return target
        target.parent.mkdir(parents=True, exist_ok=True)
        data = read_blob(ws, version.files[doc.root])
        if engine == "powerpoint":
            _powerpoint(data, target, doc, timeout)
        else:
            _libreoffice(ws, data, target, doc, timeout)
    return target


def show_hidden_slides(data: bytes) -> bytes:
    """Cópia do .pptx com os slides ocultos reexibidos (o PowerPoint não exporta slides ocultos)."""
    src = zipfile.ZipFile(io.BytesIO(data))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as out:
        for info in src.infolist():
            body = src.read(info)
            if re.fullmatch(r"ppt/slides/slide\d+\.xml", info.filename):
                body = re.sub(rb'(<p:sld\b[^>]*?)\s+show="(?:0|false)"', rb"\1", body, count=1)
            out.writestr(info, body)
    return buf.getvalue()


def _powerpoint(data: bytes, target: Path, doc: Document, timeout: int) -> None:
    if not powerpoint():
        raise MargaError(f"O Microsoft PowerPoint não está instalado: escolha o LibreOffice nos ajustes de {doc.label}.")
    folder = exchange_dir()
    stem = f"marga-{uuid.uuid4().hex[:10]}"
    src, out = folder / f"{stem}.pptx", folder / f"{stem}.pdf"
    try:
        folder.mkdir(parents=True, exist_ok=True)
        src.write_bytes(show_hidden_slides(data))
    except OSError as exc:
        raise MargaError(f"Não consegui preparar a cópia para o PowerPoint em {folder}: {exc}") from exc
    try:
        try:
            proc = subprocess.run(["osascript", "-", str(src), str(out)], input=_POWERPOINT_SCRIPT.format(timeout=timeout),
                                  capture_output=True, text=True, timeout=timeout + 30)
        except subprocess.TimeoutExpired:
            raise MargaError(f"O PowerPoint não terminou de exportar {doc.label}: veja se ele mostra alguma janela "
                             "(aviso, login) e tente de novo.") from None
        if proc.returncode != 0 or not out.exists():
            raise MargaError(_powerpoint_error(proc.stderr, doc))
        shutil.move(str(out), target)
    finally:
        src.unlink(missing_ok=True)
        out.unlink(missing_ok=True)


def _powerpoint_error(stderr: str, doc: Document) -> str:
    if "-1743" in stderr:
        return ("O macOS não deixou o Marga controlar o PowerPoint. Permita em Ajustes do Sistema › Privacidade e "
                "Segurança › Automação (o app que roda o Marga → Microsoft PowerPoint) e tente de novo.")
    if "-9074" in stderr:
        return (f"O PowerPoint não teve acesso à pasta {exchange_dir()}. Tente de novo e, quando ele pedir, clique em "
                "Select… e conceda acesso a essa pasta (só na primeira vez).")
    if "-1712" in stderr:
        return (f"O PowerPoint não respondeu ao exportar {doc.label}: veja se ele mostra alguma janela (aviso, login) "
                "e tente de novo.")
    detail = " ".join(stderr.split())[-200:] or "sem detalhes"
    return f"O PowerPoint não exportou {doc.label} em PDF ({detail})."


def _libreoffice(ws: Workspace, data: bytes, target: Path, doc: Document, timeout: int) -> None:
    exe = soffice()
    if exe is None:
        raise MargaError(f"Para ver {doc.label} como PDF, {INSTALL_HINT}.")
    profile = ws.dir / "cache" / "libreoffice"
    with tempfile.TemporaryDirectory(dir=ws.dir) as tmp:
        src = Path(tmp) / f"slides{Path(doc.root).suffix.lower() or '.pptx'}"
        src.write_bytes(data)
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
