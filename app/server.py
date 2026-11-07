"""逃げ道の点検屋のサーバー（標準ライブラリの http.server。docs/03-design.md の 8 節）。

依頼者の PC の中だけで動かす（X-05）。既定では 127.0.0.1 だけで待ち受ける。
起動: .venv/Scripts/python.exe -m app.server [--port 8000] [--stub-tools] [--lan]

- 合言葉（F-01）: 環境変数 ESCAPECHECK_PASSPHRASE。無ければ起動時に入力するか、自動で作成して画面（コンソール）に表示する
- トークン: /api/login が返す。メモリの中だけに保持する。/api/login と /api/health 以外の API に必要（Authorization: Bearer）
- 静的ファイル: web/ を配信する。CSP のヘッダーで、読み込み先を自サーバーと国土地理院（地理院タイル・ハザードマップポータルサイト）だけにする
- 上限: リクエストは 256KB まで、文字起こしは 2,000字まで（X-03）
- エラーの応答は {"error": 利用者に見せてよい文}。API キーや内部の情報は含めない（X-01）
"""

from __future__ import annotations

import argparse
import getpass
import hmac
import json
import logging
import mimetypes
import re
import secrets
import socket
import sys
import threading
import time
from collections import deque
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from app import agents, config, orchestrator, privacy
from app.audit import AUDIT
from tools import plan_model as pm

log = logging.getLogger("escapecheck.server")

TOKEN_TTL_SEC = 12 * 60 * 60
LOGIN_FAILURE_WINDOW_SEC = 5 * 60
LOGIN_FAILURE_LIMIT = 5

#: 地図のタイルの読み込み先（地理院タイル、ハザードマップポータルサイトの津波浸水想定。タイルは disaportaldata から配信）
TILE_HOSTS = ("https://cyberjapandata.gsi.go.jp", "https://disaportal.gsi.go.jp", "https://disaportaldata.gsi.go.jp")

CSP = "; ".join([
    "default-src 'self'",
    "script-src 'self'",
    # 画面は textContent で表示するため、style 属性だけは許可する（文字の差し込みで CSS が作られることは無い）
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' data: " + " ".join(TILE_HOSTS),
    "connect-src 'self'",
    "font-src 'self'",
    "media-src 'self'",
    "object-src 'none'",
    "base-uri 'none'",
    "frame-ancestors 'none'",
    "form-action 'self'",
])

SECURITY_HEADERS = {
    "Content-Security-Policy": CSP,
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
    "Permissions-Policy": "microphone=(self), geolocation=(self), camera=(), payment=(), usb=()",
}

STATIC_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".ico": "image/x-icon",
    ".webmanifest": "application/manifest+json",
    ".txt": "text/plain; charset=utf-8",
    ".woff2": "font/woff2",
}

_JOB_ID_RE = re.compile(r"^job_[0-9a-f]{8}$")
_SAMPLE_ID_RE = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")
_PLAN_LIST_KEYS = ("supporters", "premises", "findings", "fixes", "approvals", "issues")


class ApiError(Exception):
    def __init__(self, status: int, message: str, **extra: Any) -> None:
        super().__init__(message)
        self.status = status
        self.message = message
        self.extra = extra


class AppState:
    """サーバーの状態（合言葉、トークン、ログインの失敗の記録）。プロセスのメモリの中だけ。"""

    def __init__(self, passphrase: str, allowed_hosts: set[str], web_dir: Path = config.WEB_DIR,
                 samples_dir: Path = config.SAMPLES_DIR) -> None:
        if not passphrase:
            raise ValueError("合言葉が空です")
        self._passphrase = passphrase.encode("utf-8")
        self.allowed_hosts = {h.lower() for h in allowed_hosts}
        self.web_dir = web_dir.resolve()
        self.samples_dir = samples_dir
        self._tokens: dict[str, float] = {}
        self._failures: deque[float] = deque()
        self._lock = threading.Lock()

    def check_passphrase(self, given: str) -> bool:
        return hmac.compare_digest(given.encode("utf-8"), self._passphrase)

    def login_blocked(self) -> bool:
        now = time.time()
        with self._lock:
            while self._failures and now - self._failures[0] > LOGIN_FAILURE_WINDOW_SEC:
                self._failures.popleft()
            return len(self._failures) >= LOGIN_FAILURE_LIMIT

    def record_failure(self) -> None:
        with self._lock:
            self._failures.append(time.time())

    def issue_token(self) -> str:
        token = secrets.token_urlsafe(32)
        with self._lock:
            now = time.time()
            for t in [t for t, exp in self._tokens.items() if exp < now]:
                del self._tokens[t]
            self._tokens[token] = now + TOKEN_TTL_SEC
        return token

    def token_valid(self, token: str) -> bool:
        with self._lock:
            for known, expiry in self._tokens.items():
                if hmac.compare_digest(known, token):
                    return expiry >= time.time()
        return False

    def revoke(self, token: str) -> None:
        with self._lock:
            self._tokens.pop(token, None)


