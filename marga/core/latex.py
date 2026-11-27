"""Compilação LaTeX com latexmk (em segundo plano, veja ``builds``) e leitura do log.

Cada versão compila numa cópia própria (``v<n>/src``), num outdir vazio, em três etapas:

1. **rascunho**: o latexmk repete as passadas em modo rascunho (``-draftmode``; no xelatex, sem gerar o PDF) até
   as referências, o sumário e a bibliografia pararem de mudar. Essas passadas não leem as figuras;
2. **prévia**: uma passada com ``\\PassOptionsToPackage{draft}{graphicx}``. Cada figura vira uma moldura do
   mesmo tamanho, e ``previa.pdf`` sai com o layout do PDF completo, em poucos segundos;
3. **final**: uma passada normal (``latexmk -g``), com as figuras comprimidas no nível 1.

Numa dissertação com 172 figuras PNG (344 MB), as três etapas levam ~16 s, e a prévia sai aos ~4 s. Antes, as
três passadas completas levavam ~81 s, porque cada uma recomprimia todas as figuras.
"""

from __future__ import annotations

import atexit
import json
import os
import re
import shutil
import signal
import subprocess
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from .models import Document, Version, now_iso
from .versions import SKIP_DIRS, TEXT_EXTS, manifest_hash, read_blob
from .workspace import MargaError, Workspace, read_json, write_bytes, write_json

ENGINES: dict[str, str] = {"pdflatex": "-pdf", "xelatex": "-pdfxe", "lualatex": "-pdflua"}
DEFAULT_TIMEOUT = 600  # as três etapas juntas; projetos do Overleaf com muitas figuras passam de um minuto
COMMON = ["-f", "-interaction=nonstopmode", "-file-line-error", "-synctex=1"]
DRAFT: dict[str, list[str]] = {  # 1 · passadas que não geram PDF nem leem as figuras
    "pdflatex": ["-pdf", "-pdflatex=pdflatex -draftmode %O %S"],
    "lualatex": ["-pdflua", "-pdflualatex=lualatex --draftmode %O %S"],
    "xelatex": ["-xdv"],  # xelatex -no-pdf, sem o xdvipdfmx (é ele que embute as figuras)
}
PREVIEW_TEX = r"\PassOptionsToPackage{draft}{graphicx}"  # 2 · cada figura vira uma moldura do mesmo tamanho
FINAL: dict[str, list[str]] = {  # 3 · a passada com as figuras, comprimidas no nível 1 (o padrão é 9)
    "pdflatex": ["-pdf", r"-usepretex=\pdfcompresslevel=1"],
    "lualatex": ["-pdflua", r"-usepretex=\pdfvariable compresslevel=1"],
    "xelatex": ["-pdfxe", "-e", "$xdvipdfmx = q/xdvipdfmx -z 1 -E -o %D %O %S/"],
}
PREVIEW_FILES = ("previa.pdf", "previa.synctex.gz", "previa.json")
LOCK_FILE = "compilando.lock"

DOCCLASS_RE = re.compile(r"^[^%\n]*\\documentclass", re.M)
_MAGIC = re.compile(r"^\s*%\s*!\s*T[eE]X\s+(?:TS-)?program\s*=\s*([A-Za-z]+)", re.M)
_UNICODE_PKGS = re.compile(r"\\usepackage(?:\[[^\]]*\])?\{[^}]*\b(fontspec|unicode-math|polyglossia)\b")
_ERR = re.compile(r"^(?P<file>[^:\n()]*?\.[A-Za-z]{1,4}):(?P<line>\d+): (?P<msg>.+)$", re.M)
_WARN = re.compile(
    r"^(?:LaTeX|Package \S+|Class \S+|pdfTeX) [Ww]arning: (?P<msg>.+?)(?: on input line (?P<line>\d+))?\.?\s*$", re.M
)
_BOX = re.compile(r"^(?P<msg>(?:Overfull|Underfull) \\[hv]box \([^)]*\)).*? lines? (?P<line>\d+)", re.M)
_MISSING_FILE = re.compile(r"File `([^']+\.(?:sty|cls|def|clo|fd|bst|cfg))' not found")

_RUNNING: set[int] = set()  # grupos de processo das etapas em andamento neste servidor
_RUNNING_LOCK = threading.Lock()


