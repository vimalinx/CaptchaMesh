"""End-to-end envelopes for CaptchaMesh's untrusted relay mode."""
from __future__ import annotations

import base64
import json
import os
import re
import time
import uuid
from hashlib import sha256
from hmac import digest as hmac_digest
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


PROTOCOL_VERSION = 1
PAIRING_SCHEME = "captchamesh"
PAIRING_HOST = "pair"
PAIR_SECRET_BYTES = 32
NONCE_BYTES = 12
DEFAULT_TTL_SECONDS = 600
MAX_TTL_SECONDS = 900
DIRECTIONS = {"node_to_phone", "phone_to_node"}
OPAQUE_ID_RE = re.compile(r"^[A-Za-z0-9._~-]{1,128}$")


class RelayProtocolError(ValueError):
    """Raised when a relay envelope or pairing payload is invalid."""


def b64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def b64url_decode(value: str, *, name: str, expected_bytes: int | None = None) -> bytes:
    if not isinstance(value, str) or not value or len(value) > 16 * 1024 * 1024:
        raise RelayProtocolError(f"{name} is invalid")
    try:
        decoded = base64.b64decode(
            value + "=" * (-len(value) % 4), altchars=b"-_", validate=True
        )
    except (ValueError, TypeError) as exc:
        raise RelayProtocolError(f"{name} is not base64url") from exc
    if expected_bytes is not None and len(decoded) != expected_bytes:
        raise RelayProtocolError(f"{name} must be {expected_bytes} bytes")
    return decoded


def generate_pair_secret() -> bytes:
    return os.urandom(PAIR_SECRET_BYTES)


def _opaque_id(value: Any, name: str) -> str:
    if not isinstance(value, str) or not OPAQUE_ID_RE.fullmatch(value):
        raise RelayProtocolError(f"{name} is invalid")
    return value


def _direction(value: Any) -> str:
    if value not in DIRECTIONS:
        raise RelayProtocolError("direction is invalid")
    return str(value)


def _hkdf_sha256(ikm: bytes, info: bytes, length: int = 32) -> bytes:
    if len(ikm) != PAIR_SECRET_BYTES:
        raise RelayProtocolError("pair secret must be 32 bytes")
    salt = b"CaptchaMesh relay v1\x00"
    prk = hmac_digest(salt, ikm, sha256)
    output = bytearray()
    previous = b""
    counter = 1
    while len(output) < length:
        previous = hmac_digest(prk, previous + info + bytes([counter]), sha256)
        output.extend(previous)
        counter += 1
    return bytes(output[:length])


def derive_direction_key(pair_secret: bytes, direction: str) -> bytes:
    clean_direction = _direction(direction)
    return _hkdf_sha256(pair_secret, b"message-key\x00" + clean_direction.encode("ascii"))


def envelope_aad(
    mailbox_id: str,
    message_id: str,
    direction: str,
    expires_at: int,
) -> bytes:
    clean_mailbox = _opaque_id(mailbox_id, "mailboxId")
    clean_message = _opaque_id(message_id, "messageId")
    clean_direction = _direction(direction)
    if not isinstance(expires_at, int) or isinstance(expires_at, bool) or expires_at <= 0:
        raise RelayProtocolError("expiresAt is invalid")
    return (
        f"captchamesh-relay-v1\n{clean_mailbox}\n{clean_message}\n"
        f"{clean_direction}\n{expires_at}"
    ).encode("utf-8")


