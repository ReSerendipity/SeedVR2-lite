"""UI 验证专用实例：在 127.0.0.1:7999 起一份独立应用供截图比对。

不复用维护者正在跑的 7870 实例（那份进程仍持有改动前的 Python 代码，
i18n_flat / app_version 需要重启才会生效）。这里用同一套源码新起进程，
并把历史库指到临时文件，避免与生产实例争用 data/history.db。
"""

import socket
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import uvicorn  # noqa: E402

from app.integrated_app.config import load_config  # noqa: E402


def _free_port(preferred: int) -> int:
    with socket.socket() as s:
        try:
            s.bind(("127.0.0.1", preferred))
            return preferred
        except OSError:
            s.bind(("127.0.0.1", 0))
            return s.getsockname()[1]


def main() -> None:
    config = load_config()
    config.setdefault("history", {})["db_path"] = str(Path(tempfile.mkdtemp(prefix="sv2-ui-")) / "history.db")
    config.setdefault("model", {})["auto_load"] = False
    config.setdefault("server", {})["auto_open_browser"] = False

    port = _free_port(7999)
    print(f"[ui-verify] serving on http://127.0.0.1:{port}", flush=True)

    from app.integrated_app.app_server import create_app

    uvicorn.run(create_app(config), host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main()
