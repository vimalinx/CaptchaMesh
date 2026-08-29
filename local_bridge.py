#!/usr/bin/env python3
"""Loopback-only 2Captcha compatibility bridge for the encrypted relay."""
from __future__ import annotations

import asyncio
import hmac
import html
import io
import json
import secrets
import sqlite3
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable

import qrcode
import qrcode.image.svg
from quart import Quart, Response, jsonify, request

import pair_device
from diagnostic_log import DiagnosticLog
from challenge_protocol import legacy_result
from relay_client import RelayClient, RelayClientError
from twocaptcha_compat import (
    TwoCaptchaCompatError,
    translate_create_request,
    translate_solution,
    translate_v1_request,
)


BRIDGE_SCHEMA = """
CREATE TABLE IF NOT EXISTS bridge_tasks (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  protocol TEXT NOT NULL,
  external_type TEXT NOT NULL,
  task_json TEXT NOT NULL,
  status TEXT NOT NULL,
  solution_json TEXT,
  error_code TEXT,
  error_description TEXT,
  feedback TEXT,
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL
);
"""
RESULT_TTL_SECONDS = 600


class BridgeTaskStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            self.path.parent.chmod(0o700)
        except OSError:
            pass
        with self.connect() as connection:
            connection.executescript(BRIDGE_SCHEMA)
            connection.execute(
                "UPDATE bridge_tasks SET status='failed',"
                " error_code='ERROR_BRIDGE_RESTARTED',"
                " error_description='local bridge restarted before the task completed',"
                " updated_at=? WHERE status IN ('pending','processing')",
                (time.time(),),
            )
        try:
            self.path.chmod(0o600)
        except OSError:
            pass

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def create(self, protocol: str, task: dict[str, Any], external_type: str) -> int:
        timestamp = time.time()
        with self.connect() as connection:
            self._sweep(connection, timestamp)
            cursor = connection.execute(
                "INSERT INTO bridge_tasks"
                " (protocol,external_type,task_json,status,created_at,updated_at)"
                " VALUES (?,?,?,'pending',?,?)",
                (
                    protocol,
                    external_type,
                    json.dumps(task, ensure_ascii=False, separators=(",", ":")),
                    timestamp,
                    timestamp,
                ),
            )
            return int(cursor.lastrowid)

    def get(self, task_id: Any) -> sqlite3.Row | None:
        if isinstance(task_id, str) and task_id.isdigit():
            task_id = int(task_id)
        if not isinstance(task_id, int) or isinstance(task_id, bool) or task_id < 1:
            return None
        with self.connect() as connection:
            self._sweep(connection, time.time())
            return connection.execute(
                "SELECT * FROM bridge_tasks WHERE id=?", (task_id,)
            ).fetchone()

    @staticmethod
    def _sweep(connection: sqlite3.Connection, timestamp: float) -> None:
        connection.execute(
            "DELETE FROM bridge_tasks WHERE status IN ('ready','failed') AND updated_at<?",
            (timestamp - RESULT_TTL_SECONDS,),
        )

    def set_processing(self, task_id: int) -> None:
        with self.connect() as connection:
            connection.execute(
                "UPDATE bridge_tasks SET status='processing',updated_at=? WHERE id=?",
                (time.time(), task_id),
            )

    def set_solution(self, task_id: int, solution: dict[str, Any]) -> None:
        with self.connect() as connection:
            connection.execute(
                "UPDATE bridge_tasks SET status='ready',solution_json=?,updated_at=? WHERE id=?",
                (
                    json.dumps(solution, ensure_ascii=False, separators=(",", ":")),
                    time.time(),
                    task_id,
                ),
            )

    def set_error(self, task_id: int, code: str, description: str) -> None:
        with self.connect() as connection:
            connection.execute(
                "UPDATE bridge_tasks SET status='failed',error_code=?,"
                " error_description=?,updated_at=? WHERE id=?",
                (code, description[:500], time.time(), task_id),
            )

    def set_feedback(self, task_id: int, feedback: str) -> bool:
        with self.connect() as connection:
            cursor = connection.execute(
                "UPDATE bridge_tasks SET feedback=?,updated_at=?"
                " WHERE id=? AND feedback IS NULL",
                (feedback, time.time(), task_id),
            )
            return cursor.rowcount == 1