def validate_plan(value: Any) -> pm.Plan:
    """端末から届いた計画の形を確認する。足りない一覧は空で補う。"""
    if not isinstance(value, dict):
        raise ApiError(400, "計画（plan）の形が正しくありません")
    for key in ("plan_id", "fields", "sources"):
        if key not in value:
            raise ApiError(400, f"計画（plan）に {key} がありません")
    if not isinstance(value["fields"], dict) or not isinstance(value["sources"], dict):
        raise ApiError(400, "計画（plan）の fields と sources は object にしてください")
    if not isinstance(value["plan_id"], str) or not re.fullmatch(r"[A-Za-z0-9_\-]{1,64}", value["plan_id"]):
        raise ApiError(400, "計画（plan）の plan_id が正しくありません")
    unknown = [k for k in value["fields"] if k not in pm.FIELDS]
    if unknown:
        raise ApiError(400, "様式に無い欄があります: " + ", ".join(map(str, unknown[:5])))
    for key in pm.FIELDS:
        value["fields"].setdefault(key, pm.FieldValue(value=None, status="unconfirmed"))
    for key in _PLAN_LIST_KEYS:
        value.setdefault(key, [])
        if not isinstance(value[key], list):
            raise ApiError(400, f"計画（plan）の {key} は配列にしてください")
    value.setdefault("schema_version", pm.SCHEMA_VERSION)
    value.setdefault("alias", "Aさん")
    value.setdefault("area_label", "")
    value.setdefault("status", "draft")
    return value  # type: ignore[return-value]


