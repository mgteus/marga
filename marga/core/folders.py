"""Escolher a pasta do workspace: o seletor de pastas do sistema (Finder) e os workspaces usados recentemente.

O seletor abre no computador onde o servidor do Marga roda; os recentes ficam num arquivo do usuário, fora de
qualquer workspace (``MARGA_CONFIG_DIR`` muda a pasta, por exemplo nos testes).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from .workspace import MARGA_DIR, MargaError, read_json, write_json

PROMPT = "Escolha a pasta do workspace do Marga"
RECENT_MAX = 8
PICK_TIMEOUT = 600  # segundos com o seletor aberto


def config_dir() -> Path:
    env = os.environ.get("MARGA_CONFIG_DIR")
    if env:
        return Path(env).expanduser()
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "marga"
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "marga"


# ------------------------------------------------------------- seletor
def _applescript_text(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def picker_command(start: Path | None = None) -> list[str] | None:
    """Comando que abre o seletor de pastas desta máquina; ``None`` se não houver nenhum."""
    start = start if start and start.is_dir() else None
    if sys.platform == "darwin" and shutil.which("osascript"):
        where = f" default location (POSIX file {_applescript_text(str(start))})" if start else ""
        return ["osascript", "-e", "activate",
                "-e", f"POSIX path of (choose folder with prompt {_applescript_text(PROMPT)}{where})"]
    if shutil.which("zenity"):
        return ["zenity", "--file-selection", "--directory", f"--title={PROMPT}",
                *([f"--filename={start}/"] if start else [])]
    if shutil.which("kdialog"):
        return ["kdialog", "--getexistingdirectory", str(start or Path.home()), "--title", PROMPT]
    return None


def can_pick() -> bool:
    return picker_command() is not None


def pick_folder(start: Path | None = None) -> Path | None:
    """Abre o seletor de pastas e devolve a pasta escolhida; ``None`` se a pessoa cancelar."""
    cmd = picker_command(start)
    if cmd is None:
        raise MargaError("Não há seletor de pastas nesta máquina: digite o caminho da pasta.")
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=PICK_TIMEOUT)
    except subprocess.TimeoutExpired:
        return None
    except OSError as exc:
        raise MargaError(f"Não consegui abrir o seletor de pastas: {exc}") from exc
    # cancelar: o osascript sai com 1 e o erro -128; o zenity e o kdialog saem com 1; interrompido, < 0
    osascript = Path(cmd[0]).name == "osascript"
    if result.returncode < 0 or (result.returncode == 1 and (not osascript or "-128" in result.stderr)):
        return None
    if result.returncode != 0:
        raise MargaError(f"O seletor de pastas falhou: {result.stderr.strip() or result.returncode}")
    chosen = result.stdout.strip()
    return Path(chosen).expanduser() if chosen else None


# ------------------------------------------------------------- recentes
def _recent_path() -> Path:
    return config_dir() / "recentes.json"


def _load_recent() -> list[Path]:
    data = read_json(_recent_path(), [])
    paths = [Path(p) for p in data if isinstance(p, str)] if isinstance(data, list) else []
    return [p for p in paths if (p / MARGA_DIR).is_dir()]  # só os que ainda são workspaces


def recent_workspaces() -> list[Path]:
    """Workspaces usados por último (o mais recente primeiro) que ainda existem."""
    try:
        return _load_recent()
    except MargaError:
        return []


def remember_workspace(root: str | Path) -> None:
    """Põe o workspace no topo dos recentes (sem gravar nada se ele já está lá)."""
    root = Path(root).resolve()
    try:
        current = _load_recent()
    except MargaError:
        return  # arquivo de recentes corrompido: como no resto do Marga, ele não é sobrescrito
    if current[:1] == [root]:
        return
    paths = [root, *(p for p in current if p != root)][:RECENT_MAX]
    try:
        write_json(_recent_path(), [str(p) for p in paths])
    except OSError:
        pass  # sem onde gravar os recentes: o Marga funciona do mesmo jeito
