"""SyncTeX via CLI ``synctex``: PDF → fonte (inverse search) e fonte → PDF (forward search)."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


def available() -> bool:
    return shutil.which("synctex") is not None


@dataclass(frozen=True)
class SyncBox:
    page: int
    h: float
    v: float
    width: float
    height: float

    @property
    def rect(self) -> tuple[float, float, float, float]:
        """Retângulo (x0, y0, x1, y1) em pontos, origem no topo. ``v`` é a linha de base."""
        return (self.h, self.v - self.height, self.h + self.width, self.v)


def _run(args: list[str], cwd: str | Path | None = None) -> str:
    try:
        result = subprocess.run(["synctex", *args], capture_output=True, text=True, timeout=15,
                                cwd=str(cwd) if cwd else None)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return result.stdout


def _records(output: str) -> list[dict[str, str]]:
    records: list[dict[str, str]] = []
    current: dict[str, str] = {}
    inside = False
    for line in output.splitlines():
        if line.startswith("SyncTeX result begin"):
            inside = True
            continue
        if line.startswith("SyncTeX result end"):
            break
        if not inside or ":" not in line:
            continue
        key, _, value = line.partition(":")
        if key in current:
            records.append(current)
            current = {}
        current[key] = value
    if current:
        records.append(current)
    return records


def inverse(pdf: str | Path, page: int, x: float, y: float, cwd: str | Path | None = None) -> list[tuple[Path, int]]:
    """Ponto no PDF → [(arquivo fonte absoluto, linha)]."""
    out = _run(["edit", "-o", f"{page}:{x:.2f}:{y:.2f}:{pdf}"], cwd=cwd)
    hits: list[tuple[Path, int]] = []
    base = Path(cwd) if cwd else Path(pdf).parent
    for rec in _records(out):
        name, line = rec.get("Input"), rec.get("Line")
        if not name or not line:
            continue
        path = Path(name)
        if not path.is_absolute():
            path = base / path
        try:
            hits.append((Path(os.path.normpath(path)), int(line)))
        except ValueError:
            continue
    return hits


@lru_cache(maxsize=4096)
def _forward_cached(pdf: str, mtime: float, src: str, line: int, cwd: str) -> tuple[SyncBox, ...]:
    candidates = [src]
    try:
        rel = os.path.relpath(src, cwd)
        candidates += [rel, "./" + rel]
    except ValueError:
        pass
    candidates.append(os.path.basename(src))
    for name in candidates:
        out = _run(["view", "-i", f"{line}:0:{name}", "-o", pdf], cwd=cwd)
        boxes = []
        for rec in _records(out):
            try:
                boxes.append(SyncBox(int(rec["Page"]), float(rec["h"]), float(rec["v"]), float(rec["W"]),
                                     float(rec["H"])))
            except (KeyError, ValueError):
                continue
        if boxes:
            return tuple(boxes)
    return ()


def forward(pdf: str | Path, src: str | Path, line: int, cwd: str | Path) -> list[SyncBox]:
    """Linha do fonte → caixas no PDF."""
    pdf = Path(pdf)
    if not pdf.exists():
        return []
    return list(_forward_cached(str(pdf), pdf.stat().st_mtime, str(Path(src).resolve()), int(line), str(cwd)))


# ------------------------------------------------------------------ figuras
# Para um \includegraphics escalado (width=0.48\linewidth…), o SyncTeX mede a figura no tamanho natural: a
# borda esquerda e a linha de base (h, v) estão certas, mas a largura e a altura podem passar da página. E,
# num ponto dentro da imagem, a busca inversa devolve a linha 0. TOL é a folga, em pontos, para casar (h, v)
# com o canto inferior esquerdo da imagem desenhada.
TOL = 3.0
_GRAPHICS = re.compile(r"\\includegraphics\b")
_TEX_COMMENT = re.compile(r"(?<!\\)%.*")


def _is_image_of(box: SyncBox, img: tuple[float, float, float, float]) -> bool:
    return abs(box.v - img[3]) <= TOL and abs(box.h - img[0]) <= TOL


def fit_images(boxes: list[SyncBox], images: list[tuple[float, float, float, float]]) -> list[tuple]:
    """Caixas → retângulos, trocando a caixa de cada imagem pela área real dela na página.

    A caixa da linha que contém as imagens (\\begin{figure}, \\centering) vale pelas imagens que ela contém, a
    menos que alguma imagem já tenha sido achada pela caixa do próprio \\includegraphics.
    """
    matched = list(dict.fromkeys(img for b in boxes for img in images if _is_image_of(b, img)))
    out = list(matched)
    for b in boxes:
        if any(_is_image_of(b, img) for img in images):
            continue
        inside = [img for img in images if abs(b.v - img[3]) <= TOL and b.h - TOL <= img[0] <= b.h + b.width]
        if not inside:
            out.append(b.rect)
        elif not matched:
            out += [img for img in inside if img not in out]
    return out


def figure_lines(text: str) -> list[int]:
    """Linhas do fonte com \\includegraphics fora de comentário."""
    return [i for i, line in enumerate(text.splitlines(), start=1) if _GRAPHICS.search(_TEX_COMMENT.sub("", line))]


def figure_at(pdf: str | Path, page: int, x: float, y: float, src: str | Path, text: str,
              cwd: str | Path) -> int | None:
    """Linha do \\includegraphics da figura sob o ponto, para quando a busca inversa só devolve a linha 0.

    Vale a figura com a linha de base logo abaixo do ponto e, entre figuras lado a lado, a de borda esquerda
    mais próxima à esquerda do ponto.
    """
    best, best_key = None, None
    for line in figure_lines(text):
        for b in forward(pdf, src, line, cwd):
            if b.page != page or b.h - TOL > x or b.v + TOL < y:
                continue
            key = (b.v - y, -b.h)
            if best_key is None or key < best_key:
                best, best_key = line, key
    return best
