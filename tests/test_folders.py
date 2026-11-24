"""Pasta do workspace: o seletor de pastas do sistema (Finder) e os workspaces recentes."""

import shutil
import sys
from pathlib import Path

import pytest

from marga.core import folders
from marga.core.workspace import MargaError, Workspace


def _ws(path: Path) -> Path:
    path.mkdir(parents=True)
    Workspace.open(path)
    return path.resolve()


def test_recent_workspaces_put_the_last_one_first(tmp_path):
    a, b, c = (_ws(tmp_path / name) for name in ("a", "b", "c"))
    for path in (a, b, c, a):
        folders.remember_workspace(path)
    assert folders.recent_workspaces() == [a, c, b]
    shutil.rmtree(c)  # pasta apagada sai da lista
    assert folders.recent_workspaces() == [a, b]
    for i in range(12):
        folders.remember_workspace(_ws(tmp_path / f"w{i}"))
    assert len(folders.recent_workspaces()) == folders.RECENT_MAX


def test_corrupted_recent_list_is_not_overwritten(tmp_path):
    path = folders.config_dir() / "recentes.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{ quebrado", encoding="utf-8")
    folders.remember_workspace(_ws(tmp_path / "a"))
    assert folders.recent_workspaces() == [] and path.read_text(encoding="utf-8") == "{ quebrado"


def test_picker_command_for_each_system(monkeypatch, tmp_path):
    def only(name):
        return lambda tool: f"/usr/bin/{tool}" if tool == name else None

    monkeypatch.setattr(folders.sys, "platform", "darwin")
    monkeypatch.setattr(folders.shutil, "which", only("osascript"))
    cmd = folders.picker_command(tmp_path)
    assert cmd[0] == "osascript" and "choose folder" in cmd[-1] and f'default location (POSIX file "{tmp_path}")' in cmd[-1]
    monkeypatch.setattr(folders.sys, "platform", "linux")
    monkeypatch.setattr(folders.shutil, "which", only("zenity"))
    assert folders.picker_command()[:3] == ["zenity", "--file-selection", "--directory"]
    monkeypatch.setattr(folders.shutil, "which", only("kdialog"))
    assert folders.picker_command()[:2] == ["kdialog", "--getexistingdirectory"]
    monkeypatch.setattr(folders.shutil, "which", lambda tool: None)
    assert folders.picker_command() is None and not folders.can_pick()
    with pytest.raises(MargaError, match="digite o caminho"):
        folders.pick_folder()


def test_pick_folder_returns_the_choice_and_none_on_cancel(monkeypatch, tmp_path):
    def use(*cmd):
        monkeypatch.setattr(folders, "picker_command", lambda start=None: list(cmd))

    use(sys.executable, "-c", "print('/Users/ana/tese/')")
    assert folders.pick_folder() == Path("/Users/ana/tese")
    use(sys.executable, "-c", "import sys; sys.exit(1)")  # zenity/kdialog: cancelar
    assert folders.pick_folder() is None
    fake = tmp_path / "osascript"  # o osascript sai com 1 tanto ao cancelar (-128) quanto em erro
    fake.write_text("#!/bin/sh\necho 'execution error: User canceled. (-128)' >&2\nexit 1\n", encoding="utf-8")
    fake.chmod(0o755)
    use(str(fake))
    assert folders.pick_folder() is None
    fake.write_text("#!/bin/sh\necho 'execution error: sem permissão (-1743)' >&2\nexit 1\n", encoding="utf-8")
    with pytest.raises(MargaError, match="-1743"):
        folders.pick_folder()
