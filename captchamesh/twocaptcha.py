"""Drop-in 2captcha-python client transport for the local HTTP bridge."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests
from twocaptcha import TwoCaptcha as UpstreamTwoCaptcha
from twocaptcha.exceptions.api import ApiException, NetworkException


def _default_key_file() -> Path:
    state = os.environ.get("CAPTCHAMESH_STATE_FILE")
    if state:
        return Path(state).expanduser().parent / "local-api.key"
    config_home = Path(
        os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))
    ).expanduser()
    global_root = config_home / "captchamesh"
    if (global_root / "relay-pairing.json").is_file():
        return global_root / "local-api.key"
    project_key = Path.cwd() / ".secrets" / "local-api.key"
    if project_key.is_file():
        return project_key
    return global_root / "local-api.key"


def _local_key(api_key: str | None) -> str:
    if api_key:
        return api_key
    environment = os.environ.get("CAPTCHAMESH_LOCAL_API_KEY", "").strip()
    if environment:
        return environment
    key_file = _default_key_file()
    try:
        value = key_file.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise RuntimeError(
            "CaptchaMesh 本机 Key 不存在，请先运行 `captchamesh start`"
        ) from exc
    if not value:
        raise RuntimeError("CaptchaMesh 本机 Key 文件为空")
    return value


class LocalApiClient:
    def __init__(self, endpoint: str, *, timeout: float = 30) -> None:
        endpoint = endpoint.rstrip("/")
        parsed = urlparse(endpoint)
        if (
            parsed.scheme != "http"
            or parsed.hostname not in {"127.0.0.1", "::1", "localhost"}
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("2Captcha 本地适配器只允许连接 loopback HTTP 地址")
        self.endpoint = endpoint
        self.timeout = timeout

    @staticmethod
    def _decode(response: requests.Response) -> str:
        if response.status_code != 200:
            raise NetworkException(f"bad response: {response.status_code}")
        value = response.content.decode("utf-8")
        if "ERROR" in value:
            raise ApiException(value)
        return value

    def in_(self, files: dict[str, str] | None = None, **kwargs: Any) -> str:
        opened: dict[str, Any] = {}
        try:
            if files:
                opened = {name: open(path, "rb") for name, path in files.items()}
            response = requests.post(
                self.endpoint + "/in.php",
                data=kwargs,
                files=opened or None,
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise NetworkException(exc) from exc
        finally:
            for file in opened.values():
                file.close()
        return self._decode(response)

    def res(self, **kwargs: Any) -> str:
        try:
            response = requests.get(
                self.endpoint + "/res.php", params=kwargs, timeout=self.timeout
            )
        except requests.RequestException as exc:
            raise NetworkException(exc) from exc
        return self._decode(response)


class TwoCaptcha(UpstreamTwoCaptcha):
    """The upstream SDK API with its transport redirected to CaptchaMesh."""

    def __init__(
        self,
        apiKey: str | None = None,
        *,
        endpoint: str | None = None,
        **kwargs: Any,
    ) -> None:
        configured_server = kwargs.pop("server", None)
        if endpoint is None and configured_server:
            endpoint = str(configured_server)
            if "://" not in endpoint:
                endpoint = "http://" + endpoint
        super().__init__(apiKey=_local_key(apiKey), server="invalid.local", **kwargs)
        self.api_client = LocalApiClient(
            endpoint or os.environ.get("CAPTCHAMESH_LOCAL_URL", "http://127.0.0.1:8893")
        )
