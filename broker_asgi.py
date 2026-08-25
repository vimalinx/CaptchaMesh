"""Production ASGI entry point for the single-worker CaptchaMesh Hub."""
from __future__ import annotations

import os
from pathlib import Path

from broker import make_app


def _secret(path_env: str) -> str:
    path = os.environ.get(path_env, "").strip()
    if not path:
        raise RuntimeError(f"{path_env} is required")
    value = Path(path).read_text().strip()
    if not value:
        raise RuntimeError(f"{path_env} points to an empty file")
    return value


def _flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


app = make_app(
    api_key=_secret("CAPTCHAMESH_API_KEY_FILE"),
    node_key=_secret("CAPTCHAMESH_NODE_KEY_FILE"),
    allow_public_pairing=_flag("CAPTCHAMESH_ALLOW_PUBLIC_PAIRING"),
    allowed_hosts={
        value.strip().lower()
        for value in os.environ.get(
            "CAPTCHAMESH_ALLOWED_HOSTS", "localhost,127.0.0.1,::1"
        ).split(",")
        if value.strip()
    },
)