class Handler(BaseHTTPRequestHandler):
    server_version = "EscapeCheck"
    sys_version = ""
    protocol_version = "HTTP/1.1"
    state: AppState  # make_server が設定する

    # --- 共通 ---

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        # クエリ文字列（座標などが入るおそれ）を記録しない
        path = urlsplit(self.path).path
        log.info("%s %s %s", self.command, path, args[1] if len(args) > 1 else "")

    def _send(self, status: int, body: bytes, content_type: str, *, cache: str = "no-store",
              extra: dict[str, str] | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache)
        for key, value in SECURITY_HEADERS.items():
            self.send_header(key, value)
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, status: int, data: Any, extra: dict[str, str] | None = None) -> None:
        body = json.dumps(data, ensure_ascii=False, default=str).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8", extra=extra)

    def _drain_body(self) -> None:
        """読んでいない本文を読み捨てる。読まずに接続を閉じると、相手に応答が届かないことがある（Windows）。"""
        if getattr(self, "_body_read", True):
            return
        self._body_read = True
        try:
            remaining = int(self.headers.get("Content-Length") or "0")
        except ValueError:
            return
        if remaining > 4 * config.MAX_REQUEST_BYTES:
            return
        while remaining > 0:
            chunk = self.rfile.read(min(65536, remaining))
            if not chunk:
                break
            remaining -= len(chunk)

    def _error(self, status: int, message: str, **extra: Any) -> None:
        # エラーのときは接続を閉じる（読まなかった本文を、次の要求として読まないため）
        self._drain_body()
        headers = {"Connection": "close"}
        if status == 429:
            headers["Retry-After"] = "60"
        self.close_connection = True
        self._json(status, {"error": privacy.redact_secrets(message), **extra}, headers)

    def send_error(self, code: int, message: str | None = None, explain: str | None = None) -> None:
        """http.server が要求を読めなかったときの応答も、JSON とセキュリティのヘッダーにそろえる。"""
        try:
            self._error(code, "要求の形が正しくありません")
        except (ConnectionError, BrokenPipeError):
            pass

    def _host_ok(self) -> bool:
        host = (self.headers.get("Host") or "").strip().lower()
        if not host:
            return False
        name = host.rsplit(":", 1)[0] if not host.endswith("]") else host
        return name in self.state.allowed_hosts

    def _origin_ok(self) -> bool:
        origin = self.headers.get("Origin")
        if not origin:
            return True
        parts = urlsplit(origin)
        return (parts.netloc or "").lower() == (self.headers.get("Host") or "").lower()

    def _token(self) -> str:
        auth = self.headers.get("Authorization") or ""
        return auth[7:].strip() if auth.startswith("Bearer ") else ""

    def _require_token(self) -> None:
        token = self._token()
        if not token or not self.state.token_valid(token):
            raise ApiError(401, "合言葉を入力して、もう一度入ってください")

    def _read_json(self) -> dict[str, Any]:
        if "chunked" in (self.headers.get("Transfer-Encoding") or "").lower():
            raise ApiError(411, "Content-Length を付けて送ってください")
        try:
            length = int(self.headers.get("Content-Length") or "0")
        except ValueError:
            raise ApiError(400, "Content-Length が正しくありません") from None
        if length > config.MAX_REQUEST_BYTES:
            raise ApiError(413, "送るデータが大きすぎます（256KB まで）")  # 本文は _error が読み捨てる
        if length <= 0:
            raise ApiError(400, "送るデータがありません")
        raw = self.rfile.read(length)
        self._body_read = True
        content_type = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if content_type != "application/json":
            raise ApiError(415, "Content-Type を application/json にしてください")
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ApiError(400, "JSON の形が正しくありません") from None
        if not isinstance(data, dict):
            raise ApiError(400, "JSON の object を送ってください")
        return data

    def _dispatch(self, method: str) -> None:
        self._body_read = method not in ("POST", "PUT", "DELETE", "OPTIONS")
        try:
            if not self._host_ok():
                raise ApiError(421, "この住所（Host）からは使えません")
            parts = urlsplit(self.path)
            path = parts.path
            if path.startswith("/api/"):
                if method == "POST" and not self._origin_ok():
                    raise ApiError(403, "別のサイトからの要求は受け付けません")
                self._api(method, path, parse_qs(parts.query))
            elif method in ("GET", "HEAD"):
                self._static(path)
            else:
                raise ApiError(405, "この操作はできません")
        except ApiError as exc:
            self._error(exc.status, exc.message, **exc.extra)
        except (ConnectionError, BrokenPipeError):
            pass
        except Exception:  # noqa: BLE001  利用者には内部の情報を見せない
            log.error("unexpected error on %s %s\n%s", method, urlsplit(self.path).path, privacy.redacted_traceback())
            self._error(500, "サーバーでエラーが起きました。もう一度お試しください")

    def do_GET(self) -> None:  # noqa: N802
        self._dispatch("GET")

    def do_HEAD(self) -> None:  # noqa: N802
        self._dispatch("HEAD")

    def do_POST(self) -> None:  # noqa: N802
        self._dispatch("POST")

    def do_PUT(self) -> None:  # noqa: N802
        self._dispatch("PUT")

    def do_DELETE(self) -> None:  # noqa: N802
        self._dispatch("DELETE")

    def do_OPTIONS(self) -> None:  # noqa: N802
        self._dispatch("OPTIONS")

    # --- API ---

    def _api(self, method: str, path: str, query: dict[str, list[str]]) -> None:
        if path == "/api/health" and method == "GET":
            be = orchestrator.backend()
            self._json(200, {"ok": True, "stub_tools": bool(getattr(be, "is_stub", False)),
                             "claude_ready": config.api_key_available()})
            return
        if path == "/api/login" and method == "POST":
            self._login()
            return
        self._require_token()
        if method == "GET":
            if path.startswith("/api/jobs/"):
                job_id = path.rsplit("/", 1)[-1]
                if not _JOB_ID_RE.match(job_id):
                    raise ApiError(404, "点検のジョブが見つかりません")
                try:
                    self._json(200, orchestrator.get_job(job_id))
                except KeyError:
                    raise ApiError(404, "点検のジョブが見つかりません。もう一度「点検する」を押してください") from None
                return
            if path == "/api/audit":
                plan_id = (query.get("plan_id") or [None])[0]
                self._json(200, {"entries": AUDIT.entries(plan_id=plan_id, limit=500)})
                return
            if path == "/api/samples":
                self._json(200, {"samples": self._list_samples()})
                return
            if path.startswith("/api/samples/"):
                self._json(200, self._load_sample(path.rsplit("/", 1)[-1]))
                return
            if path == "/api/facts":
                raise ApiError(405, "地図の事実は POST /api/facts で、計画（plan）を送って取得してください")
            raise ApiError(404, "API が見つかりません")
        if method != "POST":
            raise ApiError(405, "この操作はできません")
        data = self._read_json()
        if path == "/api/logout":
            self.state.revoke(self._token())
            self._json(200, {"ok": True})
            return
        plan = validate_plan(data.get("plan"))
        try:
            if path == "/api/facts":
                target = data.get("target") or "home"
                result = orchestrator.facts(plan, str(target))
                result["sources"] = plan["sources"]  # 画面（C）は sources で出典表示を作る
                self._json(200, result)
            elif path == "/api/interview":
                utterance = data.get("utterance")
                if not isinstance(utterance, str) or not utterance.strip():
                    raise ApiError(400, "発言（utterance）が空です")
                if len(utterance) > config.MAX_UTTERANCE_CHARS:
                    raise ApiError(400, f"発言が長すぎます（{config.MAX_UTTERANCE_CHARS}字まで）")
                speaker = data.get("speaker") or "minsei"
                asked = data.get("asked_field")
                result = orchestrator.interview(plan, utterance, speaker, asked_field=asked if isinstance(asked, str) else None)
                self._json(200, result)
            elif path == "/api/check":
                self._json(200, {"job_id": orchestrator.start_check(plan)})
            elif path == "/api/approve":
                fix_id = data.get("fix_id")
                if not isinstance(fix_id, str):
                    raise ApiError(400, "改善案の ID（fix_id）がありません")
                by = data.get("by") or "minsei"
                plan = orchestrator.approve(plan, fix_id, by)
                self._json(200, {"plan": plan, "results": orchestrator.recheck(plan)})
            elif path == "/api/verify":
                issues = orchestrator.verify(plan)
                self._json(200, {"issues": issues, "plan": plan})
            elif path == "/api/finalize":
                # 完成にできないことは異常ではないので 200 で返す（画面は ok と reasons で表示を分ける）
                try:
                    self._json(200, {"ok": True, "reasons": [], "plan": orchestrator.finalize(plan)})
                except orchestrator.FinalizeRejected as exc:
                    self._json(200, {"ok": False, "reasons": exc.reasons, "plan": plan})
            else:
                raise ApiError(404, "API が見つかりません")
        except orchestrator.Busy as exc:
            raise ApiError(429, str(exc)) from None
        except agents.AgentUnavailable as exc:
            raise ApiError(503, str(exc)) from None
        except agents.AgentFailed as exc:
            raise ApiError(502, str(exc)) from None
        except ValueError as exc:
            raise ApiError(400, str(exc)) from None

    def _login(self) -> None:
        if self.state.login_blocked():
            raise ApiError(429, "合言葉の間違いが続いたため、しばらく入れません。少し待ってから入力してください")
        data = self._read_json()
        given = data.get("passphrase")
        if not isinstance(given, str) or not self.state.check_passphrase(given.strip()):
            self.state.record_failure()
            time.sleep(0.3)
            raise ApiError(401, "合言葉が違います")
        self._json(200, {"token": self.state.issue_token(), "expires_in": TOKEN_TTL_SEC})

    def _list_samples(self) -> list[dict[str, Any]]:
        if not self.state.samples_dir.is_dir():
            return []
        samples = []
        for file in sorted(self.state.samples_dir.glob("*.json")):
            if _SAMPLE_ID_RE.match(file.stem):
                samples.append({"id": file.stem})
        return samples

    def _load_sample(self, sample_id: str) -> Any:
        if not _SAMPLE_ID_RE.match(sample_id):
            raise ApiError(404, "サンプルデータが見つかりません")
        file = self.state.samples_dir / f"{sample_id}.json"
        if not file.is_file():
            raise ApiError(404, "サンプルデータが見つかりません")
        try:
            return json.loads(file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            raise ApiError(500, "サンプルデータを読めませんでした") from None

    # --- 静的ファイル ---

    def _static(self, path: str) -> None:
        if path in ("", "/"):
            path = "/index.html"
        elif path.endswith("/"):
            path += "index.html"
        relative = path.lstrip("/")
        if any(part.startswith(".") for part in relative.split("/")) or "\\" in relative or "\x00" in relative:
            raise ApiError(404, "ファイルが見つかりません")
        target = (self.state.web_dir / relative).resolve()
        if not target.is_relative_to(self.state.web_dir) or not target.is_file():
            raise ApiError(404, "ファイルが見つかりません")
        content_type = STATIC_TYPES.get(target.suffix.lower()) or mimetypes.guess_type(target.name)[0]
        if content_type is None:
            raise ApiError(404, "ファイルが見つかりません")
        self._send(200, target.read_bytes(), content_type, cache="no-cache")


class QuietThreadingHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    # Windows の SO_REUSEADDR は、使用中のポートに重ねて待ち受けられてしまう（別のプログラムに要求を横取りされる）。
    # Windows では使わず、SO_EXCLUSIVEADDRUSE で、このポートをほかのプログラムが使えないようにする
    allow_reuse_address = sys.platform != "win32"

    def server_bind(self) -> None:
        if sys.platform == "win32" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()

    def handle_error(self, request: Any, client_address: Any) -> None:
        # 相手が先に接続を切ったときのエラーは記録しない（ほかは、キーを伏せて記録する）
        exc = sys.exc_info()[1]
        if isinstance(exc, (ConnectionError, BrokenPipeError)):
            return
        log.error("request handling failed: %s", privacy.redact_secrets(repr(exc))[:300])


def make_server(host: str, port: int, state: AppState) -> ThreadingHTTPServer:
    handler = type("EscapeCheckHandler", (Handler,), {"state": state})
    return QuietThreadingHTTPServer((host, port), handler)


def lan_address() -> str | None:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 9))  # 送信はしない（経路だけを調べる。TEST-NET の住所）
            return s.getsockname()[0]
    except OSError:
        return None


