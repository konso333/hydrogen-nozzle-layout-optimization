"""Loopback-only HTTP host for a local engineering application; no cloud calls."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
from pathlib import Path
import socket
import traceback
from urllib.parse import unquote, urlsplit

from nozzle_platform.design import DesignStore
from nozzle_platform.models import MAX_UPLOAD_BYTES, ModelStore


STATIC = Path(__file__).with_name("static")


class PlatformServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False

    def server_bind(self):
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()

    def __init__(self, address, root, *, archive=None, gmsh_path=None):
        if address[0] != "127.0.0.1":
            raise ValueError("The local application only binds to 127.0.0.1.")
        self.models = ModelStore(Path(root) / "models", archive=archive, gmsh_path=gmsh_path)
        self.designs = DesignStore(Path(root) / "designs", self.models)
        super().__init__(address, Handler)


class Handler(BaseHTTPRequestHandler):
    server_version = "NozzlePlatform/0.1"

    def log_message(self, fmt, *args):
        print(fmt % args, flush=True)

    def send_data(self, raw, content_type, status=200, *, attachment=None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data: blob:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
        if attachment:
            self.send_header("Content-Disposition", f'attachment; filename="{attachment}"')
        self.end_headers()
        self.wfile.write(raw)

    def json(self, value, status=200):
        self.send_data(json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8"),
                       "application/json; charset=utf-8", status)

    def trusted_host(self):
        host = self.headers.get("Host", "")
        return host in {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}

    def do_GET(self):
        if not self.trusted_host():
            return self.json({"error": "仅接受本机访问。"}, 403)
        path = urlsplit(self.path).path
        if path == "/api/bootstrap":
            return self.json({"milestone": 1, "version": "0.1.0", "models": self.server.models.catalog(),
                              "simulation_available": False, "power_basis": "fuel_input"})
        if path.startswith("/downloads/"):
            key = path.removeprefix("/downloads/")
            with self.server.designs.lock:
                file = self.server.designs.downloads.get(key)
            if not file or not file.is_file():
                return self.json({"error": "下载已失效，请重新导出。"}, 404)
            return self.send_data(file.read_bytes(), "application/zip", attachment="nozzle_array.zip")
        files = {"/": "index.html", "/app.js": "app.js", "/app.css": "app.css", "/viewer.js": "viewer.js"}
        if path not in files:
            return self.json({"error": "页面不存在。"}, 404)
        file = STATIC / files[path]
        content_type = {".html": "text/html", ".js": "text/javascript", ".css": "text/css"}[file.suffix]
        self.send_data(file.read_bytes(), content_type + "; charset=utf-8")

    def do_POST(self):
        if not self.trusted_host() or self.headers.get("Origin") != "http://" + self.headers.get("Host", ""):
            return self.json({"error": "请从本机平台页面发起操作。"}, 403)
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= MAX_UPLOAD_BYTES:
                return self.json({"error": "请求为空或超过 20 MB。"}, 413)
            raw = self.rfile.read(length)
            if len(raw) != length:
                raise ValueError("上传未完成，请重试。")
            path = urlsplit(self.path).path
            if path == "/api/import":
                name = unquote(self.headers.get("X-Filename", "model.step")).replace("\\", "/").rsplit("/", 1)[-1]
                return self.json(self.server.models.add_source(name, raw))
            if self.headers.get_content_type() != "application/json":
                raise ValueError("请求必须使用 JSON。")
            def reject(value):
                raise ValueError("数值必须有限。")
            data = json.loads(raw, parse_constant=reject)
            if not isinstance(data, dict):
                raise ValueError("参数必须是对象。")
            if path == "/api/load-model":
                return self.json(self.server.models.load(data.get("source_id"), axis=data.get("axis", "y"), origin=data.get("origin")))
            if path == "/api/generate":
                return self.json(self.server.designs.create(data))
            if path == "/api/export":
                if type(data.get("cad", False)) is not bool:
                    raise ValueError("CAD 导出选项必须为布尔值。")
                return self.json(self.server.designs.export(data.get("scheme_id"), cad=data.get("cad", False)))
            return self.json({"error": "接口不存在。"}, 404)
        except (ValueError, TypeError, KeyError, OverflowError) as exc:
            self.json({"error": str(exc)}, 400)
        except Exception:
            traceback.print_exc()
            self.json({"error": "本次操作未完成，已保留本地记录。请检查运行窗口。"}, 500)
