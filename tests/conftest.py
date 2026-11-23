import shutil
from pathlib import Path

import pytest

from marga.core.workspace import Workspace

DEMO = Path(__file__).resolve().parents[1] / "examples" / "demo"


@pytest.fixture
def demo_ws(tmp_path) -> Workspace:
    dest = tmp_path / "demo"
    shutil.copytree(DEMO, dest, ignore=shutil.ignore_patterns(".marga", "uploads"))
    return Workspace.open(dest)


@pytest.fixture
def text_ws(tmp_path) -> Workspace:
    root = tmp_path / "ws"
    (root / "doc").mkdir(parents=True)
    (root / "doc" / "a.txt").write_text("linha um\nlinha dois com erro\nlinha três\n", encoding="utf-8")
    (root / "outro.md").write_text("# Outro\n\nTexto do outro documento.\n", encoding="utf-8")
    return Workspace.open(root)