@atexit.register
def _stop_running() -> None:
    """Ao sair, o servidor encerra as compilações em andamento: elas rodam numa sessão própria e sobreviveriam."""
    with _RUNNING_LOCK:
        groups = list(_RUNNING)
    for pgid in groups:
        try:
            os.killpg(pgid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass


def tools() -> dict[str, bool]:
    return {name: shutil.which(name) is not None for name in ("latexmk", "pdflatex", "xelatex", "lualatex", "synctex")}


def can_compile() -> bool:
    return shutil.which("latexmk") is not None


def find_main_candidates(folder: Path) -> list[Path]:
    """Arquivos .tex com ``\\documentclass`` (main.tex e os mais rasos primeiro)."""
    found: list[Path] = []
    for path in sorted(folder.rglob("*.tex")):
        parts = path.relative_to(folder).parts[:-1]
        if any(p.startswith(".") or p in SKIP_DIRS for p in parts):
            continue
        try:
            head = path.read_text(encoding="utf-8", errors="replace")[:50_000]
        except OSError:
            continue
        if DOCCLASS_RE.search(head):
            found.append(path)
    found.sort(key=lambda p: (p.name != "main.tex", len(p.relative_to(folder).parts), str(p)))
    return found


def detect_engine(text: str) -> str | None:
    match = _MAGIC.search(text[:5000])
    if match:
        name = match.group(1).lower()
        return name if name in ENGINES else None
    if _UNICODE_PKGS.search(text):
        return "xelatex"
    return None


@dataclass
class LogIssue:
    level: str  # "erro" | "aviso"
    message: str
    file: str | None = None  # relativo ao workspace
    line: int | None = None

    @property
    def location(self) -> str:
        if self.file and self.line:
            return f"{self.file}:{self.line}"
        if self.line:
            return f"linha {self.line}"
        return self.file or ""


@dataclass
class CompileResult:
    doc: str
    version: int
    ok: bool
    pdf: str | None
    log: str | None
    src_dir: str
    duration: float
    finished_at: str
    source_hash: str
    engine: str
    errors: list[LogIssue] = field(default_factory=list)
    warnings: list[LogIssue] = field(default_factory=list)
    timed_out: bool = False
    output_tail: str = ""
    command: list[str] = field(default_factory=list)
    stages: dict[str, float] = field(default_factory=dict)  # segundos de cada etapa: rascunho, previa, final
    preview: bool = False  # a prévia sem figuras (previa.json), e não o resultado final

    @property
    def has_pdf(self) -> bool:
        return bool(self.pdf) and Path(self.pdf).exists()

    @property
    def preview_seconds(self) -> float | None:
        """Quanto a prévia levou para sair, desde o início da compilação."""
        if "previa" not in self.stages:
            return None
        return self.stages.get("rascunho", 0.0) + self.stages["previa"]

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> CompileResult:
        names = {f.name for f in fields(cls)}
        data = {k: v for k, v in data.items() if k in names}
        data["errors"] = [LogIssue(**e) for e in data.get("errors", [])]
        data["warnings"] = [LogIssue(**w) for w in data.get("warnings", [])]
        return cls(**data)


def build_dir(ws: Workspace, doc_id: str, n: int) -> Path:
    return ws.dir / "build" / doc_id / f"v{n}"


def load_result(ws: Workspace, doc_id: str, n: int) -> CompileResult | None:
    data = read_json(build_dir(ws, doc_id, n) / "result.json", None)
    return CompileResult.from_dict(data) if data else None


def load_preview(ws: Workspace, doc_id: str, n: int) -> CompileResult | None:
    """A prévia sem figuras da última compilação da versão (``previa.json``), se houver."""
    data = read_json(build_dir(ws, doc_id, n) / "previa.json", None)
    return CompileResult.from_dict(data) if data else None


def load_pdf(ws: Workspace, doc_id: str, n: int) -> CompileResult | None:
    """O melhor PDF da versão: o final; sem ele, a prévia sem figuras; sem nenhum, o resultado com os erros."""
    result = load_result(ws, doc_id, n)
    if result is not None and result.has_pdf:
        return result
    preview = load_preview(ws, doc_id, n)
    if preview is not None and preview.has_pdf:
        return preview
    return result


def _map_file(name: str, src_dir: Path, doc_root: str) -> str | None:
    path = Path(name)
    if not path.is_absolute():
        path = src_dir / path
    path = Path(os.path.normpath(path))
    try:
        rel = path.relative_to(src_dir)
    except ValueError:
        return None
    return Path(os.path.normpath(Path(doc_root) / rel)).as_posix()


def parse_log(log_text: str, src_dir: Path, doc_root: str) -> tuple[list[LogIssue], list[LogIssue]]:
    src_dir = Path(os.path.normpath(src_dir))
    errors: list[LogIssue] = []
    seen: set[tuple] = set()
    for m in _ERR.finditer(log_text):
        file = _map_file(m["file"], src_dir, doc_root)
        message = m["msg"].strip()
        if file is None:
            message = f"{Path(m['file']).name}: {message}"
        missing = _MISSING_FILE.search(message)
        if missing:
            message += (f" (pacote LaTeX não instalado; para saber qual instalar: "
                        f"tlmgr search --global --file /{missing.group(1)})")
        key = (file, m["line"], message)
        if key not in seen:
            seen.add(key)
            errors.append(LogIssue("erro", message, file, int(m["line"])))
    warnings: list[LogIssue] = []
    for m in _WARN.finditer(log_text):
        warnings.append(LogIssue("aviso", m["msg"].strip(), None, int(m["line"]) if m["line"] else None))
    for m in _BOX.finditer(log_text):
        warnings.append(LogIssue("aviso", m["msg"].strip(), None, int(m["line"])))
    return errors, warnings[:100]


def _mirror_dirs(src: Path, out: Path) -> None:
    """O pdflatex precisa das subpastas no outdir para ``\\include{cap/x}``."""
    for dirpath, dirnames, _ in os.walk(src):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
        rel = Path(dirpath).relative_to(src)
        (out / rel).mkdir(parents=True, exist_ok=True)


def _link_or_copy(src: str, dst: str) -> None:
    """Figuras e outros binários entram como hard link (instantâneo, sem ocupar espaço); textos são copiados."""
    if Path(src).suffix.lower() not in TEXT_EXTS:
        try:
            os.link(src, dst)
            return
        except OSError:
            pass
    shutil.copy2(src, dst)


def materialize(ws: Workspace, doc: Document, version: Version, dest: Path, *, refresh: bool = False) -> Path:
    """Cópia da pasta do documento com os arquivos de texto daquela versão (figuras vêm da pasta atual).

    Sem ``refresh``, reaproveita uma cópia já feita da mesma versão.
    """
    vhash = manifest_hash(version.files)
    marker = dest / ".marga-version"
    if not refresh and marker.exists() and marker.read_text() == vhash:
        return dest
    if dest.exists():
        shutil.rmtree(dest)
    src_root = ws.abspath(doc.root)

    def ignore(_dir: str, names: list[str]) -> list[str]:
        return [n for n in names if n.startswith(".") or n in SKIP_DIRS]

    shutil.copytree(src_root, dest, ignore=ignore, copy_function=_link_or_copy)
    wanted = {os.path.normpath(p) for p in version.files}
    for path in list(dest.rglob("*")):
        if path.is_file() and path.suffix.lower() in TEXT_EXTS:
            rel_ws = os.path.normpath(Path(doc.root) / path.relative_to(dest))
            if rel_ws not in wanted:
                path.unlink()
    for rel, sha in version.files.items():
        target = dest / os.path.relpath(rel, doc.root)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.unlink(missing_ok=True)  # nunca escrever através de um link para o arquivo de trabalho
        target.write_bytes(read_blob(ws, sha))
    marker.write_text(vhash)
    return dest


# ------------------------------------------------------------------ trava
def _alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:  # existe, mas é de outro usuário
        return True
    return True


def _command_of(pid: int) -> str:
    """Linha de comando de um processo (vazia se ele não existe mais)."""
    try:
        return subprocess.run(["ps", "-o", "command=", "-p", str(pid)], capture_output=True, text=True,
                              timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


class _BuildLock:
    """``compilando.lock``: no máximo uma compilação por versão, também entre dois servidores do Marga.

    Guarda o pid do servidor e o grupo de processos da etapa em andamento. A trava de um servidor que já morreu é
    assumida; se o latexmk dela ainda roda neste outdir (um órfão), ele é encerrado antes.
    """

    def __init__(self, bdir: Path, label: str) -> None:
        self.path = bdir / LOCK_FILE
        self.out = bdir / "out"
        self.label = label

    def __enter__(self) -> _BuildLock:
        tmp = self.path.with_name(f".{LOCK_FILE}.{os.getpid()}.{threading.get_ident()}")
        tmp.write_text(json.dumps(self._info(None)), encoding="utf-8")
        try:
            for _ in range(3):
                try:
                    os.link(tmp, self.path)  # cria a trava já com o conteúdo, ou falha se ela existe
                    return self
                except FileExistsError:
                    self._take_over()
        finally:
            tmp.unlink(missing_ok=True)
        raise MargaError(f"Não consegui começar a compilação de {self.label}: tente de novo.")

    def __exit__(self, *_exc) -> None:
        self.path.unlink(missing_ok=True)

    def running(self, pgid: int | None) -> None:
        """Anota o grupo de processos da etapa em andamento."""
        write_json(self.path, self._info(pgid))

    def _info(self, pgid: int | None) -> dict:
        return {"pid": os.getpid(), "pgid": pgid, "since": now_iso()}

    def _take_over(self) -> None:
        try:
            info = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return
        except (OSError, ValueError):
            info = None
        info = info if isinstance(info, dict) else {}
        pid, pgid = info.get("pid"), info.get("pgid")
        if isinstance(pid, int) and _alive(pid):
            other = "" if pid == os.getpid() else " por outro processo do Marga"
            raise MargaError(f"{self.label} já está sendo compilada{other}.")
        if isinstance(pgid, int) and pgid > 0 and str(self.out) in _command_of(pgid):
            try:
                os.killpg(pgid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
        self.path.unlink(missing_ok=True)


# ------------------------------------------------------------- compilação
@dataclass
class _Step:
    command: list[str]
    returncode: int | None
    output: str
    timed_out: bool
    duration: float


def _run_step(cmd: list[str], cwd: Path, env: dict, deadline: float, lock: _BuildLock) -> _Step:
    """Roda uma etapa até o prazo; estourou, o grupo de processos inteiro é encerrado."""
    start = time.monotonic()
    if deadline - start <= 0:
        return _Step(cmd, None, "", True, 0.0)
    proc = subprocess.Popen(cmd, cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, env=env, start_new_session=True)
    with _RUNNING_LOCK:
        _RUNNING.add(proc.pid)
    timed_out = False
    try:
        lock.running(proc.pid)
        raw, _ = proc.communicate(timeout=max(0.01, deadline - time.monotonic()))
    except subprocess.TimeoutExpired:
        timed_out = True
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        raw, _ = proc.communicate()
    finally:
        with _RUNNING_LOCK:
            _RUNNING.discard(proc.pid)
    lock.running(None)
    output = raw.decode("utf-8", errors="replace")
    return _Step(cmd, proc.returncode, output, timed_out, round(time.monotonic() - start, 2))


def _save_preview(bdir: Path, out: Path, stem: str, doc: Document, version: Version, src_dir: Path, vhash: str,
                  engine: str, elapsed: float) -> CompileResult:
    """Guarda a prévia sem figuras, com o SyncTeX dela, ao lado do resultado (o PDF antes do previa.json)."""
    pdf = bdir / "previa.pdf"
    write_bytes(pdf, (out / f"{stem}.pdf").read_bytes())
    sync = out / f"{stem}.synctex.gz"
    if sync.exists():
        write_bytes(bdir / "previa.synctex.gz", sync.read_bytes())
    preview = CompileResult(doc=doc.id, version=version.n, ok=False, pdf=str(pdf), log=None, src_dir=str(src_dir),
                            duration=round(elapsed, 2), finished_at=now_iso(), source_hash=vhash, engine=engine,
                            preview=True)
    write_json(bdir / "previa.json", preview.to_dict())
    return preview


def compile_version(ws: Workspace, doc: Document, version: Version, *, force: bool = False,
                    timeout: int = DEFAULT_TIMEOUT,
                    on_preview: Callable[[CompileResult], None] | None = None) -> CompileResult:
    """Compila a versão nas três etapas do topo do módulo; ``on_preview`` recebe a prévia assim que ela sai.

    Sem ``force``, devolve o resultado guardado se ele já tem PDF. Uma compilação de verdade começa sempre de um
    outdir vazio e de uma cópia nova da versão, com a versão travada até o fim: um ``.aux`` estragado nunca prende
    a versão em erro, e editar os arquivos durante a compilação não muda o PDF.
    """
    if doc.kind != "latex":
        raise MargaError(f"{doc.label} não é um documento LaTeX.")
    if not doc.main:
        raise MargaError(f"{doc.label} não tem .tex principal definido.")
    if not can_compile():
        raise MargaError("latexmk não encontrado no PATH; instale o TeX Live para compilar.")

    vhash = manifest_hash(version.files)
    try:
        cached = load_result(ws, doc.id, version.n)
    except MargaError:  # result.json estragado: é só o registro da compilação, que ela mesma refaz
        cached = None
    if not force and cached and cached.source_hash == vhash and cached.has_pdf:
        return cached

    bdir = build_dir(ws, doc.id, version.n)
    bdir.mkdir(parents=True, exist_ok=True)
    with _BuildLock(bdir, f"{doc.id} v{version.n}") as lock:
        return _compile(ws, doc, version, vhash, bdir, timeout, on_preview, lock)


def _compile(ws: Workspace, doc: Document, version: Version, vhash: str, bdir: Path, timeout: int,
             on_preview: Callable[[CompileResult], None] | None, lock: _BuildLock) -> CompileResult:
    start = time.monotonic()
    deadline = start + timeout
    for name in ("result.json", *PREVIEW_FILES):
        (bdir / name).unlink(missing_ok=True)
    out = bdir / "out"
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)
    src_dir = materialize(ws, doc, version, bdir / "src", refresh=True)
    _mirror_dirs(src_dir, out)

    main_rel = os.path.relpath(doc.main, doc.root)
    stem = Path(main_rel).stem
    engine = doc.engine if doc.engine in ENGINES else "pdflatex"
    env = dict(os.environ, max_print_line="10000", error_line="254", half_error_line="238")
    stages: dict[str, float] = {}

    # 1 · rascunho: sai com código 12 ("não gerou PDF"), e isso é o esperado
    step = _run_step(["latexmk", *DRAFT[engine], *COMMON, f"-outdir={out}", main_rel], src_dir, env, deadline, lock)
    stages["rascunho"] = step.duration
    final = False
    if not step.timed_out:
        # 2 · prévia
        step = _run_step([engine, "-interaction=nonstopmode", "-file-line-error", "-synctex=1",
                          f"-output-directory={out}", f"-jobname={stem}", f"{PREVIEW_TEX}\\input{{{main_rel}}}"],
                         src_dir, env, deadline, lock)
        stages["previa"] = step.duration
        if not step.timed_out:
            if (out / f"{stem}.pdf").exists():
                preview = _save_preview(bdir, out, stem, doc, version, src_dir, vhash, engine,
                                        time.monotonic() - start)
                if on_preview is not None:
                    try:
                        on_preview(preview)
                    except Exception:  # noqa: BLE001 - quem acompanha a prévia nunca derruba a compilação
                        pass
            # 3 · final
            step = _run_step(["latexmk", *FINAL[engine], "-g", *COMMON, f"-outdir={out}", main_rel],
                             src_dir, env, deadline, lock)
            stages["final"] = step.duration
            final = True

    pdf, log = out / f"{stem}.pdf", out / f"{stem}.log"
    log_text = log.read_text(encoding="utf-8", errors="replace") if log.exists() else ""
    errors, warnings = parse_log(log_text, src_dir, doc.root)
    if (out / f"{stem}.bcf").exists() and shutil.which("biber") is None:
        errors.append(LogIssue("erro", "O documento usa biblatex com biber, mas o biber não está instalado: "
                                       "as referências ficam sem bibliografia até instalá-lo."))
    if step.timed_out:
        where = " na passada final" if final else ""
        errors.insert(0, LogIssue("erro", f"Tempo esgotado ({timeout} s){where}; a compilação foi interrompida."))
    elif step.returncode != 0 and not errors:
        errors.append(LogIssue("erro", f"latexmk terminou com código {step.returncode}; veja a saída."))
    has_pdf = final and not step.timed_out and pdf.exists()  # o PDF de uma etapa interrompida nunca vale

    result = CompileResult(
        doc=doc.id,
        version=version.n,
        ok=has_pdf and step.returncode == 0 and not errors,
        pdf=str(pdf) if has_pdf else None,
        log=str(log) if log.exists() else None,
        src_dir=str(src_dir),
        duration=round(time.monotonic() - start, 2),
        finished_at=now_iso(),
        source_hash=vhash,
        engine=engine,
        errors=errors,
        warnings=warnings,
        timed_out=step.timed_out,
        output_tail="\n".join(step.output.splitlines()[-40:]),
        command=step.command,
        stages=stages,
    )
    write_json(bdir / "result.json", result.to_dict())
    return result


def compile_many(ws: Workspace, jobs: list[tuple[Document, Version]], *, force: bool = False,
                 timeout: int = DEFAULT_TIMEOUT) -> list[CompileResult | Exception]:
    """Compila vários (documento, versão) ao mesmo tempo, cada um no seu outdir, e espera todos."""
    if not jobs:
        return []
    with ThreadPoolExecutor(max_workers=min(4, len(jobs))) as pool:
        futures = [pool.submit(compile_version, ws, doc, ver, force=force, timeout=timeout) for doc, ver in jobs]
    results: list[CompileResult | Exception] = []
    for future in futures:
        try:
            results.append(future.result())
        except Exception as exc:  # noqa: BLE001 - devolvido para a interface mostrar
            results.append(exc)
    return results