def encrypt_payload(
    pair_secret: bytes,
    mailbox_id: str,
    direction: str,
    payload: dict[str, Any],
    *,
    message_id: str | None = None,
    expires_at: int | None = None,
    now_value: float | None = None,
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise RelayProtocolError("payload must be an object")
    timestamp = time.time() if now_value is None else now_value
    expiry = int(timestamp + DEFAULT_TTL_SECONDS) if expires_at is None else expires_at
    if expiry <= int(timestamp) or expiry > int(timestamp) + MAX_TTL_SECONDS:
        raise RelayProtocolError("expiresAt is outside the allowed window")
    identifier = message_id or ("msg-" + uuid.uuid4().hex)
    aad = envelope_aad(mailbox_id, identifier, direction, expiry)
    nonce = os.urandom(NONCE_BYTES)
    plaintext = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    ciphertext = AESGCM(derive_direction_key(pair_secret, direction)).encrypt(
        nonce, plaintext, aad
    )
    return {
        "protocolVersion": PROTOCOL_VERSION,
        "mailboxId": mailbox_id,
        "messageId": identifier,
        "direction": direction,
        "expiresAt": expiry,
        "nonce": b64url_encode(nonce),
        "ciphertext": b64url_encode(ciphertext),
    }


def decrypt_payload(
    pair_secret: bytes,
    envelope: dict[str, Any],
    *,
    expected_direction: str,
    now_value: float | None = None,
) -> dict[str, Any]:
    if not isinstance(envelope, dict):
        raise RelayProtocolError("envelope must be an object")
    if envelope.get("protocolVersion") != PROTOCOL_VERSION:
        raise RelayProtocolError("unsupported relay protocol")
    direction = _direction(envelope.get("direction"))
    if direction != expected_direction:
        raise RelayProtocolError("unexpected message direction")
    mailbox_id = _opaque_id(envelope.get("mailboxId"), "mailboxId")
    message_id = _opaque_id(envelope.get("messageId"), "messageId")
    expires_at = envelope.get("expiresAt")
    aad = envelope_aad(mailbox_id, message_id, direction, expires_at)
    timestamp = time.time() if now_value is None else now_value
    if expires_at <= int(timestamp):
        raise RelayProtocolError("relay message expired")
    nonce = b64url_decode(envelope.get("nonce"), name="nonce", expected_bytes=NONCE_BYTES)
    ciphertext = b64url_decode(envelope.get("ciphertext"), name="ciphertext")
    try:
        plaintext = AESGCM(derive_direction_key(pair_secret, direction)).decrypt(
            nonce, ciphertext, aad
        )
        payload = json.loads(plaintext)
    except Exception as exc:
        raise RelayProtocolError("relay message authentication failed") from exc
    if not isinstance(payload, dict):
        raise RelayProtocolError("relay payload must be an object")
    return payload


def validate_hub_url(value: str) -> str:
    if not isinstance(value, str):
        raise RelayProtocolError("hub URL is invalid")
    clean = value.strip().rstrip("/")
    parsed = urlparse(clean)
    local = parsed.hostname in {"127.0.0.1", "localhost", "::1"}
    if parsed.scheme != "https" and not (parsed.scheme == "http" and local):
        raise RelayProtocolError("hub URL must use HTTPS")
    try:
        port = parsed.port
    except ValueError as exc:
        raise RelayProtocolError("hub URL is invalid") from exc
    if (
        not parsed.netloc
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
        or (port is not None and not 1 <= port <= 65535)
        or (not local and port not in {None, 443})
    ):
        raise RelayProtocolError("hub URL is invalid")
    return clean


def build_pairing_uri(
    *,
    hub_url: str,
    mailbox_id: str,
    join_token: str,
    pair_secret: bytes,
    node_name: str,
) -> str:
    clean_hub = validate_hub_url(hub_url)
    clean_mailbox = _opaque_id(mailbox_id, "mailboxId")
    clean_join = _opaque_id(join_token, "joinToken")
    if len(pair_secret) != PAIR_SECRET_BYTES:
        raise RelayProtocolError("pair secret must be 32 bytes")
    if not isinstance(node_name, str) or not node_name.strip() or len(node_name) > 128:
        raise RelayProtocolError("node name is invalid")
    query = urlencode(
        {
            "v": str(PROTOCOL_VERSION),
            "hub": clean_hub,
            "mailbox": clean_mailbox,
            "join": clean_join,
            "secret": b64url_encode(pair_secret),
            "name": node_name.strip(),
        }
    )
    return f"{PAIRING_SCHEME}://{PAIRING_HOST}?{query}"


def parse_pairing_uri(uri: str) -> dict[str, Any]:
    if not isinstance(uri, str) or len(uri) > 4096:
        raise RelayProtocolError("pairing URI is invalid")
    parsed = urlparse(uri)
    if parsed.scheme != PAIRING_SCHEME or parsed.netloc != PAIRING_HOST:
        raise RelayProtocolError("pairing URI is invalid")
    values = parse_qs(parsed.query, strict_parsing=True)

    def one(name: str) -> str:
        found = values.get(name, [])
        if len(found) != 1:
            raise RelayProtocolError(f"pairing field {name} is invalid")
        return found[0]

    if one("v") != str(PROTOCOL_VERSION):
        raise RelayProtocolError("unsupported pairing protocol")
    node_name = one("name").strip()
    if not node_name or len(node_name) > 128:
        raise RelayProtocolError("node name is invalid")
    return {
        "protocolVersion": PROTOCOL_VERSION,
        "hub": validate_hub_url(one("hub")),
        "mailboxId": _opaque_id(one("mailbox"), "mailboxId"),
        "joinToken": _opaque_id(one("join"), "joinToken"),
        "pairSecret": b64url_decode(
            one("secret"), name="pairSecret", expected_bytes=PAIR_SECRET_BYTES
        ),
        "nodeName": node_name,
    }
