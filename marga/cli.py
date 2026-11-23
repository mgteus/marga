"""``marga [pasta] [opções do streamlit]`` abre o app no workspace indicado (padrão: pasta atual)."""

from __future__ import annotations

import sys
from pathlib import Path

MAX_UPLOAD_MB = 2048


def main(argv: list[str] | None = None) -> None:
    from streamlit.web import cli as stcli

    args = list(sys.argv[1:] if argv is None else argv)
    workspace = "."
    if args and not args[0].startswith("-"):
        workspace = args.pop(0)
    if not any(a.startswith("--server.maxUploadSize") for a in args):
        args.append(f"--server.maxUploadSize={MAX_UPLOAD_MB}")  # .zip do Overleaf com figuras passa fácil de 200 MB
    app = Path(__file__).with_name("app.py")
    sys.argv = ["streamlit", "run", str(app), *args, "--", "--workspace", str(Path(workspace).expanduser().resolve())]
    sys.exit(stcli.main())


if __name__ == "__main__":
    main()
