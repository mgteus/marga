"""SyncTeX via CLI ``synctex``: PDF → fonte (inverse search) e fonte → PDF (forward search)."""

from __future__ import annotations

import os
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