def choose_passphrase() -> tuple[str, bool]:
    """合言葉を決める。(合言葉, 自動で作成したか)。"""
    import os

    value = os.environ.get("ESCAPECHECK_PASSPHRASE", "").strip()
    if value:
        return value, False
    if sys.stdin is not None and sys.stdin.isatty():
        entered = getpass.getpass("合言葉を決めて入力してください（画面には表示されません。空なら自動で作成します）: ").strip()
        if entered:
            return entered, False
    alphabet = "abcdefghjkmnpqrstuvwxyz23456789"
    return "".join(secrets.choice(alphabet) for _ in range(8)), True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="逃げ道の点検屋のサーバー（依頼者の PC の中だけで動かす）")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--lan", action="store_true", help="同じ Wi-Fi のスマートフォンから開けるようにする（インターネットには公開しない）")
    parser.add_argument("--stub-tools", action="store_true", help="計算をテスト用の仮の値で行う（tools/ の実装が入るまでの開発用）")
    parser.add_argument("--audit-file", action="store_true", help="監査ログを app/logs/ にも書き出す（デモの記録用。仮名だけ）")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    for handler in logging.getLogger().handlers:
        handler.setFormatter(privacy.RedactingFormatter("%(asctime)s %(levelname)s %(message)s"))

    import os

    if args.stub_tools:
        os.environ["ESCAPECHECK_STUB_TOOLS"] = "1"
    removed = agents.scrub_host_env()
    if removed:
        log.info("開発環境のセッションの環境変数を %d 個外しました（作品のエージェントに渡さない）", len(removed))
    if args.audit_file:
        AUDIT._file_path = config.LOG_DIR / f"audit-{time.strftime('%Y%m%d-%H%M%S')}.jsonl"  # noqa: SLF001

    passphrase, generated = choose_passphrase()
    hosts = {"localhost", "127.0.0.1", "[::1]"}
    bind = "127.0.0.1"
    if args.lan:
        address = lan_address()
        if address:
            hosts.add(address)
        bind = "0.0.0.0"
    try:
        server = make_server(bind, args.port, AppState(passphrase, hosts))
    except OSError:
        print(f"ポート {args.port} はほかのプログラムが使っています。--port で別の番号を指定してください。", flush=True)
        return 1

    print("逃げ道の点検屋のサーバーを起動しました。")
    print(f"  開く: http://localhost:{args.port}/")
    if args.lan:
        print(f"  同じ Wi-Fi のスマートフォン: http://{next((h for h in hosts if h[0].isdigit() and h != '127.0.0.1'), '?')}:{args.port}/")
        print("  注意: インターネットには公開しないでください。音声入力はスマートフォンでは使えないことがあります（HTTPS ではないため）。")
    if generated:
        print(f"  合言葉（自動で作成）: {passphrase}")
    if not config.api_key_available():
        print("  注意: Claude の API キーが環境変数にありません。聞き取り・点検・検証は動きません。")
    if os.environ.get("ESCAPECHECK_STUB_TOOLS") == "1":
        print("  注意: 計算はテスト用の仮の値です。画面や印刷物の数値を、実際の判断に使わないでください。")
    print("  止める: Ctrl+C", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
