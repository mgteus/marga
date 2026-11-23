"""Compilação LaTeX com latexmk (vários documentos em paralelo) e leitura do log."""

from __future__ import annotations

import os
import re
import shutil
import signal
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from .models import Document, Version, now_iso
from .versions import SKIP_DIRS, TEXT_EXTS, manifest_hash, read_blob, working_manifest
from .workspace import MargaError, Workspace, read_json, write_json

ENGINES: dict[str, str] = {"pdflatex": "-pdf", "xelatex": "-pdfxe", "lualatex": "-pdflua"}
DEFAULT_TIMEOUT = 600  # uma dissertação com ~350 MB de figuras leva ~100 s (4 passadas + bibtex)

DOCCLASS_RE = re.compile(r"^[^%\n]*\\documentclass", re.M)
_MAGIC = re.compile(r"^\s*%\s*!\s*T[eE]X\s+(?:TS-)?program\s*=\s*([A-Za-z]+)", re.M)
_UNICODE_PKGS = re.compile(r"\\usepackage(?:\[[^\]]*\])?\{[^}]*\b(fontspec|unicode-math|polyglossia)\b")
_ERR = re.compile(r"^(?P<file>[^:\n()]*?\.[A-Za-z]{1,4}):(?P<line>\d+): (?P<msg>.+)$", re.M)
_WARN = re.compile(
    r"^(?:LaTeX|Package \S+|Class \S+|pdfTeX) [Ww]arning: (?P<msg>.+?)(?: on input line (?P<line>\d+))?\.?\s*$", re.M
)
_BOX = re.compile(r"^(?P<msg>(?:Overfull|Underfull) \\[hv]box \([^)]*\)).*? lines? (?P<line>\d+)", re.M)
_MISSING_FILE = re.compile(r"File `([^']+\.(?:sty|cls|def|clo|fd|bst|cfg))' not found")


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

    @property
    def has_pdf(self) -> bool:
        return bool(self.pdf) and Path(self.pdf).exists()

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


def materialize(ws: Workspace, doc: Document, version: Version, dest: Path) -> Path:
    """Cópia da pasta do documento com os arquivos de texto daquela versão (figuras vêm da pasta atual)."""
    vhash = manifest_hash(version.files)
    marker = dest / ".marga-version"
    if marker.exists() and marker.read_text() == vhash:
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


def compile_version(ws: Workspace, doc: Document, version: Version, *, force: bool = False,
                    timeout: int = DEFAULT_TIMEOUT) -> CompileResult:
    if doc.kind != "latex":
        raise MargaError(f"{doc.label} não é um documento LaTeX.")
    if not doc.main:
        raise MargaError(f"{doc.label} não tem .tex principal definido.")
    if not can_compile():
        raise MargaError("latexmk não encontrado no PATH; instale o TeX Live para compilar.")

    vhash = manifest_hash(version.files)
    cached = load_result(ws, doc.id, version.n)
    if not force and cached and cached.source_hash == vhash and cached.has_pdf:
        return cached

    bdir = build_dir(ws, doc.id, version.n)
    out = bdir / "out"
    out.mkdir(parents=True, exist_ok=True)
    if working_manifest(ws, doc) == version.files:
        src_dir = ws.abspath(doc.root)  # o disco é exatamente esta versão: compila no lugar
    else:
        src_dir = materialize(ws, doc, version, bdir / "src")
    _mirror_dirs(src_dir, out)

    main_rel = os.path.relpath(doc.main, doc.root)
    engine = doc.engine if doc.engine in ENGINES else "pdflatex"
    cmd = ["latexmk", ENGINES[engine], "-f", "-interaction=nonstopmode", "-file-line-error", "-synctex=1",
           f"-outdir={out}", main_rel]
    env = dict(os.environ, max_print_line="10000", error_line="254", half_error_line="238")

    start = time.monotonic()
    timed_out = False
    proc = subprocess.Popen(cmd, cwd=src_dir, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, env=env, start_new_session=True)
    try:
        raw, _ = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        raw, _ = proc.communicate()
    duration = round(time.monotonic() - start, 2)
    output = raw.decode("utf-8", errors="replace")

    stem = Path(main_rel).stem
    pdf, log = out / f"{stem}.pdf", out / f"{stem}.log"
    log_text = log.read_text(encoding="utf-8", errors="replace") if log.exists() else ""
    errors, warnings = parse_log(log_text, Path(src_dir), doc.root)
    if (out / f"{stem}.bcf").exists() and shutil.which("biber") is None:
        errors.append(LogIssue("erro", "O documento usa biblatex com biber, mas o biber não está instalado: "
                                       "as referências ficam sem bibliografia até instalá-lo."))
    if timed_out:
        errors.insert(0, LogIssue("erro", f"Tempo esgotado ({timeout} s); a compilação foi interrompida."))
    elif proc.returncode != 0 and not errors:
        errors.append(LogIssue("erro", f"latexmk terminou com código {proc.returncode}; veja a saída."))

    result = CompileResult(
        doc=doc.id,
        version=version.n,
        ok=proc.returncode == 0 and pdf.exists() and not errors,
        pdf=str(pdf) if pdf.exists() else None,
        log=str(log) if log.exists() else None,
        src_dir=str(src_dir),
        duration=duration,
        finished_at=now_iso(),
        source_hash=vhash,
        engine=engine,
        errors=errors,
        warnings=warnings,
        timed_out=timed_out,
        output_tail="\n".join(output.splitlines()[-40:]),
        command=cmd,
    )
    write_json(bdir / "result.json", result.to_dict())
    return result


def compile_many(ws: Workspace, jobs: list[tuple[Document, Version]], *, force: bool = False,
                 timeout: int = DEFAULT_TIMEOUT) -> list[CompileResult | Exception]:
    """Compila vários (documento, versão) ao mesmo tempo, cada um no seu outdir."""
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