class PairingManager:
    """Own the short-lived pairing URI without exposing it in process arguments."""

    def __init__(
        self,
        *,
        hub: str,
        state_file: Path,
        api_key: str,
        node_name: str,
    ) -> None:
        self.hub = hub
        self.state_file = state_file
        self.api_key = api_key
        self.node_name = node_name
        self.pairing_uri: str | None = None
        self.last_error: str | None = None
        self._lock = threading.Lock()

    def has_state(self) -> bool:
        return self.state_file.is_file()

    def restart(self) -> str:
        with self._lock:
            try:
                self.pairing_uri = pair_device.start_pairing(
                    self.hub,
                    api_key=self.api_key,
                    state_file=self.state_file,
                    node_name=self.node_name,
                )
            except Exception as exc:
                self.last_error = str(exc)
                raise
            self.last_error = None
            return self.pairing_uri


class LocalBridge:
    def __init__(
        self,
        *,
        state_file: Path,
        database: Path,
        local_api_key: str,
        pairing: PairingManager,
        client_factory: Callable[[Path], RelayClient] = RelayClient,
        setup_token: str | None = None,
        diagnostics: DiagnosticLog | None = None,
    ) -> None:
        self.state_file = state_file
        self.store = BridgeTaskStore(database)
        self.local_api_key = local_api_key
        self.pairing = pairing
        self.client_factory = client_factory
        self._client_lock = threading.Lock()
        self._relay_client: RelayClient | None = None
        self._relay_state_mtime_ns: int | None = None
        self._background: set[asyncio.Task[Any]] = set()
        self.setup_token = setup_token or secrets.token_urlsafe(24)
        self.diagnostics = diagnostics
        self.app = self._make_app()

    def _client(self) -> RelayClient:
        if not self.state_file.is_file():
            raise RelayClientError("手机尚未配对，请先打开本机配对页面")
        state_mtime_ns = self.state_file.stat().st_mtime_ns
        with self._client_lock:
            if self._relay_client is None or self._relay_state_mtime_ns != state_mtime_ns:
                self._relay_client = self.client_factory(self.state_file)
                self._relay_state_mtime_ns = state_mtime_ns
            return self._relay_client

    def _authorized(self, supplied: Any) -> bool:
        return isinstance(supplied, str) and hmac.compare_digest(
            supplied, self.local_api_key
        )

    def submit(self, protocol: str, task: dict[str, Any], external_type: str) -> int:
        task_id = self.store.create(protocol, task, external_type)
        background = asyncio.create_task(self._solve(task_id, task))
        self._background.add(background)
        background.add_done_callback(self._background.discard)
        return task_id

    async def _solve(self, task_id: int, task: dict[str, Any]) -> None:
        self.store.set_processing(task_id)
        try:
            solution = await asyncio.to_thread(self._client().solve, task)
        except Exception as exc:
            if self.diagnostics is not None:
                self.diagnostics.event("LOCAL_BRIDGE", "TASK_FAILED", exc)
            code = "ERROR_CAPTCHA_UNSOLVABLE"
            description = str(exc) or "phone did not complete the task"
            if isinstance(exc, RelayClientError) and "尚未配对" in description:
                code = "ERROR_PHONE_NOT_PAIRED"
            self.store.set_error(task_id, code, description)
            return
        self.store.set_solution(task_id, solution)

    async def connection_status(self) -> dict[str, Any]:
        if not self.state_file.is_file():
            return {"paired": False, "hub": self.pairing.hub, "error": self.pairing.last_error}
        try:
            status = await asyncio.to_thread(self._client().status)
        except Exception as exc:
            if self.diagnostics is not None:
                self.diagnostics.event("LOCAL_BRIDGE", "STATUS_FAILED", exc)
            return {"paired": False, "hub": self.pairing.hub, "error": str(exc)}
        phone = next(
            (device for device in status.get("devices", []) if device.get("role") == "phone"),
            None,
        )
        return {
            "paired": phone is not None,
            "hub": self.pairing.hub,
            "phoneName": phone.get("name") if phone else None,
            "queued": status.get("queued", 0),
        }

    def _track_cors(self, response: Response, params: dict[str, Any]) -> Response:
        if str(params.get("header_acao", "0")).lower() in {"1", "true"}:
            response.headers["Access-Control-Allow-Origin"] = "*"
        return response

    def _v1_response(
        self,
        params: dict[str, Any],
        status: int,
        value: str,
        *,
        price: str | None = None,
        raw: bool = False,
    ) -> Response:
        if str(params.get("json", "0")).lower() in {"1", "true"}:
            payload: dict[str, Any] = {"status": status, "request": value}
            if price is not None:
                payload["price"] = price
            response = jsonify(payload)
        else:
            prefix = "" if raw or not status else "OK|"
            suffix = f"|{price}" if status and price is not None else ""
            response = Response(prefix + value + suffix, content_type="text/plain; charset=utf-8")
        return self._track_cors(response, params)

    async def _v1_params(self) -> dict[str, Any]:
        params = dict(request.args)
        if request.method == "POST":
            form = await request.form
            params.update(dict(form))
        return params

    def _make_app(self) -> Quart:
        app = Quart(__name__)

        def setup_authorized() -> bool:
            supplied = request.headers.get("X-CaptchaMesh-Setup", "")
            return hmac.compare_digest(supplied, self.setup_token)

        @app.after_request
        async def security_headers(response: Response) -> Response:
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["Referrer-Policy"] = "no-referrer"
            response.headers["Cache-Control"] = "no-store"
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; style-src 'unsafe-inline'; script-src 'unsafe-inline';"
                " img-src 'self' blob:; connect-src 'self'; frame-ancestors 'none';"
                " base-uri 'none'; form-action 'none'"
            )
            return response

        @app.get("/healthz")
        async def healthz():
            return jsonify(ok=True, service="captchamesh-local-bridge", protocolVersion=1)

        @app.get("/setup")
        async def setup_page():
            return Response(self._setup_html(), content_type="text/html; charset=utf-8")

        @app.get("/setup/pairing.svg")
        async def pairing_svg():
            if not setup_authorized():
                return Response(status=403)
            if not self.pairing.pairing_uri:
                return Response(status=404)
            image = qrcode.make(
                self.pairing.pairing_uri,
                image_factory=qrcode.image.svg.SvgPathImage,
                box_size=8,
                border=2,
            )
            output = io.BytesIO()
            image.save(output)
            return Response(output.getvalue(), content_type="image/svg+xml")

        @app.get("/setup/status")
        async def setup_status():
            if not setup_authorized():
                return jsonify(error="invalid setup token"), 403
            return jsonify(await self.connection_status())

        @app.post("/setup/pair")
        async def setup_pair():
            if not setup_authorized():
                return jsonify(error="invalid setup token"), 403
            try:
                await asyncio.to_thread(self.pairing.restart)
            except Exception as exc:
                if self.diagnostics is not None:
                    self.diagnostics.event("LOCAL_BRIDGE", "PAIRING_FAILED", exc)
                return jsonify(error=str(exc)), 502
            return jsonify(ok=True)

        @app.route("/in.php", methods=["GET", "POST"])
        async def v1_submit():
            params = await self._v1_params()
            try:
                if not self._authorized(params.get("key")):
                    raise TwoCaptchaCompatError("ERROR_KEY_DOES_NOT_EXIST", "invalid local key")
                translated, external_type = translate_v1_request(params)
                task_id = self.submit("v1", translated, external_type)
            except TwoCaptchaCompatError as exc:
                return self._v1_response(params, 0, exc.code)
            return self._v1_response(params, 1, str(task_id))

        @app.route("/res.php", methods=["GET", "POST"])
        async def v1_result():
            params = await self._v1_params()
            if not self._authorized(params.get("key")):
                return self._v1_response(params, 0, "ERROR_KEY_DOES_NOT_EXIST")
            action = str(params.get("action", "")).lower()
            if action == "getbalance":
                return self._v1_response(params, 1, "999999.00000", raw=True)
            if action in {"add_pingback", "get_pingback", "del_pingback"}:
                return self._v1_response(params, 0, "ERROR_CALLBACK_NOT_SUPPORTED")
            row = self.store.get(params.get("id"))
            if row is None:
                return self._v1_response(params, 0, "ERROR_WRONG_CAPTCHA_ID")
            if action in {"reportgood", "reportbad"}:
                feedback = "correct" if action == "reportgood" else "incorrect"
                if not self.store.set_feedback(int(row["id"]), feedback):
                    return self._v1_response(params, 0, "ERROR_DUPLICATE_REPORT")
                return self._v1_response(params, 1, "OK_REPORT_RECORDED", raw=True)
            if action not in {"get", "get2"}:
                return self._v1_response(params, 0, "ERROR_BAD_PARAMETERS")
            if row["status"] == "ready":
                solution = json.loads(row["solution_json"] or "{}")
                price = "0.00000" if action == "get2" else None
                return self._v1_response(params, 1, legacy_result(solution), price=price)
            if row["status"] == "failed":
                return self._v1_response(
                    params, 0, row["error_code"] or "ERROR_CAPTCHA_UNSOLVABLE"
                )
            return self._v1_response(params, 0, "CAPCHA_NOT_READY")

        @app.post("/createTask")
        async def v2_create():
            body = await request.get_json(force=True, silent=True)
            try:
                if not isinstance(body, dict) or not self._authorized(body.get("clientKey")):
                    raise TwoCaptchaCompatError("ERROR_KEY_DOES_NOT_EXIST", "invalid local key")
                translated, external_type = translate_create_request(body)
                task_id = self.submit("v2", translated, external_type)
            except TwoCaptchaCompatError as exc:
                return jsonify(errorId=1, errorCode=exc.code, errorDescription=exc.description)
            return jsonify(errorId=0, taskId=task_id)

        @app.post("/getTaskResult")
        async def v2_result():
            body = await request.get_json(force=True, silent=True)
            if not isinstance(body, dict) or not self._authorized(body.get("clientKey")):
                return jsonify(
                    errorId=1,
                    errorCode="ERROR_KEY_DOES_NOT_EXIST",
                    errorDescription="invalid local key",
                )
            row = self.store.get(body.get("taskId"))
            if row is None:
                return jsonify(
                    errorId=1,
                    errorCode="ERROR_TASK_NOT_FOUND",
                    errorDescription="task not found",
                )
            if row["status"] == "ready":
                solution = translate_solution(
                    row["external_type"], json.loads(row["solution_json"] or "{}")
                )
                return jsonify(
                    errorId=0,
                    status="ready",
                    solution=solution,
                    cost="0.00000",
                    createTime=int(row["created_at"]),
                    endTime=int(row["updated_at"]),
                    solveCount=1,
                )
            if row["status"] == "failed":
                return jsonify(
                    errorId=1,
                    errorCode=row["error_code"] or "ERROR_CAPTCHA_UNSOLVABLE",
                    errorDescription=row["error_description"] or "task failed",
                )
            return jsonify(errorId=0, status="processing")

        @app.post("/getBalance")
        async def v2_balance():
            body = await request.get_json(force=True, silent=True)
            if not isinstance(body, dict) or not self._authorized(body.get("clientKey")):
                return jsonify(
                    errorId=1,
                    errorCode="ERROR_KEY_DOES_NOT_EXIST",
                    errorDescription="invalid local key",
                )
            return jsonify(errorId=0, balance="999999.00000")

        @app.post("/reportCorrect")
        @app.post("/reportIncorrect")
        async def v2_report():
            body = await request.get_json(force=True, silent=True)
            if not isinstance(body, dict) or not self._authorized(body.get("clientKey")):
                return jsonify(
                    errorId=1,
                    errorCode="ERROR_KEY_DOES_NOT_EXIST",
                    errorDescription="invalid local key",
                )
            row = self.store.get(body.get("taskId"))
            if row is None:
                return jsonify(
                    errorId=1,
                    errorCode="ERROR_TASK_NOT_FOUND",
                    errorDescription="task not found",
                )
            feedback = "correct" if request.path.endswith("reportCorrect") else "incorrect"
            if not self.store.set_feedback(int(row["id"]), feedback):
                return jsonify(
                    errorId=1,
                    errorCode="ERROR_DUPLICATE_REPORT",
                    errorDescription="feedback already recorded",
                )
            return jsonify(errorId=0, status="success")

        return app

    def _setup_html(self) -> str:
        local_key_path = self.state_file.parent / "local-api.key"
        escaped_path = html.escape(str(local_key_path))
        has_qr = "true" if self.pairing.pairing_uri else "false"
        return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>CaptchaMesh 配对</title><style>
