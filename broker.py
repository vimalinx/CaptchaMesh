#!/usr/bin/env python3
"""CaptchaMesh broker: a small, authenticated CAPTCHA task exchange.

The broker persists public task metadata and short-lived results in SQLite.
Session material (cookies, headers, local storage, proxy credentials and user
agent overrides) is deliberately kept in process memory only and is erased as
soon as a task reaches a terminal state.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import binascii
import hmac
import hashlib
import ipaddress
import json
import os
import re
import secrets
import signal
import sqlite3
import time
import uuid
from collections import defaultdict, deque
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from quart import Quart, g, jsonify, redirect, request
from werkzeug.exceptions import RequestEntityTooLarge

from challenge_protocol import (
    PRESENTATION_KIND,
    SUPPORTED_TYPES,
    VISUAL_TYPES,
    SolutionError,
    legacy_result,
    normalize_solution,
)
from twocaptcha_compat import (
    TwoCaptchaCompatError,
    translate_create_request,
    translate_solution,
    translate_v1_request,
)
from relay_protocol import (
    MAX_TTL_SECONDS as RELAY_MAX_TTL_SECONDS,
    NONCE_BYTES as RELAY_NONCE_BYTES,
    PROTOCOL_VERSION as RELAY_PROTOCOL_VERSION,
    RelayProtocolError,
    b64url_decode,
)

DB_PATH = Path(
    os.environ.get("CAPTCHAMESH_DB_PATH", str(Path(__file__).parent / "broker.db"))
).expanduser()
REGISTRY_PATH = Path(
    os.environ.get("CAPTCHAMESH_REGISTRY_PATH", str(Path(__file__).parent / "registrations.json"))
).expanduser()
PROTOCOL_VERSION = "3"
TASK_LEASE_SECONDS = 240
TASK_TTL_SECONDS = 600
RESULT_TTL_SECONDS = 600
MAX_ATTEMPTS = 3
MAX_CONTEXT_BYTES = 128 * 1024
MAX_ASSET_BYTES = 5 * 1024 * 1024
NODE_ONLINE_SECONDS = 75
NODE_COMMAND_LEASE_SECONDS = 75
NODE_COMMAND_MAX_ATTEMPTS = 3
PAIRING_TTL_SECONDS = 60
MAX_RELAY_CIPHERTEXT_BYTES = 8 * 1024 * 1024
MAX_RELAY_MESSAGES_PER_MAILBOX = 32
PUBLIC_PAIRING_PER_MINUTE = 12
MAX_PENDING_PUBLIC_PAIRINGS = 24
MAX_REQUEST_BODY_BYTES = 12 * 1024 * 1024
MAX_CONCURRENT_REQUESTS = 128
REQUESTS_PER_MINUTE_PER_CLIENT = 240
REQUESTS_PER_MINUTE_GLOBAL = 1_200
MAX_RATE_LIMIT_CLIENTS = 4_096
REGISTRATION_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
ASSET_MEDIA_TYPES = {"image/png", "image/jpeg", "image/webp", "image/gif"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
  id TEXT PRIMARY KEY,
  url TEXT NOT NULL,
  sitekey TEXT NOT NULL,
  type TEXT NOT NULL DEFAULT 'turnstile',
  run_id TEXT,
  registration_id TEXT,
  task_json TEXT NOT NULL DEFAULT '{}',
  context_required INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'pending',
  result TEXT,
  error_code TEXT,
  error_description TEXT,
  worker TEXT,
  leased_at REAL,
  attempts INTEGER NOT NULL DEFAULT 0,
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tasks_status_created ON tasks(status, created_at);
CREATE TABLE IF NOT EXISTS twocaptcha_tasks (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  internal_task_id TEXT NOT NULL UNIQUE,
  external_type TEXT NOT NULL,
  feedback TEXT,
  feedback_at REAL,
  created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS workers (
  name TEXT PRIMARY KEY,
  token TEXT NOT NULL,
  domains TEXT NOT NULL,
  types TEXT NOT NULL DEFAULT '[]',
  app_version TEXT,
  device TEXT,
  solved INTEGER NOT NULL DEFAULT 0,
  failed INTEGER NOT NULL DEFAULT 0,
  last_seen REAL
);
CREATE TABLE IF NOT EXISTS runs (
  id TEXT PRIMARY KEY,
  registration_id TEXT NOT NULL,
  registration_name TEXT NOT NULL,
  node_id TEXT,
  status TEXT NOT NULL,
  pid INTEGER,
  exit_code INTEGER,
  started_at REAL NOT NULL,
  updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_runs_registration_started
  ON runs(registration_id, started_at DESC);
CREATE TABLE IF NOT EXISTS nodes (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  token TEXT NOT NULL,
  version TEXT,
  device TEXT,
  last_seen REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS remote_registrations (
  public_id TEXT PRIMARY KEY,
  node_id TEXT NOT NULL,
  local_id TEXT NOT NULL,
  name TEXT NOT NULL,
  summary TEXT NOT NULL,
  provides TEXT NOT NULL,
  details TEXT NOT NULL DEFAULT '[]',
  description TEXT NOT NULL DEFAULT '',
  captcha_types TEXT NOT NULL DEFAULT '[]',
  enabled INTEGER NOT NULL DEFAULT 1,
  updated_at REAL NOT NULL,
  UNIQUE(node_id, local_id)
);
CREATE INDEX IF NOT EXISTS idx_remote_registrations_node
  ON remote_registrations(node_id, local_id);
CREATE TABLE IF NOT EXISTS node_commands (
  id TEXT PRIMARY KEY,
  node_id TEXT NOT NULL,
  run_id TEXT NOT NULL,
  action TEXT NOT NULL,
  local_registration_id TEXT,
  status TEXT NOT NULL DEFAULT 'pending',
  attempts INTEGER NOT NULL DEFAULT 0,
  leased_at REAL,
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_node_commands_poll
  ON node_commands(node_id, status, created_at);
CREATE TABLE IF NOT EXISTS relay_mailboxes (
  id TEXT PRIMARY KEY,
  status TEXT NOT NULL DEFAULT 'active',
  created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS relay_devices (
  id TEXT PRIMARY KEY,
  mailbox_id TEXT NOT NULL,
  role TEXT NOT NULL,
  name TEXT NOT NULL,
  token_hash TEXT NOT NULL UNIQUE,
  created_at REAL NOT NULL,
  last_seen REAL NOT NULL,
  revoked_at REAL
);
CREATE INDEX IF NOT EXISTS idx_relay_devices_mailbox
  ON relay_devices(mailbox_id, role);
CREATE TABLE IF NOT EXISTS relay_pairings (
  token_hash TEXT PRIMARY KEY,
  mailbox_id TEXT NOT NULL,
  node_device_id TEXT NOT NULL,
  expires_at REAL NOT NULL,
  used_at REAL
);
CREATE TABLE IF NOT EXISTS relay_messages (
  message_id TEXT PRIMARY KEY,
  mailbox_id TEXT NOT NULL,
  direction TEXT NOT NULL,
  expires_at REAL NOT NULL,
  nonce TEXT NOT NULL,
  ciphertext TEXT NOT NULL,
  created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_relay_messages_recipient
  ON relay_messages(mailbox_id, direction, created_at);
"""


class RequestError(ValueError):
    def __init__(self, code: str, description: str, http_status: int = 400):
        super().__init__(description)
        self.code = code
        self.description = description
        self.http_status = http_status


def now() -> float:
    return time.time()


def _token_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


@contextmanager
def db() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def _ensure_column(conn: sqlite3.Connection, table: str, declaration: str) -> None:
    name = declaration.split()[0]
    columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    if name not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {declaration}")


def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with db() as conn:
        conn.executescript(SCHEMA)
        # Migrate the pre-v1 prototype database in place without retaining a
        # second runtime schema.
        for declaration in (
            "type TEXT NOT NULL DEFAULT 'turnstile'",
            "run_id TEXT",
            "registration_id TEXT",
            "task_json TEXT NOT NULL DEFAULT '{}'",
            "context_required INTEGER NOT NULL DEFAULT 0",
            "error_code TEXT",
            "error_description TEXT",
            "attempts INTEGER NOT NULL DEFAULT 0",
        ):
            _ensure_column(conn, "tasks", declaration)
        for declaration in (
            "types TEXT NOT NULL DEFAULT '[]'",
            "app_version TEXT",
            "device TEXT",
        ):
            _ensure_column(conn, "workers", declaration)
        _ensure_column(conn, "runs", "node_id TEXT")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_tasks_run_status"
            " ON tasks(run_id, status, created_at)"
        )
    DB_PATH.chmod(0o600)


def load_registrations(path: Path | None = None) -> dict[str, dict[str, Any]]:
    registry_path = REGISTRY_PATH if path is None else path
    try:
        payload = json.loads(registry_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"cannot load registration registry: {exc}") from exc
    if not isinstance(payload, list):
        raise TypeError("registrations.json must contain an array")
    registrations: dict[str, dict[str, Any]] = {}
    for item in payload:
        if not isinstance(item, dict):
            raise TypeError("each registration entry must be an object")
        try:
            offer = _validate_registration_offer(item)
        except RequestError as exc:
            raise RuntimeError(f"invalid registration entry: {exc.description}") from exc
        registration_id = offer["id"]
        cwd = Path(_limited_text(item.get("cwd"), "cwd", 4096, required=True)).expanduser().resolve()
        command = item.get("command")
        if not isinstance(command, list) or not command or len(command) > 20:
            raise RuntimeError(f"registration {registration_id}: command must be a non-empty array")
        clean_command = [
            _limited_text(part, "command part", 4096, required=True, strip=False)
            for part in command
        ]
        registrations[registration_id] = {
            **offer,
            "cwd": cwd,
            "command": clean_command,
        }
    return registrations


def _json_size(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode())


def _limited_text(
    value: Any,
    name: str,
    limit: int,
    required: bool = False,
    strip: bool = True,
) -> str:
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise RequestError("ERROR_BAD_TASK", f"{name} must be a string")
    if strip:
        value = value.strip()
    if required and not value:
        raise RequestError("ERROR_BAD_TASK", f"{name} is required")
    if len(value) > limit:
        raise RequestError("ERROR_BAD_TASK", f"{name} is too long")
    return value


def _validate_registration_id(value: Any, name: str = "registration id") -> str:
    clean = _limited_text(value, name, 64, required=True).lower()
    if not REGISTRATION_ID_RE.fullmatch(clean):
        raise RequestError(
            "ERROR_BAD_REGISTRATION",
            f"{name} must use lowercase letters, digits, dot, underscore or dash",
        )
    return clean


def _validate_details(value: Any) -> list[dict[str, str]]:
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > 16:
        raise RequestError(
            "ERROR_BAD_REGISTRATION", "details must be an array with at most 16 rows"
        )
    details: list[dict[str, str]] = []
    for row in value:
        if not isinstance(row, dict):
            raise RequestError("ERROR_BAD_REGISTRATION", "each detail must be an object")
        details.append(
            {
                "label": _limited_text(
                    row.get("label"), "detail label", 40, required=True
                ),
                "value": _limited_text(
                    row.get("value"), "detail value", 600, required=True
                ),
            }
        )
    return details


def _validate_registration_offer(item: Any) -> dict[str, Any]:
    if not isinstance(item, dict):
        raise RequestError("ERROR_BAD_REGISTRATION", "registration must be an object")
    registration_id = _validate_registration_id(item.get("id"))
    provides = item.get("provides", [])
    if not isinstance(provides, list) or not provides or len(provides) > 16:
        raise RequestError(
            "ERROR_BAD_REGISTRATION", "provides must contain between 1 and 16 items"
        )
    captcha_types = item.get("captchaTypes", [])
    if not isinstance(captcha_types, list) or len(captcha_types) > len(SUPPORTED_TYPES):
        raise RequestError("ERROR_BAD_REGISTRATION", "captchaTypes must be an array")
    clean_captcha_types = [
        _limited_text(value, "CAPTCHA type", 64, required=True) for value in captcha_types
    ]
    if any(value not in SUPPORTED_TYPES for value in clean_captcha_types):
        raise RequestError("ERROR_BAD_REGISTRATION", "unsupported CAPTCHA type advertised")
    return {
        "id": registration_id,
        "name": _limited_text(item.get("name"), "registration name", 128, required=True),
        "summary": _limited_text(
            item.get("summary"), "registration summary", 240, required=True
        ),
        "provides": [
            _limited_text(value, "provides item", 100, required=True) for value in provides
        ],
        "details": _validate_details(item.get("details", [])),
        "description": _limited_text(item.get("description"), "description", 600),
        "captchaTypes": clean_captcha_types,
        "enabled": bool(item.get("enabled", True)),
    }


