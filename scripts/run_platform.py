"""Start the independently operated local nozzle design application."""

import argparse
import importlib.util
import json
from pathlib import Path
import sys
import threading
import webbrowser
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from nozzle_platform.server import PlatformServer


def main():
    parser = argparse.ArgumentParser(description="多喷嘴阵列设计平台（本地版）")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--archive", type=Path)
    parser.add_argument("--gmsh-path", type=Path)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    workspace = ROOT.parent.parent
    config_file = ROOT / ".local" / "platform.json"
    config = json.loads(config_file.read_text(encoding="utf-8-sig")) if config_file.exists() else {}
    archive = args.archive or config.get("archive")
    if archive is None:
        candidate = workspace / "模型" / "钝体改型与仿真工况10.18.zip"
        archive = candidate if candidate.is_file() else None
    gmsh_path = args.gmsh_path or config.get("gmsh_path")
    if gmsh_path is None and importlib.util.find_spec("gmsh") is None:
        candidate = workspace / "分析" / "M8B最新模型_内部通流_20261008" / "tools" / "python"
        if (candidate / "gmsh.py").is_file():
            gmsh_path = candidate
    try:
        server = PlatformServer(("127.0.0.1", args.port), ROOT / "outputs" / "platform", archive=archive, gmsh_path=gmsh_path)
    except OSError as exc:
        if getattr(exc, "winerror", None) == 10048 or exc.errno in {48, 98, 10048}:
            existing_url = f"http://127.0.0.1:{args.port}"
            try:
                with urlopen(existing_url + "/api/bootstrap", timeout=3) as response:
                    if not response.headers.get("Server", "").startswith("NozzlePlatform/"):
                        raise ValueError("different application")
                print(f"Platform is already running: {existing_url}", flush=True)
                if not args.no_browser:
                    webbrowser.open(existing_url)
                return
            except Exception:
                raise SystemExit(f"Port {args.port} is in use. Close the other application or use --port 8766.") from exc
        raise
    url = f"http://127.0.0.1:{server.server_port}"
    print(f"Nozzle platform: {url}\nClose with Ctrl+C. Milestone 1: geometry only; Fluent is not connected.", flush=True)
    if not args.no_browser:
        threading.Timer(.7, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