:root{{--bg:#0f172a;--fg:#f8fafc;--muted:#94a3b8;--line:#475569;--green:#22c55e;--red:#f87171}}
*{{box-sizing:border-box}} body{{margin:0;background:var(--bg);color:var(--fg);font:16px/1.55 system-ui,sans-serif}}
main{{width:min(720px,calc(100% - 32px));margin:0 auto;padding:48px 0}} h1{{font-size:28px;margin:0 0 8px}}
.lead{{color:var(--muted);margin:0 0 32px}} section{{border-top:1px solid var(--line);padding:24px 0}}
.status{{display:flex;align-items:center;gap:10px;font-weight:650}} .dot{{width:10px;height:10px;border-radius:50%;background:var(--muted)}}
.dot.ok{{background:var(--green)}} .dot.bad{{background:var(--red)}} #qr{{width:min(300px,80vw);background:white;margin:20px 0}}
code{{font-family:ui-monospace,monospace;color:#bbf7d0;overflow-wrap:anywhere}} button{{border:0;border-radius:8px;padding:12px 18px;background:var(--green);color:#052e16;font-weight:700;cursor:pointer;transition:opacity .2s}}
button:hover{{opacity:.9}} button:focus-visible{{outline:3px solid white;outline-offset:3px}} button:disabled{{opacity:.55;cursor:wait}}
.hint,.error{{color:var(--muted)}} .error{{color:#fecaca}} [hidden]{{display:none!important}}
@media (prefers-reduced-motion:reduce){{*{{transition:none!important}}}}
</style></head><body><main><h1>CaptchaMesh</h1><p class="lead">电脑负责加密，手机负责手动完成验证。</p>
<section><div class="status"><span id="dot" class="dot"></span><span id="state">正在检查连接…</span></div><p id="detail" class="hint"></p></section>
<section id="pair"><h2>用手机扫描</h2><p class="hint">二维码 60 秒有效，只在这台电脑和手机之间使用。</p><img id="qr" hidden alt="CaptchaMesh 一次性配对二维码"><br><button id="again" type="button">{'二维码过期了，重新生成' if self.pairing.pairing_uri else '生成配对二维码'}</button><p id="pairError" class="error" role="alert"></p></section>
<section><h2>Agent 接入</h2><p>本机 API：<code id="apiBase"></code></p><p>本机 Key 文件：<code>{escaped_path}</code></p><p class="hint">配对完成后，Agent 可调用 v1 的 /in.php、/res.php，或 v2 的 /createTask、/getTaskResult。</p></section>
</main><script>
const capability=location.hash.slice(1); history.replaceState(null,'',location.pathname);
const prefix='/setup', headers={{'X-CaptchaMesh-Setup':capability}}; let hasQr={has_qr};
document.querySelector('#apiBase').textContent=location.origin;
async function loadQr(force=false){{const qr=document.querySelector('#qr');if(!hasQr||!capability||(!force&&qr.dataset.loaded==='1'))return;const r=await fetch(prefix+'/pairing.svg',{{headers,cache:'no-store'}});if(!r.ok)return;if(qr.src.startsWith('blob:'))URL.revokeObjectURL(qr.src);qr.src=URL.createObjectURL(await r.blob());qr.dataset.loaded='1';qr.hidden=false}}
async function refresh(){{if(!capability){{document.querySelector('#dot').className='dot bad';document.querySelector('#state').textContent='配对链接无效或已被清除';document.querySelector('#detail').textContent='请回到终端重新获取配对链接';return}}try{{const r=await fetch(prefix+'/status',{{headers,cache:'no-store'}}),s=await r.json();if(!r.ok)throw new Error('unauthorized');
const dot=document.querySelector('#dot'), state=document.querySelector('#state'), detail=document.querySelector('#detail'), pair=document.querySelector('#pair');
if(s.paired){{dot.className='dot ok';state.textContent='手机已连接';detail.textContent=s.phoneName||'';pair.hidden=true}}
else{{dot.className='dot';state.textContent='等待手机配对';detail.textContent=s.error||'打开手机相机扫描下面的二维码';pair.hidden=false;await loadQr()}}}}catch(e){{document.querySelector('#dot').className='dot bad';document.querySelector('#state').textContent='本机服务异常';document.querySelector('#detail').textContent='请从终端重新获取配对链接'}}}}
document.querySelector('#again').addEventListener('click',async e=>{{e.currentTarget.disabled=true;document.querySelector('#pairError').textContent='';try{{const r=await fetch(prefix+'/pair',{{method:'POST',headers}}),v=await r.json();if(!r.ok)throw new Error(v.error||'生成失败');hasQr=true;await loadQr(true);e.currentTarget.textContent='二维码过期了，重新生成';await refresh()}}catch(x){{document.querySelector('#pairError').textContent=x.message+'。请检查 Hub 地址或邀请密钥。'}}finally{{e.currentTarget.disabled=false}}}});
refresh();setInterval(refresh,2500);
</script></body></html>"""


def make_local_bridge(**kwargs: Any) -> LocalBridge:
    return LocalBridge(**kwargs)