def _validate_url(value: Any) -> str:
    url = _limited_text(value, "websiteURL", 4096, required=True)
    parsed = urlparse(url)
    try:
        port = parsed.port
    except ValueError as exc:
        raise RequestError("ERROR_BAD_TASK", "websiteURL contains an invalid port") from exc
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or any(ord(char) < 0x20 for char in url)
        or (port is not None and not 1 <= port <= 65535)
    ):
        raise RequestError("ERROR_BAD_TASK", "websiteURL must be an http(s) URL")
    return url


def _validate_provider_url(value: Any, name: str, suffixes: tuple[str, ...]) -> str:
    url = _validate_url(value)
    host = (urlparse(url).hostname or "").lower()
    if not any(host == suffix or host.endswith("." + suffix) for suffix in suffixes):
        raise RequestError("ERROR_BAD_TASK", f"{name} uses an untrusted host")
    return url


def _decode_asset(value: Any, name: str) -> tuple[bytes, str]:
    if not isinstance(value, dict):
        raise RequestError("ERROR_BAD_TASK", f"presentation.{name} must be an object")
    encoded = _limited_text(value.get("data"), f"presentation.{name}.data", 8_000_000, required=True)
    media_type = _limited_text(
        value.get("mediaType", "image/png"),
        f"presentation.{name}.mediaType",
        64,
        required=True,
    ).lower()
    if encoded.startswith("data:"):
        header, separator, encoded = encoded.partition(",")
        if not separator or ";base64" not in header:
            raise RequestError("ERROR_BAD_TASK", f"presentation.{name} data URI is invalid")
        media_type = header[5:].split(";", 1)[0].lower()
    if media_type not in ASSET_MEDIA_TYPES:
        raise RequestError("ERROR_BAD_TASK", f"unsupported image media type: {media_type}")
    try:
        decoded = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise RequestError("ERROR_BAD_TASK", f"presentation.{name}.data is not valid base64") from exc
    if not 1 <= len(decoded) <= MAX_ASSET_BYTES:
        raise RequestError("ERROR_BAD_TASK", f"presentation.{name} must be 1 byte to 5 MiB")
    return decoded, media_type


def _validate_visual_presentation(
    value: Any,
    task_type: str,
) -> tuple[dict[str, Any], dict[str, tuple[bytes, str]]]:
    if not isinstance(value, dict):
        raise RequestError("ERROR_BAD_TASK", "presentation must be an object")
    kind = _limited_text(value.get("kind", task_type), "presentation.kind", 64, required=True)
    if kind != task_type:
        raise RequestError("ERROR_BAD_TASK", "presentation.kind must match task type")

    presentation: dict[str, Any] = {
        "kind": kind,
        "prompt": _limited_text(value.get("prompt"), "presentation.prompt", 1_000),
    }
    assets: dict[str, tuple[bytes, str]] = {}
    for name in ("image", "instructionImage"):
        if value.get(name) is None:
            if name == "image":
                raise RequestError("ERROR_BAD_TASK", "presentation.image is required")
            continue
        decoded, media_type = _decode_asset(value[name], name)
        asset_id = "a-" + secrets.token_urlsafe(24)
        assets[asset_id] = (decoded, media_type)
        presentation[name] = {
            "assetId": asset_id,
            "mediaType": media_type,
            "byteLength": len(decoded),
        }

    if task_type == "image_text":
        minimum = value.get("minLength", 1)
        maximum = value.get("maxLength", 1_024)
        if any(isinstance(item, bool) or not isinstance(item, int) for item in (minimum, maximum)):
            raise RequestError("ERROR_BAD_TASK", "minLength and maxLength must be integers")
        if not 1 <= minimum <= maximum <= 1_024:
            raise RequestError("ERROR_BAD_TASK", "image text length bounds are invalid")
        numeric_mode = value.get("numericMode", 0)
        if isinstance(numeric_mode, bool) or not isinstance(numeric_mode, int) or numeric_mode not in range(5):
            raise RequestError("ERROR_BAD_TASK", "numericMode must be an integer from 0 through 4")
        presentation.update(
            minLength=minimum,
            maxLength=maximum,
            caseSensitive=bool(value.get("caseSensitive", False)),
            phrase=bool(value.get("phrase", False)),
            numericMode=numeric_mode,
            math=bool(value.get("math", False)),
        )
    elif task_type == "coordinates":
        minimum, maximum = value.get("minClicks", 1), value.get("maxClicks", 100)
        if any(isinstance(item, bool) or not isinstance(item, int) for item in (minimum, maximum)):
            raise RequestError("ERROR_BAD_TASK", "coordinate click bounds must be integers")
        if not 1 <= minimum <= maximum <= 100:
            raise RequestError("ERROR_BAD_TASK", "coordinate click bounds are invalid")
        presentation.update(
            multiple=bool(value.get("multiple", maximum > 1)),
            minClicks=minimum,
            maxClicks=maximum,
        )
    elif task_type == "grid":
        rows, columns = value.get("rows"), value.get("columns")
        if any(isinstance(item, bool) or not isinstance(item, int) for item in (rows, columns)):
            raise RequestError("ERROR_BAD_TASK", "grid rows and columns must be integers")
        if not 1 <= rows <= 12 or not 1 <= columns <= 12 or rows * columns > 100:
            raise RequestError("ERROR_BAD_TASK", "grid dimensions must contain 1 to 100 cells")
        minimum = value.get("minClicks", 1)
        maximum = value.get("maxClicks")
        if maximum is None:
            maximum = rows * columns
        if any(isinstance(item, bool) or not isinstance(item, int) for item in (minimum, maximum)):
            raise RequestError("ERROR_BAD_TASK", "grid click bounds must be integers")
        if not 1 <= minimum <= maximum <= rows * columns:
            raise RequestError("ERROR_BAD_TASK", "grid click bounds are invalid")
        presentation.update(
            rows=rows,
            columns=columns,
            multiple=bool(value.get("multiple", maximum > 1)),
            minClicks=minimum,
            maxClicks=maximum,
        )
    elif task_type == "rotate":
        step = value.get("angleStep", 1)
        if isinstance(step, bool) or not isinstance(step, (int, float)) or not 0 < step <= 180:
            raise RequestError("ERROR_BAD_TASK", "angleStep must be between 0 and 180")
        presentation["angleStep"] = step
    return presentation, assets


def _validate_context(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise RequestError("ERROR_BAD_CONTEXT", "context must be an object")
    allowed = {"headers", "cookies", "localStorage", "proxy", "userAgent"}
    unknown = set(value) - allowed
    if unknown:
        raise RequestError("ERROR_BAD_CONTEXT", f"unknown context fields: {sorted(unknown)}")

    context: dict[str, Any] = {}
    headers = value.get("headers", {})
    if not isinstance(headers, dict) or len(headers) > 50:
        raise RequestError("ERROR_BAD_CONTEXT", "headers must be an object with at most 50 entries")
    clean_headers: dict[str, str] = {}
    for key, header_value in headers.items():
        key = _limited_text(key, "header name", 128, required=True)
        header_value = _limited_text(header_value, f"header {key}", 8192, strip=False)
        if any(char in key + header_value for char in "\r\n"):
            raise RequestError("ERROR_BAD_CONTEXT", "headers cannot contain newlines")
        clean_headers[key] = header_value
    if clean_headers:
        context["headers"] = clean_headers

    cookies = value.get("cookies", [])
    if not isinstance(cookies, list) or len(cookies) > 100:
        raise RequestError("ERROR_BAD_CONTEXT", "cookies must be an array with at most 100 entries")
    clean_cookies: list[dict[str, Any]] = []
    for cookie in cookies:
        if not isinstance(cookie, dict):
            raise RequestError("ERROR_BAD_CONTEXT", "each cookie must be an object")
        clean = {
            "name": _limited_text(cookie.get("name"), "cookie name", 256, required=True),
            "value": _limited_text(cookie.get("value"), "cookie value", 8192, strip=False),
        }
        for field, limit in (("domain", 255), ("path", 1024), ("sameSite", 32)):
            if cookie.get(field) is not None:
                clean[field] = _limited_text(cookie[field], f"cookie {field}", limit)
        for field in ("secure", "httpOnly"):
            if cookie.get(field) is not None:
                clean[field] = bool(cookie[field])
        clean_cookies.append(clean)
    if clean_cookies:
        context["cookies"] = clean_cookies

    local_storage = value.get("localStorage", {})
    if not isinstance(local_storage, dict) or len(local_storage) > 100:
        raise RequestError(
            "ERROR_BAD_CONTEXT", "localStorage must be an object with at most 100 entries"
        )
    clean_storage = {
        _limited_text(key, "localStorage key", 512, required=True): _limited_text(
            item, "localStorage value", 16384, strip=False
        )
        for key, item in local_storage.items()
    }
    if clean_storage:
        context["localStorage"] = clean_storage

    proxy = value.get("proxy")
    if proxy:
        context["proxy"] = _limited_text(proxy, "proxy", 2048, required=True)
    user_agent = value.get("userAgent")
    if user_agent:
        context["userAgent"] = _limited_text(user_agent, "userAgent", 1024, required=True)

    if _json_size(context) > MAX_CONTEXT_BYTES:
        raise RequestError("ERROR_BAD_CONTEXT", "context exceeds 128 KiB")
    return context


def validate_task(
    body: Any,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, tuple[bytes, str]]]:
    if not isinstance(body, dict):
        raise RequestError("ERROR_BAD_TASK", "request body must be an object")
    task_type = _limited_text(body.get("type"), "type", 64, required=True)
    if task_type not in SUPPORTED_TYPES:
        raise RequestError("ERROR_UNSUPPORTED_TYPE", f"unsupported CAPTCHA type: {task_type}")

    if task_type in VISUAL_TYPES and not body.get("websiteURL"):
        website_url = f"https://manual.captchamesh.invalid/{task_type}"
    else:
        website_url = _validate_url(body.get("websiteURL"))
    website_key = _limited_text(body.get("websiteKey"), "websiteKey", 4096)
    if task_type in {"turnstile", "hcaptcha", "recaptcha_v2", "recaptcha_v3", "funcaptcha", "amazon_waf"} and not website_key:
        raise RequestError("ERROR_BAD_TASK", "websiteKey is required for this CAPTCHA type")
    default_mode = "auto" if task_type == "recaptcha_v3" else "interactive"
    mode = _limited_text(body.get("mode", default_mode), "mode", 32, required=True)
    if mode not in {"auto", "interactive"}:
        raise RequestError("ERROR_BAD_TASK", "mode must be auto or interactive")
    timeout_seconds = body.get("timeoutSeconds", 120)
    if not isinstance(timeout_seconds, int) or not 30 <= timeout_seconds <= 180:
        raise RequestError("ERROR_BAD_TASK", "timeoutSeconds must be between 30 and 180")

    public_task: dict[str, Any] = {
        "type": task_type,
        "websiteURL": website_url,
        "websiteKey": website_key,
        "mode": mode,
        "timeoutSeconds": timeout_seconds,
        "isInvisible": bool(body.get("isInvisible", mode == "auto")),
        "presentation": {"kind": PRESENTATION_KIND[task_type]},
    }
    assets: dict[str, tuple[bytes, str]] = {}
    if task_type in VISUAL_TYPES:
        public_task["presentation"], assets = _validate_visual_presentation(
            body.get("presentation"), task_type
        )
    for field, limit in (
        ("action", 256),
        ("cData", 4096),
        ("chlPageData", 32768),
        ("rqdata", 32768),
        ("responseSelector", 2048),
        ("responseProperty", 64),
        ("funcaptchaApiJSSubdomain", 255),
        ("gt", 4_096),
        ("challenge", 4_096),
        ("geetestApiServerSubdomain", 255),
        ("captchaId", 4_096),
        ("riskType", 4_096),
        ("captchaUrl", 8_192),
        ("iv", 8_192),
        ("awsContext", 32_768),
        ("challengeScript", 8_192),
        ("captchaScript", 8_192),
        ("jsapiScript", 8_192),
    ):
        if body.get(field) is not None:
            public_task[field] = _limited_text(body[field], field, limit)
    if task_type == "recaptcha_v3" and not public_task.get("action"):
        raise RequestError("ERROR_BAD_TASK", "action is required for recaptcha_v3")
    if task_type == "webview" and not public_task.get("responseSelector"):
        raise RequestError("ERROR_BAD_TASK", "responseSelector is required for webview tasks")

    if task_type == "funcaptcha":
        subdomain = public_task.get("funcaptchaApiJSSubdomain")
        if subdomain:
            host = subdomain.lower().removeprefix("https://").rstrip("/")
            if not any(host == suffix or host.endswith("." + suffix) for suffix in ("arkoselabs.com", "funcaptcha.com")):
                raise RequestError("ERROR_BAD_TASK", "funcaptchaApiJSSubdomain is untrusted")
        data = body.get("data")
        if data is not None:
            if not isinstance(data, (dict, str)):
                raise RequestError("ERROR_BAD_TASK", "data must be a string or object")
            if _json_size(data) > 32_768:
                raise RequestError("ERROR_BAD_TASK", "data exceeds 32 KiB")
            public_task["data"] = data
    elif task_type == "geetest_v3":
        if not public_task.get("gt") or not public_task.get("challenge"):
            raise RequestError("ERROR_BAD_TASK", "gt and challenge are required for GeeTest v3")
        api_host = public_task.get("geetestApiServerSubdomain")
        if api_host and not (
            api_host.lower() == "geetest.com" or api_host.lower().endswith(".geetest.com")
        ):
            raise RequestError("ERROR_BAD_TASK", "geetestApiServerSubdomain is untrusted")
    elif task_type == "geetest_v4":
        if not public_task.get("captchaId"):
            raise RequestError("ERROR_BAD_TASK", "captchaId is required for GeeTest v4")
    elif task_type == "datadome":
        public_task["captchaUrl"] = _validate_provider_url(
            body.get("captchaUrl"), "captchaUrl", ("captcha-delivery.com",)
        )
    elif task_type == "amazon_waf":
        jsapi_mode = bool(public_task.get("jsapiScript"))
        interstitial_mode = all(
            public_task.get(field)
            for field in ("iv", "awsContext", "challengeScript", "captchaScript")
        )
        if not jsapi_mode and not interstitial_mode:
            raise RequestError(
                "ERROR_BAD_TASK",
                "Amazon WAF requires jsapiScript or iv, awsContext, challengeScript and captchaScript",
            )
        if jsapi_mode and any(public_task.get(field) for field in ("iv", "awsContext", "challengeScript", "captchaScript")):
            raise RequestError("ERROR_BAD_TASK", "Amazon WAF modes cannot be mixed")
        for field in ("challengeScript", "captchaScript", "jsapiScript"):
            if public_task.get(field):
                public_task[field] = _validate_provider_url(
                    public_task[field], field, ("awswaf.com", "amazonaws.com")
                )

    context = _validate_context(body.get("context"))
    if task_type == "datadome" and not all(context.get(field) for field in ("proxy", "userAgent")):
        raise RequestError("ERROR_BAD_CONTEXT", "DataDome requires proxy and userAgent")
    return public_task, context, assets


def _error(code: str, description: str, http_status: int = 400):
    return jsonify(errorId=1, errorCode=code, errorDescription=description), http_status


def _bearer(header: str, scheme: str) -> str:
    prefix = scheme + " "
    return header[len(prefix) :] if header.startswith(prefix) else ""


def _domain_allowed(url: str, domains: list[str]) -> bool:
    if not domains:
        return True
    host = (urlparse(url).hostname or "").lower()
    return any(host == domain or host.endswith("." + domain) for domain in domains)


def make_app(
    api_key: str | None = None,
    node_key: str | None = None,
    *,
    allow_public_pairing: bool = False,
    allowed_hosts: set[str] | None = None,
    max_concurrent_requests: int = MAX_CONCURRENT_REQUESTS,
    requests_per_minute_per_client: int = REQUESTS_PER_MINUTE_PER_CLIENT,
    requests_per_minute_global: int = REQUESTS_PER_MINUTE_GLOBAL,
) -> Quart:
    if api_key and node_key and hmac.compare_digest(api_key, node_key):
        raise ValueError("API key and node enrollment key must be different")
    app = Quart(__name__)
    app.config["MAX_CONTENT_LENGTH"] = MAX_REQUEST_BODY_BYTES
    configured_hosts = allowed_hosts
    if configured_hosts is None:
        configured_hosts = {
            value.strip().lower()
            for value in os.environ.get(
                "CAPTCHAMESH_ALLOWED_HOSTS", "localhost,127.0.0.1,::1"
            ).split(",")
            if value.strip()
        }
    else:
        configured_hosts = {value.strip().lower() for value in configured_hosts if value.strip()}
    if not configured_hosts:
        raise ValueError("at least one allowed Host is required")
    if max_concurrent_requests < 1:
        raise ValueError("max_concurrent_requests must be positive")
    request_slots = asyncio.Semaphore(max_concurrent_requests)
    client_requests: defaultdict[str, deque[float]] = defaultdict(deque)
    global_requests: deque[float] = deque()
    init_db()
    registrations = load_registrations()
    processes: dict[str, asyncio.subprocess.Process] = {}
    ephemeral_contexts: dict[str, dict[str, Any]] = {}
    ephemeral_assets: dict[str, dict[str, Any]] = {}
    public_pairing_attempts: deque[float] = deque()

    def request_host() -> str:
        raw = request.host.strip()
        if not raw or "@" in raw or any(ord(char) < 0x20 for char in raw):
            raise RequestError("ERROR_BAD_HOST", "invalid Host header")
        try:
            parsed = urlparse("//" + raw)
            host = (parsed.hostname or "").lower()
            if parsed.username or parsed.password or not host:
                raise ValueError
            if parsed.port is not None and not 1 <= parsed.port <= 65535:
                raise ValueError
        except ValueError as exc:
            raise RequestError("ERROR_BAD_HOST", "invalid Host header") from exc
        return host

    def client_address() -> str:
        remote = request.remote_addr or "unknown"
        try:
            remote_ip = ipaddress.ip_address(remote)
        except ValueError:
            return "unknown"
        if remote_ip.is_loopback:
            forwarded = request.headers.get("CF-Connecting-IP", "").strip()
            if forwarded:
                try:
                    return str(ipaddress.ip_address(forwarded))
                except ValueError:
                    raise RequestError("ERROR_BAD_PROXY_HEADER", "invalid proxy address")
        return str(remote_ip)

    def consume_rate_limit(bucket: deque[float], limit: int, timestamp: float) -> None:
        while bucket and bucket[0] <= timestamp - 60:
            bucket.popleft()
        if len(bucket) >= limit:
            raise RequestError("ERROR_RATE_LIMIT", "request rate limit exceeded", 429)
        bucket.append(timestamp)

    @app.before_request
    async def security_gate():
        host = request_host()
        if host not in configured_hosts:
            raise RequestError("ERROR_BAD_HOST", "Host is not allowed")
        remote = request.remote_addr or "unknown"
        try:
            remote_ip = ipaddress.ip_address(remote)
        except ValueError:
            remote_ip = None
        visitor = request.headers.get("CF-Visitor", "").strip()
        if visitor and remote_ip is not None and remote_ip.is_loopback:
            try:
                scheme = json.loads(visitor).get("scheme")
            except (AttributeError, json.JSONDecodeError) as exc:
                raise RequestError(
                    "ERROR_BAD_PROXY_HEADER", "invalid Cloudflare visitor header"
                ) from exc
            if scheme == "http":
                target = f"https://{host}{request.path}"
                if request.query_string:
                    target += "?" + request.query_string.decode("ascii", errors="strict")
                return redirect(target, code=308)
        g.captchamesh_request_slot = False
        try:
            await asyncio.wait_for(request_slots.acquire(), timeout=0.05)
        except TimeoutError as exc:
            raise RequestError("ERROR_BUSY", "too many concurrent requests", 503) from exc
        g.captchamesh_request_slot = True
        timestamp = now()
        consume_rate_limit(global_requests, requests_per_minute_global, timestamp)
        address = client_address()
        if address not in client_requests and len(client_requests) >= MAX_RATE_LIMIT_CLIENTS:
            stale = [key for key, values in client_requests.items() if not values or values[-1] <= timestamp - 60]
            for key in stale:
                client_requests.pop(key, None)
            if len(client_requests) >= MAX_RATE_LIMIT_CLIENTS:
                raise RequestError("ERROR_RATE_LIMIT_CAPACITY", "rate limiter is at capacity", 503)
        consume_rate_limit(client_requests[address], requests_per_minute_per_client, timestamp)

    @app.teardown_request
    async def release_request_slot(_error: BaseException | None) -> None:
        if getattr(g, "captchamesh_request_slot", False):
            g.captchamesh_request_slot = False
            request_slots.release()

    @app.after_request
    async def security_headers(response):
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        response.headers["Strict-Transport-Security"] = "max-age=31536000"
        return response

    def discard_ephemeral(task_id: str) -> None:
        ephemeral_contexts.pop(task_id, None)
        for asset_id in [
            key for key, value in ephemeral_assets.items() if value["taskId"] == task_id
        ]:
            ephemeral_assets.pop(asset_id, None)

    # Any task that depended on process-memory context cannot be resumed after
    # a broker restart. Fail it explicitly instead of silently solving in the
    # wrong authentication/proxy context.
    with db() as conn:
        conn.execute(
            "UPDATE tasks SET status='failed', error_code='ERROR_CONTEXT_LOST',"
            " error_description='broker restarted before sensitive context was delivered',"
            " updated_at=? WHERE context_required=1 AND status IN ('pending','leased')",
            (now(),),
        )
        conn.execute(
            "UPDATE runs SET status='interrupted',updated_at=?"
            " WHERE status IN ('starting','running','captcha')",
            (now(),),
        )

    def require_api_key() -> None:
        if api_key is None:
            return
        supplied = _bearer(request.headers.get("Authorization", ""), "Bearer")
        if not supplied or not hmac.compare_digest(supplied, api_key):
            raise RequestError("ERROR_UNAUTHORIZED", "invalid API key", 401)

    def require_pairing_start_access() -> bool:
        """Return True when an unauthenticated, rate-limited public slot is used."""
        if api_key is None:
            return False
        authorization = request.headers.get("Authorization", "")
        supplied = _bearer(authorization, "Bearer")
        if supplied and hmac.compare_digest(supplied, api_key):
            return False
        if authorization or not allow_public_pairing:
            raise RequestError("ERROR_UNAUTHORIZED", "invalid API key", 401)
        timestamp = now()
        while public_pairing_attempts and public_pairing_attempts[0] <= timestamp - 60:
            public_pairing_attempts.popleft()
        if len(public_pairing_attempts) >= PUBLIC_PAIRING_PER_MINUTE:
            raise RequestError(
                "ERROR_PAIRING_RATE_LIMIT", "too many public pairing attempts", 429
            )
        public_pairing_attempts.append(timestamp)
        return True

    def require_twocaptcha_key(body: Any) -> None:
        if not isinstance(body, dict):
            raise RequestError("ERROR_BAD_PARAMETERS", "request body must be an object")
        supplied = _limited_text(body.get("clientKey"), "clientKey", 4096, required=True)
        if api_key is not None and not hmac.compare_digest(supplied, api_key):
            raise RequestError("ERROR_KEY_DOES_NOT_EXIST", "invalid clientKey")

    def require_worker(conn: sqlite3.Connection) -> sqlite3.Row:
        token = _bearer(request.headers.get("Authorization", ""), "Worker")
        if not token:
            raise RequestError("ERROR_UNAUTHORIZED", "missing worker token", 401)
        row = conn.execute("SELECT * FROM workers WHERE token=?", (token,)).fetchone()
        if not row:
            raise RequestError("ERROR_UNAUTHORIZED", "invalid worker token", 401)
        return row

    def require_node_enrollment() -> None:
        if node_key is None:
            raise RequestError(
                "ERROR_NODE_ENROLLMENT_DISABLED", "node enrollment is not configured", 503
            )
        supplied = _bearer(request.headers.get("Authorization", ""), "NodeKey")
        if not supplied or not hmac.compare_digest(supplied, node_key):
            raise RequestError("ERROR_UNAUTHORIZED", "invalid node enrollment key", 401)

    def require_node(conn: sqlite3.Connection) -> sqlite3.Row:
        token = _bearer(request.headers.get("Authorization", ""), "Node")
        if not token:
            raise RequestError("ERROR_UNAUTHORIZED", "missing node token", 401)
        row = conn.execute("SELECT * FROM nodes WHERE token=?", (token,)).fetchone()
        if not row:
            raise RequestError("ERROR_UNAUTHORIZED", "invalid node token", 401)
        return row

    def require_relay_device(conn: sqlite3.Connection) -> sqlite3.Row:
        token = _bearer(request.headers.get("Authorization", ""), "Device")
        if not token:
            raise RequestError("ERROR_UNAUTHORIZED", "missing device token", 401)
        row = conn.execute(
            "SELECT * FROM relay_devices WHERE token_hash=? AND revoked_at IS NULL",
            (_token_hash(token),),
        ).fetchone()
        if not row:
            raise RequestError("ERROR_UNAUTHORIZED", "invalid device token", 401)
        conn.execute(
            "UPDATE relay_devices SET last_seen=? WHERE id=?", (now(), row["id"])
        )
        return row

    def node_is_online(row: sqlite3.Row, timestamp: float | None = None) -> bool:
        observed_at = now() if timestamp is None else timestamp
        return bool(row["last_seen"] and observed_at - row["last_seen"] <= NODE_ONLINE_SECONDS)

    def sweep(conn: sqlite3.Connection) -> None:
        timestamp = now()
        conn.execute(
            "DELETE FROM relay_pairings WHERE expires_at < ? OR used_at IS NOT NULL",
            (timestamp,),
        )
        conn.execute("DELETE FROM relay_messages WHERE expires_at < ?", (timestamp,))
        orphan_mailboxes = [
            row[0]
            for row in conn.execute(
                "SELECT m.id FROM relay_mailboxes m WHERE m.created_at<?"
                " AND NOT EXISTS (SELECT 1 FROM relay_pairings p WHERE p.mailbox_id=m.id)"
                " AND NOT EXISTS (SELECT 1 FROM relay_devices d"
                " WHERE d.mailbox_id=m.id AND d.role='phone')",
                (timestamp - PAIRING_TTL_SECONDS,),
            )
        ]
        for mailbox_id in orphan_mailboxes:
            conn.execute("DELETE FROM relay_messages WHERE mailbox_id=?", (mailbox_id,))
            conn.execute("DELETE FROM relay_devices WHERE mailbox_id=?", (mailbox_id,))
            conn.execute("DELETE FROM relay_mailboxes WHERE id=?", (mailbox_id,))
        expired_ids = [
            row[0]
            for row in conn.execute(
                "SELECT id FROM tasks WHERE status IN ('pending','leased') AND created_at < ?",
                (timestamp - TASK_TTL_SECONDS,),
            )
        ]
        conn.execute(
            "UPDATE tasks SET status='expired', error_code='ERROR_TASK_EXPIRED',"
            " error_description='task TTL exceeded', updated_at=?"
            " WHERE status IN ('pending','leased') AND created_at < ?",
            (timestamp, timestamp - TASK_TTL_SECONDS),
        )
        conn.execute(
            "UPDATE tasks SET status='pending', worker=NULL, leased_at=NULL, updated_at=?"
            " WHERE status='leased' AND leased_at < ? AND attempts < ?",
            (timestamp, timestamp - TASK_LEASE_SECONDS, MAX_ATTEMPTS),
        )
        exhausted_ids = [
            row[0]
            for row in conn.execute(
                "SELECT id FROM tasks WHERE status='leased' AND leased_at < ? AND attempts >= ?",
                (timestamp - TASK_LEASE_SECONDS, MAX_ATTEMPTS),
            )
        ]
        conn.execute(
            "UPDATE tasks SET status='failed', error_code='ERROR_MAX_ATTEMPTS',"
            " error_description='maximum solve attempts reached', updated_at=?"
            " WHERE status='leased' AND leased_at < ? AND attempts >= ?",
            (timestamp, timestamp - TASK_LEASE_SECONDS, MAX_ATTEMPTS),
        )
        old_ids = [
            row[0]
            for row in conn.execute(
                "SELECT id FROM tasks WHERE status IN ('solved','failed','expired','cancelled')"
                " AND updated_at < ?",
                (timestamp - RESULT_TTL_SECONDS,),
            )
        ]
        conn.executemany("DELETE FROM tasks WHERE id=?", ((task_id,) for task_id in old_ids))
        conn.execute(
            "DELETE FROM twocaptcha_tasks WHERE internal_task_id NOT IN (SELECT id FROM tasks)"
        )
        conn.execute(
            "UPDATE node_commands SET status='pending',leased_at=NULL,updated_at=?"
            " WHERE status='leased' AND leased_at < ? AND attempts < ?",
            (timestamp, timestamp - NODE_COMMAND_LEASE_SECONDS, NODE_COMMAND_MAX_ATTEMPTS),
        )
        exhausted_commands = conn.execute(
            "SELECT id,run_id FROM node_commands WHERE status='leased' AND leased_at < ?"
            " AND attempts >= ?",
            (timestamp - NODE_COMMAND_LEASE_SECONDS, NODE_COMMAND_MAX_ATTEMPTS),
        ).fetchall()
        for command in exhausted_commands:
            conn.execute(
                "UPDATE node_commands SET status='failed',updated_at=? WHERE id=?",
                (timestamp, command["id"]),
            )
            conn.execute(
                "UPDATE runs SET status='failed',updated_at=?"
                " WHERE id=? AND status IN ('starting','running','captcha','stopping')",
                (timestamp, command["run_id"]),
            )
        for task_id in expired_ids + exhausted_ids + old_ids:
            discard_ephemeral(task_id)

    async def watch_process(run_id: str, process: asyncio.subprocess.Process) -> None:
        exit_code = await process.wait()
        processes.pop(run_id, None)
        timestamp = now()
        with db() as conn:
            row = conn.execute("SELECT status FROM runs WHERE id=?", (run_id,)).fetchone()
            status = "cancelled" if row and row["status"] == "stopping" else (
                "succeeded" if exit_code == 0 else "failed"
            )
            conn.execute(
                "UPDATE runs SET status=?,exit_code=?,updated_at=? WHERE id=?",
                (status, exit_code, timestamp, run_id),
            )

    def serialize_run(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        return {
            "runId": row["id"],
            "registrationId": row["registration_id"],
            "registrationName": row["registration_name"],
            "nodeId": row["node_id"],
            "status": row["status"],
            "exitCode": row["exit_code"],
            "startedAt": row["started_at"],
            "updatedAt": row["updated_at"],
        }

    @app.errorhandler(RequestError)
    async def request_error(exc: RequestError):
        return _error(exc.code, exc.description, exc.http_status)

    @app.errorhandler(RequestEntityTooLarge)
    async def request_too_large(_exc: RequestEntityTooLarge):
        return _error("ERROR_REQUEST_TOO_LARGE", "request body is too large", 413)

    def create_task_record(
        body: Any,
        *,
        infer_active_run: bool = False,
        external_type: str | None = None,
    ) -> tuple[str, int | None]:
        public_task, context, assets = validate_task(body)
        run_id = _limited_text(body.get("runId"), "runId", 64) if isinstance(body, dict) else ""
        registration_id = ""
        task_id = str(uuid.uuid4())
        timestamp = now()
        compatibility_id: int | None = None
        with db() as conn:
            sweep(conn)
            run = None
            if run_id:
                run = conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
                if not run:
                    raise RequestError("ERROR_RUN_NOT_FOUND", "registration run not found", 404)
            elif infer_active_run:
                active_runs = conn.execute(
                    "SELECT * FROM runs WHERE status IN ('starting','running','captcha')"
                    " ORDER BY started_at"
                ).fetchall()
                if not active_runs:
                    raise RequestError(
                        "ERROR_NO_ACTIVE_RUN",
                        "start one registration in CaptchaMesh before creating a task",
                        409,
                    )
                if len(active_runs) > 1:
                    raise RequestError(
                        "ERROR_AMBIGUOUS_RUN",
                        "multiple registrations are active; pass the CaptchaMesh runId extension",
                        409,
                    )
                run = active_runs[0]
                run_id = run["id"]
            if run is not None:
                if run["status"] not in {"starting", "running", "captcha"}:
                    raise RequestError("ERROR_RUN_TERMINAL", "registration run is not active", 409)
                registration_id = run["registration_id"]
                public_task["runId"] = run_id
                public_task["registrationId"] = registration_id
                public_task["registrationName"] = run["registration_name"]
            conn.execute(
                "INSERT INTO tasks"
                " (id,url,sitekey,type,run_id,registration_id,task_json,context_required,status,created_at,updated_at)"
                " VALUES (?,?,?,?,?,?,?,?,'pending',?,?)",
                (
                    task_id,
                    public_task["websiteURL"],
                    public_task["websiteKey"],
                    public_task["type"],
                    run_id or None,
                    registration_id or None,
                    json.dumps(public_task, ensure_ascii=False, separators=(",", ":")),
                    int(bool(context or assets)),
                    timestamp,
                    timestamp,
                ),
            )
            if external_type is not None:
                cursor = conn.execute(
                    "INSERT INTO twocaptcha_tasks(internal_task_id,external_type,created_at)"
                    " VALUES (?,?,?)",
                    (task_id, external_type, timestamp),
                )
                compatibility_id = int(cursor.lastrowid)
            if run_id:
                conn.execute(
                    "UPDATE runs SET status='captcha',updated_at=? WHERE id=?",
                    (timestamp, run_id),
                )
        if context or assets:
            ephemeral_contexts[task_id] = context
        for asset_id, (asset_bytes, media_type) in assets.items():
            ephemeral_assets[asset_id] = {
                "taskId": task_id,
                "bytes": asset_bytes,
                "mediaType": media_type,
            }
        return task_id, compatibility_id

    @app.route("/v1/tasks", methods=["POST"])
    async def create_task():
        require_api_key()
        body = await request.get_json(force=True, silent=True)
        task_id, _ = create_task_record(body)
        return jsonify(errorId=0, taskId=task_id, protocolVersion=PROTOCOL_VERSION), 201

    def twocaptcha_error(exc: RequestError | TwoCaptchaCompatError):
        return jsonify(errorId=1, errorCode=exc.code, errorDescription=exc.description)

    async def twocaptcha_v1_params() -> dict[str, Any]:
        params = {key: value for key, value in request.args.items()}
        if request.method == "POST":
            form = await request.form
            params.update({key: value for key, value in form.items()})
        return params

    def require_twocaptcha_v1_key(params: dict[str, Any]) -> None:
        supplied = _limited_text(params.get("key"), "key", 4096, required=True)
        if api_key is not None and not hmac.compare_digest(supplied, api_key):
            raise RequestError("ERROR_KEY_DOES_NOT_EXIST", "invalid key")

    def twocaptcha_v1_response(
        params: dict[str, Any],
        status: int,
        value: str,
        *,
        price: str | None = None,
        plain_raw: bool = False,
    ):
        if str(params.get("json", "0")).lower() in {"1", "true"}:
            payload: dict[str, Any] = {"status": status, "request": value}
            if price is not None:
                payload["price"] = price
            response = jsonify(payload)
        else:
            prefix = "" if plain_raw or not status else "OK|"
            suffix = f"|{price}" if status and price is not None else ""
            response = app.response_class(
                f"{prefix}{value}{suffix}", content_type="text/plain; charset=utf-8"
            )
        if str(params.get("header_acao", "0")).lower() in {"1", "true"}:
            response.headers["Access-Control-Allow-Origin"] = "*"
        return response

    def twocaptcha_v1_error(params: dict[str, Any], exc: RequestError | TwoCaptchaCompatError):
        return twocaptcha_v1_response(params, 0, exc.code)

    def compatibility_task_row(task_id: Any) -> sqlite3.Row:
        if isinstance(task_id, str) and task_id.isdigit():
            task_id = int(task_id)
        if not isinstance(task_id, int) or isinstance(task_id, bool) or task_id < 1:
            raise RequestError("ERROR_WRONG_ID_FORMAT", "task id must be a positive integer")
        with db() as conn:
            sweep(conn)
            row = conn.execute(
                "SELECT t.*,c.external_type,c.feedback FROM twocaptcha_tasks c"
                " JOIN tasks t ON t.id=c.internal_task_id WHERE c.id=?",
                (task_id,),
            ).fetchone()
        if not row:
            raise RequestError("ERROR_WRONG_CAPTCHA_ID", "task not found")
        return row

    def compatibility_solution(row: sqlite3.Row) -> dict[str, Any]:
        try:
            value = json.loads(row["result"] or "{}")
            return value if isinstance(value, dict) else {"token": str(value)}
        except json.JSONDecodeError:
            return {"token": str(row["result"] or "")}

    @app.route("/in.php", methods=["GET", "POST"])
    async def twocaptcha_v1_submit():
        params = await twocaptcha_v1_params()
        try:
            require_twocaptcha_v1_key(params)
            translated, external_type = translate_v1_request(params)
            _, compatibility_id = create_task_record(
                translated, infer_active_run=True, external_type=external_type
            )
        except (RequestError, TwoCaptchaCompatError) as exc:
            return twocaptcha_v1_error(params, exc)
        return twocaptcha_v1_response(params, 1, str(compatibility_id))

    @app.route("/res.php", methods=["GET", "POST"])
    async def twocaptcha_v1_result():
        params = await twocaptcha_v1_params()
        try:
            require_twocaptcha_v1_key(params)
            action = _limited_text(params.get("action"), "action", 64, required=True).lower()
            if action == "getbalance":
                return twocaptcha_v1_response(params, 1, "999999.00000", plain_raw=True)
            if action in {"get", "get2"}:
                if params.get("ids"):
                    raise RequestError(
                        "ERROR_BAD_PARAMETERS", "multiple-id result queries are not supported"
                    )
                row = compatibility_task_row(params.get("id"))
                if row["status"] == "solved":
                    price = "0.00000" if action == "get2" else None
                    return twocaptcha_v1_response(
                        params, 1, legacy_result(compatibility_solution(row)), price=price
                    )
                if row["status"] in {"failed", "expired", "cancelled"}:
                    raise RequestError(
                        row["error_code"] or "ERROR_CAPTCHA_UNSOLVABLE",
                        row["error_description"] or "task failed",
                    )
                return twocaptcha_v1_response(params, 0, "CAPCHA_NOT_READY")
            if action in {"reportgood", "reportbad"}:
                row = compatibility_task_row(params.get("id"))
                if row["feedback"]:
                    raise RequestError("ERROR_DUPLICATE_REPORT", "feedback already recorded")
                feedback = "correct" if action == "reportgood" else "incorrect"
                with db() as conn:
                    conn.execute(
                        "UPDATE twocaptcha_tasks SET feedback=?,feedback_at=?"
                        " WHERE internal_task_id=?",
                        (feedback, now(), row["id"]),
                    )
                return twocaptcha_v1_response(
                    params, 1, "OK_REPORT_RECORDED", plain_raw=True
                )
            if action in {"add_pingback", "get_pingback", "del_pingback"}:
                raise RequestError(
                    "ERROR_CALLBACK_NOT_SUPPORTED", "pingback management is disabled"
                )
            raise RequestError("ERROR_BAD_PARAMETERS", f"unsupported action: {action}")
        except (RequestError, TwoCaptchaCompatError) as exc:
            return twocaptcha_v1_error(params, exc)

    @app.route("/createTask", methods=["POST"])
    async def twocaptcha_create_task():
        body = await request.get_json(force=True, silent=True)
        try:
            require_twocaptcha_key(body)
            translated, external_type = translate_create_request(body)
            _, compatibility_id = create_task_record(
                translated, infer_active_run=True, external_type=external_type
            )
        except (RequestError, TwoCaptchaCompatError) as exc:
            return twocaptcha_error(exc)
        return jsonify(errorId=0, taskId=compatibility_id)

    @app.route("/getTaskResult", methods=["POST"])
    async def twocaptcha_get_task_result():
        body = await request.get_json(force=True, silent=True)
        try:
            require_twocaptcha_key(body)
            try:
                row = compatibility_task_row(body.get("taskId"))
            except RequestError as exc:
                if exc.code in {"ERROR_WRONG_ID_FORMAT", "ERROR_WRONG_CAPTCHA_ID"}:
                    raise RequestError("ERROR_TASK_NOT_FOUND", exc.description) from exc
                raise
        except RequestError as exc:
            return twocaptcha_error(exc)
        if row["status"] == "solved":
            return jsonify(
                errorId=0,
                status="ready",
                solution=translate_solution(
                    row["external_type"], compatibility_solution(row)
                ),
                cost="0.00000",
                createTime=int(row["created_at"]),
                endTime=int(row["updated_at"]),
                solveCount=row["attempts"],
            )
        if row["status"] in {"failed", "expired", "cancelled"}:
            return jsonify(
                errorId=1,
                errorCode=row["error_code"] or "ERROR_CAPTCHA_UNSOLVABLE",
                errorDescription=row["error_description"] or "task failed",
            )
        return jsonify(errorId=0, status="processing")

    @app.route("/getBalance", methods=["POST"])
    async def twocaptcha_get_balance():
        body = await request.get_json(force=True, silent=True)
        try:
            require_twocaptcha_key(body)
        except RequestError as exc:
            return twocaptcha_error(exc)
        # CaptchaMesh has no billing ledger.  A positive synthetic balance
        # keeps clients that perform a startup balance check from treating the
        # private, manual worker as an exhausted paid account.
        return jsonify(errorId=0, balance="999999.00000")

    @app.route("/reportCorrect", methods=["POST"])
    @app.route("/reportIncorrect", methods=["POST"])
    async def twocaptcha_report_result():
        body = await request.get_json(force=True, silent=True)
        try:
            require_twocaptcha_key(body)
            task_id = body.get("taskId")
            if isinstance(task_id, str) and task_id.isdigit():
                task_id = int(task_id)
            if not isinstance(task_id, int) or isinstance(task_id, bool) or task_id < 1:
                raise RequestError("ERROR_BAD_PARAMETERS", "taskId must be a positive integer")
            feedback = "correct" if request.path.endswith("reportCorrect") else "incorrect"
            with db() as conn:
                row = conn.execute(
                    "SELECT id FROM twocaptcha_tasks WHERE id=?", (task_id,)
                ).fetchone()
                if not row:
                    raise RequestError("ERROR_TASK_NOT_FOUND", "task not found")
                conn.execute(
                    "UPDATE twocaptcha_tasks SET feedback=?,feedback_at=? WHERE id=?",
                    (feedback, now(), task_id),
                )
        except RequestError as exc:
            return twocaptcha_error(exc)
        return jsonify(errorId=0, status="success")

    @app.route("/v1/tasks/<task_id>", methods=["GET"])
    async def task_result(task_id: str):
        require_api_key()
        with db() as conn:
            sweep(conn)
            row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if not row:
            return _error("ERROR_TASK_NOT_FOUND", "task not found", 404)
        if row["status"] == "solved":
            try:
                solution = json.loads(row["result"] or "{}")
            except json.JSONDecodeError:
                solution = {"token": row["result"]}
            return jsonify(errorId=0, status="ready", solution=solution)
        if row["status"] in {"failed", "expired", "cancelled"}:
            return jsonify(
                errorId=1,
                status=row["status"],
                errorCode=row["error_code"] or "ERROR_CAPTCHA_UNSOLVABLE",
                errorDescription=row["error_description"] or "task failed",
            )
        return jsonify(errorId=0, status="processing", attempts=row["attempts"])

    @app.route("/v1/tasks/<task_id>/cancel", methods=["POST"])
    async def cancel_task(task_id: str):
        require_api_key()
        with db() as conn:
            row = conn.execute("SELECT status FROM tasks WHERE id=?", (task_id,)).fetchone()
            if not row:
                return _error("ERROR_TASK_NOT_FOUND", "task not found", 404)
            if row["status"] in {"solved", "failed", "expired", "cancelled"}:
                return _error("ERROR_TASK_TERMINAL", "task is already terminal", 409)
            conn.execute(
                "UPDATE tasks SET status='cancelled', error_code='ERROR_CANCELLED',"
                " error_description='cancelled by client', updated_at=? WHERE id=?",
                (now(), task_id),
            )
        discard_ephemeral(task_id)
        return jsonify(errorId=0, status="cancelled")

    @app.route("/v1/nodes/join", methods=["POST"])
    async def node_join():
        require_node_enrollment()
        body = await request.get_json(force=True, silent=True) or {}
        node_id = _validate_registration_id(body.get("nodeId"), "node id")
        node_name = _limited_text(body.get("name"), "node name", 128, required=True)
        advertised = body.get("registrations", [])
        if not isinstance(advertised, list) or len(advertised) > 100:
            raise RequestError(
                "ERROR_BAD_NODE", "registrations must be an array with at most 100 items"
            )
        offers: list[tuple[str, dict[str, Any]]] = []
        seen_local_ids: set[str] = set()
        for item in advertised:
            offer = _validate_registration_offer(item)
            if offer["id"] in seen_local_ids:
                raise RequestError("ERROR_BAD_NODE", "duplicate registration id in node offer")
            seen_local_ids.add(offer["id"])
            offers.append((f"{node_id}:{offer['id']}", offer))

        token = "n-" + secrets.token_hex(32)
        timestamp = now()
        with db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "INSERT INTO nodes(id,name,token,version,device,last_seen) VALUES (?,?,?,?,?,?)"
                " ON CONFLICT(id) DO UPDATE SET name=excluded.name,token=excluded.token,"
                " version=excluded.version,device=excluded.device,last_seen=excluded.last_seen",
                (
                    node_id,
                    node_name,
                    token,
                    _limited_text(body.get("version"), "version", 64),
                    _limited_text(body.get("device"), "device", 256),
                    timestamp,
                ),
            )
            for public_id, offer in offers:
                conn.execute(
                    "INSERT INTO remote_registrations"
                    " (public_id,node_id,local_id,name,summary,provides,details,description,"
                    " captcha_types,enabled,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)"
                    " ON CONFLICT(public_id) DO UPDATE SET name=excluded.name,"
                    " summary=excluded.summary,provides=excluded.provides,details=excluded.details,"
                    " description=excluded.description,captcha_types=excluded.captcha_types,"
                    " enabled=excluded.enabled,updated_at=excluded.updated_at",
                    (
                        public_id,
                        node_id,
                        offer["id"],
                        offer["name"],
                        offer["summary"],
                        json.dumps(offer["provides"], ensure_ascii=False),
                        json.dumps(offer["details"], ensure_ascii=False),
                        offer["description"],
                        json.dumps(offer["captchaTypes"], ensure_ascii=False),
                        int(offer["enabled"]),
                        timestamp,
                    ),
                )
            if seen_local_ids:
                existing_ids = {
                    row[0]
                    for row in conn.execute(
                        "SELECT local_id FROM remote_registrations WHERE node_id=?",
                        (node_id,),
                    )
                }
                conn.executemany(
                    "DELETE FROM remote_registrations WHERE node_id=? AND local_id=?",
                    ((node_id, local_id) for local_id in existing_ids - seen_local_ids),
                )
            else:
                conn.execute("DELETE FROM remote_registrations WHERE node_id=?", (node_id,))
        return jsonify(
            nodeToken=token,
            protocolVersion=PROTOCOL_VERSION,
            registered=len(offers),
            pollSeconds=25,
        )

    @app.route("/v1/nodes/poll", methods=["POST"])
    async def node_poll():
        body = await request.get_json(force=True, silent=True) or {}
        wait_seconds = body.get("waitSeconds", 25)
        if not isinstance(wait_seconds, int):
            raise RequestError("ERROR_BAD_REQUEST", "waitSeconds must be an integer")
        wait_seconds = max(0, min(wait_seconds, 55))
        deadline = now() + wait_seconds
        first_attempt = True
        while first_attempt or now() <= deadline:
            first_attempt = False
            with db() as conn:
                conn.execute("BEGIN IMMEDIATE")
                node = require_node(conn)
                timestamp = now()
                conn.execute(
                    "UPDATE nodes SET last_seen=? WHERE id=?", (timestamp, node["id"])
                )
                sweep(conn)
                command = conn.execute(
                    "SELECT * FROM node_commands WHERE node_id=? AND status='pending'"
                    " ORDER BY created_at LIMIT 1",
                    (node["id"],),
                ).fetchone()
                if command:
                    conn.execute(
                        "UPDATE node_commands SET status='leased',attempts=attempts+1,"
                        " leased_at=?,updated_at=? WHERE id=? AND status='pending'",
                        (timestamp, timestamp, command["id"]),
                    )
                    return jsonify(
                        commandId=command["id"],
                        action=command["action"],
                        runId=command["run_id"],
                        registrationId=command["local_registration_id"],
                        leaseExpiresAt=timestamp + NODE_COMMAND_LEASE_SECONDS,
                    )
            if wait_seconds == 0:
                break
            await asyncio.sleep(1.0)
        return "", 204

    @app.route("/v1/nodes/report", methods=["POST"])
    async def node_report():
        body = await request.get_json(force=True, silent=True) or {}
        command_id = _limited_text(
            body.get("commandId"), "commandId", 80, required=True
        )
        run_id = _limited_text(body.get("runId"), "runId", 64, required=True)
        status = _limited_text(body.get("status"), "status", 32, required=True)
        if status not in {"running", "succeeded", "failed", "cancelled", "interrupted"}:
            raise RequestError("ERROR_BAD_NODE_REPORT", "unsupported run status")
        exit_code = body.get("exitCode")
        if exit_code is not None and not isinstance(exit_code, int):
            raise RequestError("ERROR_BAD_NODE_REPORT", "exitCode must be an integer")
        with db() as conn:
            node = require_node(conn)
            command = conn.execute(
                "SELECT * FROM node_commands WHERE id=? AND node_id=? AND run_id=?",
                (command_id, node["id"], run_id),
            ).fetchone()
            if not command:
                return _error("ERROR_COMMAND_NOT_FOUND", "node command not found", 404)
            timestamp = now()
            conn.execute("UPDATE nodes SET last_seen=? WHERE id=?", (timestamp, node["id"]))
            conn.execute(
                "UPDATE node_commands SET status='done',updated_at=? WHERE id=?",
                (timestamp, command_id),
            )
            conn.execute(
                "UPDATE runs SET status=?,exit_code=?,updated_at=? WHERE id=? AND node_id=?",
                (status, exit_code, timestamp, run_id, node["id"]),
            )
        return jsonify(ok=True)

    @app.route("/v1/registrations", methods=["GET"])
    async def registration_list():
        require_api_key()
        items = []
        with db() as conn:
            for registration in registrations.values():
                latest = conn.execute(
                    "SELECT * FROM runs WHERE registration_id=? ORDER BY started_at DESC LIMIT 1",
                    (registration["id"],),
                ).fetchone()
                items.append(
                    {
                        "id": registration["id"],
                        "name": registration["name"],
                        "summary": registration["summary"],
                        "provides": registration["provides"],
                        "details": registration["details"],
                        "description": registration["description"],
                        "captchaTypes": registration["captchaTypes"],
                        "enabled": registration["enabled"],
                        "source": "当前电脑",
                        "remote": False,
                        "online": True,
                        "latestRun": serialize_run(latest),
                    }
                )
            timestamp = now()
            for row in conn.execute(
                "SELECT r.*,n.name AS node_name,n.last_seen AS node_last_seen"
                " FROM remote_registrations r JOIN nodes n ON n.id=r.node_id"
                " ORDER BY r.name COLLATE NOCASE"
            ):
                latest = conn.execute(
                    "SELECT * FROM runs WHERE registration_id=? ORDER BY started_at DESC LIMIT 1",
                    (row["public_id"],),
                ).fetchone()
                online = bool(
                    row["node_last_seen"]
                    and timestamp - row["node_last_seen"] <= NODE_ONLINE_SECONDS
                )
                items.append(
                    {
                        "id": row["public_id"],
                        "name": row["name"],
                        "summary": row["summary"],
                        "provides": json.loads(row["provides"] or "[]"),
                        "details": json.loads(row["details"] or "[]"),
                        "description": row["description"],
                        "captchaTypes": json.loads(row["captcha_types"] or "[]"),
                        "enabled": bool(row["enabled"] and online),
                        "source": row["node_name"],
                        "remote": True,
                        "online": online,
                        "latestRun": serialize_run(latest),
                    }
                )
        return jsonify(registrations=items)

    @app.route("/v1/registrations/<registration_id>/start", methods=["POST"])
    async def registration_start(registration_id: str):
        require_api_key()
        registration = registrations.get(registration_id)
        timestamp = now()
        remote_registration: sqlite3.Row | None = None
        remote_node: sqlite3.Row | None = None
        if registration is None:
            with db() as conn:
                remote_registration = conn.execute(
                    "SELECT * FROM remote_registrations WHERE public_id=?", (registration_id,)
                ).fetchone()
                if remote_registration:
                    remote_node = conn.execute(
                        "SELECT * FROM nodes WHERE id=?", (remote_registration["node_id"],)
                    ).fetchone()
            if remote_registration is None:
                return _error(
                    "ERROR_REGISTRATION_NOT_FOUND", "registration is not configured", 404
                )
            if not remote_registration["enabled"]:
                return _error("ERROR_REGISTRATION_DISABLED", "registration is disabled", 409)
            if remote_node is None or not node_is_online(remote_node, timestamp):
                return _error("ERROR_NODE_OFFLINE", "registration node is offline", 409)
            registration_name = remote_registration["name"]
            run_node_id: str | None = remote_registration["node_id"]
        else:
            if not registration["enabled"]:
                return _error("ERROR_REGISTRATION_DISABLED", "registration is disabled", 409)
            if not registration["cwd"].is_dir():
                return _error(
                    "ERROR_LAUNCH_CONFIG", "registration working directory is missing", 500
                )
            registration_name = registration["name"]
            run_node_id = None

        with db() as conn:
            active = conn.execute(
                "SELECT id FROM runs WHERE registration_id=?"
                " AND status IN ('starting','running','captcha','stopping') LIMIT 1",
                (registration_id,),
            ).fetchone()
            if active:
                return _error("ERROR_ALREADY_RUNNING", "registration already has an active run", 409)
            run_id = "run-" + uuid.uuid4().hex
            conn.execute(
                "INSERT INTO runs"
                " (id,registration_id,registration_name,node_id,status,started_at,updated_at)"
                " VALUES (?,?,?,?,'starting',?,?)",
                (
                    run_id,
                    registration_id,
                    registration_name,
                    run_node_id,
                    timestamp,
                    timestamp,
                ),
            )
            if remote_registration is not None:
                command_id = "cmd-" + uuid.uuid4().hex
                conn.execute(
                    "INSERT INTO node_commands"
                    " (id,node_id,run_id,action,local_registration_id,status,created_at,updated_at)"
                    " VALUES (?,?,?,?,?,'pending',?,?)",
                    (
                        command_id,
                        remote_registration["node_id"],
                        run_id,
                        "start",
                        remote_registration["local_id"],
                        timestamp,
                        timestamp,
                    ),
                )
                row = conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()

        if remote_registration is not None:
            return jsonify(run=serialize_run(row)), 201

        environment = os.environ.copy()
        environment.update(
            {
                "CAPTCHAMESH_URL": os.environ.get(
                    "CAPTCHAMESH_INTERNAL_URL", "http://127.0.0.1:8890"
                ),
                "CAPTCHAMESH_RUN_ID": run_id,
                "CAPTCHAMESH_REGISTRATION_ID": registration_id,
            }
        )
        existing_python_path = environment.get("PYTHONPATH", "")
        environment["PYTHONPATH"] = str(Path(__file__).parent) + (
            os.pathsep + existing_python_path if existing_python_path else ""
        )
        if api_key:
            environment["CAPTCHAMESH_API_KEY"] = api_key
        try:
            process = await asyncio.create_subprocess_exec(
                *registration["command"],
                cwd=registration["cwd"],
                env=environment,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
                start_new_session=True,
            )
        except (OSError, ValueError) as exc:
            with db() as conn:
                conn.execute(
                    "UPDATE runs SET status='failed',updated_at=? WHERE id=?",
                    (now(), run_id),
                )
            raise RequestError("ERROR_LAUNCH_FAILED", str(exc), 500) from exc

        processes[run_id] = process
        with db() as conn:
            conn.execute(
                "UPDATE runs SET status='running',pid=?,updated_at=? WHERE id=?",
                (process.pid, now(), run_id),
            )
            row = conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        asyncio.create_task(watch_process(run_id, process))
        return jsonify(run=serialize_run(row)), 201

    @app.route("/v1/runs/<run_id>", methods=["GET"])
    async def run_status(run_id: str):
        require_api_key()
        with db() as conn:
            row = conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
            if not row:
                return _error("ERROR_RUN_NOT_FOUND", "registration run not found", 404)
            task_counts = dict(
                conn.execute(
                    "SELECT status,COUNT(*) FROM tasks WHERE run_id=? GROUP BY status",
                    (run_id,),
                ).fetchall()
            )
        return jsonify(run=serialize_run(row), tasks=task_counts)

    @app.route("/v1/runs/<run_id>/stop", methods=["POST"])
    async def run_stop(run_id: str):
        require_api_key()
        with db() as conn:
            row = conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
            if not row:
                return _error("ERROR_RUN_NOT_FOUND", "registration run not found", 404)
            if row["status"] not in {"starting", "running", "captcha"}:
                return _error("ERROR_RUN_TERMINAL", "registration run is already terminal", 409)
            conn.execute(
                "UPDATE runs SET status='stopping',updated_at=? WHERE id=?", (now(), run_id)
            )
            if row["node_id"]:
                command_id = "cmd-" + uuid.uuid4().hex
                conn.execute(
                    "INSERT INTO node_commands"
                    " (id,node_id,run_id,action,status,created_at,updated_at)"
                    " VALUES (?,?,?,'stop','pending',?,?)",
                    (command_id, row["node_id"], run_id, now(), now()),
                )
                return jsonify(errorId=0, status="stopping")
        process = processes.get(run_id)
        if process and process.returncode is None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
                await asyncio.wait_for(process.wait(), timeout=5)
            except asyncio.TimeoutError:
                os.killpg(process.pid, signal.SIGKILL)
                await process.wait()
            except ProcessLookupError:
                pass
        return jsonify(errorId=0, status="stopping")

    @app.route("/v1/workers/join", methods=["POST"])
    async def worker_join():
        require_api_key()
        body = await request.get_json(force=True, silent=True) or {}
        name = _limited_text(body.get("name"), "name", 128, required=True)
        domains = body.get("domains", [])
        types = body.get("types", [])
        if not isinstance(domains, list) or len(domains) > 100:
            raise RequestError("ERROR_BAD_WORKER", "domains must be an array")
        if not isinstance(types, list) or not types:
            raise RequestError("ERROR_BAD_WORKER", "types must be a non-empty array")
        clean_domains = [
            _limited_text(domain, "domain", 255, required=True).lower().lstrip(".")
            for domain in domains
        ]
        clean_types = [
            _limited_text(item, "worker type", 64, required=True) for item in types
        ]
        if any(item not in SUPPORTED_TYPES for item in clean_types):
            raise RequestError("ERROR_BAD_WORKER", "worker advertised an unsupported type")
        token = "w-" + secrets.token_hex(32)
        with db() as conn:
            conn.execute(
                "INSERT INTO workers"
                " (name,token,domains,types,app_version,device,last_seen) VALUES (?,?,?,?,?,?,?)"
                " ON CONFLICT(name) DO UPDATE SET token=excluded.token,domains=excluded.domains,"
                " types=excluded.types,app_version=excluded.app_version,device=excluded.device,"
                " last_seen=excluded.last_seen",
                (
                    name,
                    token,
                    json.dumps(clean_domains),
                    json.dumps(clean_types),
                    _limited_text(body.get("appVersion"), "appVersion", 64),
                    _limited_text(body.get("device"), "device", 256),
                    now(),
                ),
            )
        return jsonify(
            workerToken=token,
            protocolVersion=PROTOCOL_VERSION,
            leaseSeconds=TASK_LEASE_SECONDS,
        )

    @app.route("/v1/workers/poll", methods=["POST"])
    async def worker_poll():
        body = await request.get_json(force=True, silent=True) or {}
        run_id = _limited_text(body.get("runId"), "runId", 64)
        wait_seconds = body.get("waitSeconds", 50)
        if not isinstance(wait_seconds, int):
            raise RequestError("ERROR_BAD_REQUEST", "waitSeconds must be an integer")
        wait_seconds = max(0, min(wait_seconds, 55))
        deadline = now() + wait_seconds
        first_attempt = True
        while first_attempt or now() <= deadline:
            first_attempt = False
            with db() as conn:
                conn.execute("BEGIN IMMEDIATE")
                worker = require_worker(conn)
                conn.execute("UPDATE workers SET last_seen=? WHERE name=?", (now(), worker["name"]))
                sweep(conn)
                worker_types = json.loads(worker["types"] or "[]")
                domains = json.loads(worker["domains"] or "[]")
                if not worker_types:
                    raise RequestError(
                        "ERROR_BAD_WORKER", "worker must rejoin with supported types", 409
                    )
                candidates: list[sqlite3.Row] = []
                for worker_type in worker_types:
                    if run_id:
                        rows = conn.execute(
                            "SELECT * FROM tasks WHERE status='pending' AND type=? AND run_id=?"
                            " ORDER BY created_at LIMIT 100",
                            (worker_type, run_id),
                        ).fetchall()
                    else:
                        rows = conn.execute(
                            "SELECT * FROM tasks WHERE status='pending' AND type=?"
                            " ORDER BY created_at LIMIT 100",
                            (worker_type,),
                        ).fetchall()
                    candidates.extend(rows)
                candidates.sort(key=lambda row: row["created_at"])
                selected = next(
                    (
                        row
                        for row in candidates[:100]
                        if _domain_allowed(row["url"], domains)
                    ),
                    None,
                )
                if selected and selected["context_required"] and selected["id"] not in ephemeral_contexts:
                    conn.execute(
                        "UPDATE tasks SET status='failed', error_code='ERROR_CONTEXT_LOST',"
                        " error_description='sensitive context is no longer available', updated_at=?"
                        " WHERE id=?",
                        (now(), selected["id"]),
                    )
                    selected = None
                if selected:
                    leased_at = now()
                    conn.execute(
                        "UPDATE tasks SET status='leased',worker=?,leased_at=?,attempts=attempts+1,"
                        " updated_at=? WHERE id=? AND status='pending'",
                        (worker["name"], leased_at, leased_at, selected["id"]),
                    )
                    task = json.loads(selected["task_json"])
                    context = ephemeral_contexts.get(selected["id"], {})
                    return jsonify(
                        taskId=selected["id"],
                        task=task,
                        context=context,
                        leaseExpiresAt=leased_at + TASK_LEASE_SECONDS,
                    )
            if wait_seconds == 0:
                break
            await asyncio.sleep(1.0)
        return "", 204

    @app.route("/v1/workers/heartbeat", methods=["POST"])
    async def worker_heartbeat():
        body = await request.get_json(force=True, silent=True) or {}
        task_id = _limited_text(body.get("taskId"), "taskId", 64)
        with db() as conn:
            worker = require_worker(conn)
            timestamp = now()
            conn.execute("UPDATE workers SET last_seen=? WHERE name=?", (timestamp, worker["name"]))
            if task_id:
                conn.execute(
                    "UPDATE tasks SET leased_at=?,updated_at=?"
                    " WHERE id=? AND status='leased' AND worker=?",
                    (timestamp, timestamp, task_id, worker["name"]),
                )
        return jsonify(ok=True)

    @app.route("/v1/assets/<asset_id>", methods=["GET"])
    async def worker_asset(asset_id: str):
        asset_id = _limited_text(asset_id, "assetId", 128, required=True)
        with db() as conn:
            worker = require_worker(conn)
            asset = ephemeral_assets.get(asset_id)
            if asset is None:
                return _error("ERROR_ASSET_NOT_FOUND", "challenge asset is unavailable", 404)
            row = conn.execute(
                "SELECT status,worker FROM tasks WHERE id=?", (asset["taskId"],)
            ).fetchone()
            if not row or row["status"] != "leased" or row["worker"] != worker["name"]:
                return _error("ERROR_ASSET_FORBIDDEN", "asset is not leased to this worker", 403)
        response = app.response_class(asset["bytes"], mimetype=asset["mediaType"])
        response.headers["Cache-Control"] = "no-store, max-age=0"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.route("/v1/workers/submit", methods=["POST"])
    async def worker_submit():
        body = await request.get_json(force=True, silent=True) or {}
        task_id = _limited_text(body.get("taskId"), "taskId", 64, required=True)
        status = _limited_text(body.get("status"), "status", 32, required=True)
        with db() as conn:
            worker = require_worker(conn)
            row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            if not row or row["status"] != "leased" or row["worker"] != worker["name"]:
                return _error("ERROR_LEASE_MISMATCH", "task is not leased to this worker", 409)
            timestamp = now()
            if status == "ready":
                try:
                    persisted_solution = normalize_solution(
                        row["type"], json.loads(row["task_json"]), body.get("solution")
                    )
                except (SolutionError, json.JSONDecodeError) as exc:
                    raise RequestError("ERROR_BAD_SOLUTION", str(exc)) from exc
                conn.execute(
                    "UPDATE tasks SET status='solved',result=?,error_code=NULL,"
                    " error_description=NULL,updated_at=? WHERE id=?",
                    (
                        json.dumps(
                            persisted_solution, ensure_ascii=False, separators=(",", ":")
                        ),
                        timestamp,
                        task_id,
                    ),
                )
                conn.execute(
                    "UPDATE workers SET solved=solved+1,last_seen=? WHERE name=?",
                    (timestamp, worker["name"]),
                )
                if row["run_id"]:
                    conn.execute(
                        "UPDATE runs SET status='running',updated_at=?"
                        " WHERE id=? AND status='captcha'",
                        (timestamp, row["run_id"]),
                    )
            elif status == "failed":
                error_code = _limited_text(
                    body.get("errorCode", "ERROR_CAPTCHA_UNSOLVABLE"),
                    "errorCode",
                    128,
                    required=True,
                )
                description = _limited_text(
                    body.get("errorDescription", "solve failed"), "errorDescription", 1000
                )
                retryable = bool(body.get("retryable", False)) and row["attempts"] < MAX_ATTEMPTS
                if retryable:
                    conn.execute(
                        "UPDATE tasks SET status='pending',worker=NULL,leased_at=NULL,"
                        " error_code=?,error_description=?,updated_at=? WHERE id=?",
                        (error_code, description, timestamp, task_id),
                    )
                else:
                    conn.execute(
                        "UPDATE tasks SET status='failed',error_code=?,error_description=?,"
                        " updated_at=? WHERE id=?",
                        (error_code, description, timestamp, task_id),
                    )
                    discard_ephemeral(task_id)
                    if row["run_id"]:
                        conn.execute(
                            "UPDATE runs SET status='running',updated_at=?"
                            " WHERE id=? AND status='captcha'",
                            (timestamp, row["run_id"]),
                        )
                conn.execute(
                    "UPDATE workers SET failed=failed+1,last_seen=? WHERE name=?",
                    (timestamp, worker["name"]),
                )
            else:
                raise RequestError("ERROR_BAD_SOLUTION", "status must be ready or failed")
        if status == "ready":
            discard_ephemeral(task_id)
        return jsonify(ok=True)

    @app.route("/.well-known/captchamesh", methods=["GET"])
    async def relay_manifest():
        return jsonify(
            protocol="captchamesh-relay",
            protocolVersions=[RELAY_PROTOCOL_VERSION],
            pairingTtlSeconds=PAIRING_TTL_SECONDS,
            messageTtlSeconds=RELAY_MAX_TTL_SECONDS,
            maxMessageBytes=MAX_RELAY_CIPHERTEXT_BYTES,
            maxQueuedMessages=MAX_RELAY_MESSAGES_PER_MAILBOX,
            transports=["https-long-poll"],
            push=["foreground-service"],
            encryption="endpoint-managed",
            pairingAuth="optional" if allow_public_pairing or api_key is None else "bearer",
        )

    @app.route("/v1/pairing/start", methods=["POST"])
    async def relay_pairing_start():
        public_slot = require_pairing_start_access()
        body = await request.get_json(force=True, silent=True) or {}
        node_name = _limited_text(body.get("nodeName"), "nodeName", 128, required=True)
        mailbox_id = "mb-" + secrets.token_urlsafe(18)
        node_device_id = "device-" + secrets.token_urlsafe(18)
        node_token = "dev-" + secrets.token_urlsafe(32)
        join_token = "join-" + secrets.token_urlsafe(32)
        timestamp = now()
        expires_at = timestamp + PAIRING_TTL_SECONDS
        with db() as conn:
            sweep(conn)
            if public_slot:
                pending = conn.execute(
                    "SELECT COUNT(*) FROM relay_pairings"
                    " WHERE used_at IS NULL AND expires_at>?",
                    (timestamp,),
                ).fetchone()[0]
                if pending >= MAX_PENDING_PUBLIC_PAIRINGS:
                    raise RequestError(
                        "ERROR_PAIRING_CAPACITY",
                        "public pairing capacity is temporarily full",
                        429,
                    )
            conn.execute(
                "INSERT INTO relay_mailboxes(id,status,created_at) VALUES (?,'active',?)",
                (mailbox_id, timestamp),
            )
            conn.execute(
                "INSERT INTO relay_devices"
                " (id,mailbox_id,role,name,token_hash,created_at,last_seen)"
                " VALUES (?,?, 'node', ?,?,?,?)",
                (
                    node_device_id,
                    mailbox_id,
                    node_name,
                    _token_hash(node_token),
                    timestamp,
                    timestamp,
                ),
            )
            conn.execute(
                "INSERT INTO relay_pairings"
                " (token_hash,mailbox_id,node_device_id,expires_at) VALUES (?,?,?,?)",
                (_token_hash(join_token), mailbox_id, node_device_id, expires_at),
            )
        return jsonify(
            protocolVersion=RELAY_PROTOCOL_VERSION,
            mailboxId=mailbox_id,
            nodeDeviceId=node_device_id,
            nodeToken=node_token,
            joinToken=join_token,
            expiresAt=int(expires_at),
        ), 201

    @app.route("/v1/pairing/claim", methods=["POST"])
    async def relay_pairing_claim():
        body = await request.get_json(force=True, silent=True) or {}
        join_token = _limited_text(
            body.get("joinToken"), "joinToken", 256, required=True
        )
        phone_name = _limited_text(
            body.get("phoneName"), "phoneName", 128, required=True
        )
        timestamp = now()
        phone_device_id = "device-" + secrets.token_urlsafe(18)
        phone_token = "dev-" + secrets.token_urlsafe(32)
        with db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            pairing = conn.execute(
                "SELECT * FROM relay_pairings WHERE token_hash=?",
                (_token_hash(join_token),),
            ).fetchone()
            if not pairing or pairing["used_at"] is not None or pairing["expires_at"] <= timestamp:
                raise RequestError(
                    "ERROR_PAIRING_EXPIRED", "pairing code is invalid or expired", 410
                )
            mailbox = conn.execute(
                "SELECT status FROM relay_mailboxes WHERE id=?", (pairing["mailbox_id"],)
            ).fetchone()
            if not mailbox or mailbox["status"] != "active":
                raise RequestError("ERROR_MAILBOX_UNAVAILABLE", "mailbox is unavailable", 410)
            existing_phone = conn.execute(
                "SELECT id FROM relay_devices"
                " WHERE mailbox_id=? AND role='phone' AND revoked_at IS NULL",
                (pairing["mailbox_id"],),
            ).fetchone()
            if existing_phone:
                raise RequestError("ERROR_PHONE_ALREADY_PAIRED", "mailbox already has a phone", 409)
            conn.execute(
                "UPDATE relay_pairings SET used_at=? WHERE token_hash=? AND used_at IS NULL",
                (timestamp, pairing["token_hash"]),
            )
            conn.execute(
                "INSERT INTO relay_devices"
                " (id,mailbox_id,role,name,token_hash,created_at,last_seen)"
                " VALUES (?,?, 'phone', ?,?,?,?)",
                (
                    phone_device_id,
                    pairing["mailbox_id"],
                    phone_name,
                    _token_hash(phone_token),
                    timestamp,
                    timestamp,
                ),
            )
        return jsonify(
            protocolVersion=RELAY_PROTOCOL_VERSION,
            mailboxId=pairing["mailbox_id"],
            phoneDeviceId=phone_device_id,
            deviceToken=phone_token,
        )

    def validate_relay_envelope(body: Any, device: sqlite3.Row) -> dict[str, Any]:
        if not isinstance(body, dict):
            raise RequestError("ERROR_BAD_RELAY_MESSAGE", "message must be an object")
        if body.get("protocolVersion") != RELAY_PROTOCOL_VERSION:
            raise RequestError("ERROR_BAD_RELAY_MESSAGE", "unsupported relay protocol")
        mailbox_id = _limited_text(
            body.get("mailboxId"), "mailboxId", 128, required=True
        )
        message_id = _limited_text(
            body.get("messageId"), "messageId", 128, required=True
        )
        direction = _limited_text(
            body.get("direction"), "direction", 32, required=True
        )
        expected_direction = (
            "node_to_phone" if device["role"] == "node" else "phone_to_node"
        )
        if mailbox_id != device["mailbox_id"] or direction != expected_direction:
            raise RequestError(
                "ERROR_RELAY_DIRECTION", "device cannot send this relay direction", 403
            )
        if not re.fullmatch(r"[A-Za-z0-9._~-]{1,128}", message_id):
            raise RequestError("ERROR_BAD_RELAY_MESSAGE", "messageId is invalid")
        expires_at = body.get("expiresAt")
        timestamp = now()
        if (
            not isinstance(expires_at, int)
            or isinstance(expires_at, bool)
            or expires_at <= int(timestamp)
            or expires_at > int(timestamp) + RELAY_MAX_TTL_SECONDS
        ):
            raise RequestError("ERROR_BAD_RELAY_MESSAGE", "expiresAt is invalid")
        try:
            b64url_decode(
                body.get("nonce"), name="nonce", expected_bytes=RELAY_NONCE_BYTES
            )
            ciphertext = b64url_decode(body.get("ciphertext"), name="ciphertext")
        except RelayProtocolError as exc:
            raise RequestError("ERROR_BAD_RELAY_MESSAGE", str(exc)) from exc
        if len(ciphertext) < 16 or len(ciphertext) > MAX_RELAY_CIPHERTEXT_BYTES:
            raise RequestError("ERROR_BAD_RELAY_MESSAGE", "ciphertext size is invalid")
        return {
            "mailboxId": mailbox_id,
            "messageId": message_id,
            "direction": direction,
            "expiresAt": expires_at,
            "nonce": body["nonce"],
            "ciphertext": body["ciphertext"],
        }

    @app.route("/v1/relay/messages", methods=["POST"])
    async def relay_message_send():
        body = await request.get_json(force=True, silent=True)
        with db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            device = require_relay_device(conn)
            sweep(conn)
            envelope = validate_relay_envelope(body, device)
            queued = conn.execute(
                "SELECT COUNT(*) FROM relay_messages WHERE mailbox_id=?",
                (device["mailbox_id"],),
            ).fetchone()[0]
            if queued >= MAX_RELAY_MESSAGES_PER_MAILBOX:
                raise RequestError("ERROR_RELAY_QUOTA", "mailbox queue is full", 429)
            try:
                conn.execute(
                    "INSERT INTO relay_messages"
                    " (message_id,mailbox_id,direction,expires_at,nonce,ciphertext,created_at)"
                    " VALUES (?,?,?,?,?,?,?)",
                    (
                        envelope["messageId"],
                        envelope["mailboxId"],
                        envelope["direction"],
                        envelope["expiresAt"],
                        envelope["nonce"],
                        envelope["ciphertext"],
                        now(),
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise RequestError(
                    "ERROR_RELAY_REPLAY", "messageId was already used", 409
                ) from exc
        return jsonify(ok=True, messageId=envelope["messageId"]), 201

    @app.route("/v1/relay/poll", methods=["POST"])
    async def relay_message_poll():
        body = await request.get_json(force=True, silent=True) or {}
        wait_seconds = body.get("waitSeconds", 25)
        if not isinstance(wait_seconds, int) or isinstance(wait_seconds, bool):
            raise RequestError("ERROR_BAD_REQUEST", "waitSeconds must be an integer")
        wait_seconds = max(0, min(wait_seconds, 30))
        deadline = now() + wait_seconds
        first_attempt = True
        while first_attempt or now() <= deadline:
            first_attempt = False
            with db() as conn:
                device = require_relay_device(conn)
                sweep(conn)
                incoming = (
                    "phone_to_node" if device["role"] == "node" else "node_to_phone"
                )
                row = conn.execute(
                    "SELECT * FROM relay_messages"
                    " WHERE mailbox_id=? AND direction=? ORDER BY created_at LIMIT 1",
                    (device["mailbox_id"], incoming),
                ).fetchone()
                if row:
                    return jsonify(
                        protocolVersion=RELAY_PROTOCOL_VERSION,
                        mailboxId=row["mailbox_id"],
                        messageId=row["message_id"],
                        direction=row["direction"],
                        expiresAt=int(row["expires_at"]),
                        nonce=row["nonce"],
                        ciphertext=row["ciphertext"],
                    )
            if wait_seconds == 0:
                break
            await asyncio.sleep(1.0)
        return "", 204

    @app.route("/v1/relay/ack", methods=["POST"])
    async def relay_message_ack():
        body = await request.get_json(force=True, silent=True) or {}
        message_id = _limited_text(
            body.get("messageId"), "messageId", 128, required=True
        )
        with db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            device = require_relay_device(conn)
            incoming = "phone_to_node" if device["role"] == "node" else "node_to_phone"
            cursor = conn.execute(
                "DELETE FROM relay_messages"
                " WHERE message_id=? AND mailbox_id=? AND direction=?",
                (message_id, device["mailbox_id"], incoming),
            )
            if cursor.rowcount != 1:
                raise RequestError("ERROR_RELAY_MESSAGE_NOT_FOUND", "message not found", 404)
        return jsonify(ok=True)

    @app.route("/v1/relay/status", methods=["GET"])
    async def relay_status():
        with db() as conn:
            device = require_relay_device(conn)
            peers = conn.execute(
                "SELECT id,role,name,last_seen FROM relay_devices"
                " WHERE mailbox_id=? AND revoked_at IS NULL ORDER BY role,name",
                (device["mailbox_id"],),
            ).fetchall()
            queued = conn.execute(
                "SELECT COUNT(*) FROM relay_messages WHERE mailbox_id=?",
                (device["mailbox_id"],),
            ).fetchone()[0]
        return jsonify(
            protocolVersion=RELAY_PROTOCOL_VERSION,
            mailboxId=device["mailbox_id"],
            role=device["role"],
            queued=queued,
            devices=[dict(peer) for peer in peers],
        )

    @app.route("/v1/stats", methods=["GET"])
    async def stats():
        require_api_key()
        with db() as conn:
            sweep(conn)
            task_counts = dict(
                conn.execute("SELECT status,COUNT(*) FROM tasks GROUP BY status").fetchall()
            )
            workers = []
            nodes = []
            timestamp = now()
            for row in conn.execute(
                "SELECT name,domains,types,app_version,device,solved,failed,last_seen FROM workers"
            ):
                item = dict(row)
                item["domains"] = json.loads(item["domains"] or "[]")
                item["types"] = json.loads(item["types"] or "[]")
                item["online"] = bool(
                    item["last_seen"] and timestamp - item["last_seen"] <= 75
                )
                workers.append(item)
            for row in conn.execute(
                "SELECT id,name,version,device,last_seen FROM nodes ORDER BY name"
            ):
                item = dict(row)
                item["online"] = bool(
                    item["last_seen"]
                    and timestamp - item["last_seen"] <= NODE_ONLINE_SECONDS
                )
                item["registrations"] = conn.execute(
                    "SELECT COUNT(*) FROM remote_registrations WHERE node_id=?", (item["id"],)
                ).fetchone()[0]
                nodes.append(item)
        return jsonify(
            protocolVersion=PROTOCOL_VERSION,
            tasks=task_counts,
            workers=workers,
            nodes=nodes,
        )

    @app.route("/healthz", methods=["GET"])
    async def healthz():
        with db() as conn:
            conn.execute("SELECT 1").fetchone()
        return jsonify(ok=True, protocolVersion=PROTOCOL_VERSION)

    return app


def _read_api_key(path: str | None) -> str | None:
    env_key = os.environ.get("CAPTCHAMESH_API_KEY")
    if path and env_key:
        raise SystemExit("use either CAPTCHAMESH_API_KEY or --api-key-file, not both")
    if path:
        key_path = Path(path).expanduser()
        key = key_path.read_text().strip()
        if not key:
            raise SystemExit("API key file is empty")
        return key
    return env_key.strip() if env_key else None


def _read_node_key(path: str | None) -> str | None:
    env_key = os.environ.get("CAPTCHAMESH_NODE_KEY")
    if path and env_key:
        raise SystemExit("use either CAPTCHAMESH_NODE_KEY or --node-key-file, not both")
    if path:
        key = Path(path).expanduser().read_text().strip()
        if not key:
            raise SystemExit("node key file is empty")
        return key
    return env_key.strip() if env_key else None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8890)
    parser.add_argument("--api-key-file")
    parser.add_argument("--node-key-file")
    args = parser.parse_args()
    api_key = _read_api_key(args.api_key_file)
    node_key = _read_node_key(args.node_key_file)
    if not api_key and args.host not in {"127.0.0.1", "::1", "localhost"}:
        parser.error("non-loopback listening requires CAPTCHAMESH_API_KEY or --api-key-file")
    make_app(api_key=api_key, node_key=node_key).run(host=args.host, port=args.port)


if __name__ == "__main__":
    main()
