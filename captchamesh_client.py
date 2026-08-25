#!/usr/bin/env python3
"""Python client for the CaptchaMesh v1 task protocol."""
from __future__ import annotations

import argparse
import base64
import os
import time
from typing import Any

import requests


class CaptchaMeshError(RuntimeError):
    """A broker, transport, or solve error."""

    def __init__(self, code: str, description: str):
        super().__init__(f"{code}: {description}")
        self.code = code
        self.description = description


class CaptchaMeshClient:
    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        request_timeout: float = 20,
        run_id: str | None = None,
    ) -> None:
        configured_url = base_url or os.environ.get("CAPTCHAMESH_URL") or "http://127.0.0.1:8890"
        self.base_url = configured_url.rstrip("/")
        self.api_key = api_key if api_key is not None else os.environ.get("CAPTCHAMESH_API_KEY")
        self.request_timeout = request_timeout
        self.run_id = run_id if run_id is not None else os.environ.get("CAPTCHAMESH_RUN_ID")
        self.session = requests.Session()

    @property
    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        headers = dict(self._headers)
        headers.update(kwargs.pop("headers", {}))
        try:
            response = self.session.request(
                method,
                self.base_url + path,
                headers=headers,
                timeout=kwargs.pop("timeout", self.request_timeout),
                **kwargs,
            )
            data = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise CaptchaMeshError("ERROR_TRANSPORT", str(exc)) from exc
        if response.status_code >= 400 or data.get("errorId"):
            raise CaptchaMeshError(
                data.get("errorCode", f"HTTP_{response.status_code}"),
                data.get("errorDescription", "broker request failed"),
            )
        return data

    def create_task(self, task: dict[str, Any]) -> str:
        payload = dict(task)
        if self.run_id:
            payload["runId"] = self.run_id
        return str(self._request("POST", "/v1/tasks", json=payload)["taskId"])

    def result(self, task_id: str) -> dict[str, Any]:
        return self._request("GET", f"/v1/tasks/{task_id}")

    def cancel(self, task_id: str) -> None:
        self._request("POST", f"/v1/tasks/{task_id}/cancel")

    def stats(self) -> dict[str, Any]:
        return self._request("GET", "/v1/stats")

    def solve(self, task: dict[str, Any], timeout: float | None = None) -> dict[str, Any]:
        task_timeout = int(task.get("timeoutSeconds", 120))
        wait_timeout = timeout if timeout is not None else task_timeout + 30
        task_id = self.create_task(task)
        deadline = time.monotonic() + wait_timeout
        try:
            while time.monotonic() < deadline:
                result = self.result(task_id)
                if result.get("status") == "ready":
                    return result["solution"]
                time.sleep(1.5)
        except BaseException:
            try:
                self.cancel(task_id)
            except CaptchaMeshError:
                pass
            raise
        try:
            self.cancel(task_id)
        except CaptchaMeshError:
            pass
        raise CaptchaMeshError("ERROR_CLIENT_TIMEOUT", "solve wait timed out")

    def solve_turnstile(
        self,
        website_url: str,
        website_key: str,
        *,
        action: str | None = None,
        cdata: str | None = None,
        chl_page_data: str | None = None,
        context: dict[str, Any] | None = None,
        mode: str = "auto",
        timeout_seconds: int = 120,
    ) -> dict[str, Any]:
        task: dict[str, Any] = {
            "type": "turnstile",
            "websiteURL": website_url,
            "websiteKey": website_key,
            "mode": mode,
            "timeoutSeconds": timeout_seconds,
        }
        for key, value in (("action", action), ("cData", cdata), ("chlPageData", chl_page_data)):
            if value is not None:
                task[key] = value
        if context:
            task["context"] = context
        return self.solve(task)

    def solve_hcaptcha(
        self,
        website_url: str,
        website_key: str,
        *,
        rqdata: str | None = None,
        context: dict[str, Any] | None = None,
        mode: str = "interactive",
        timeout_seconds: int = 120,
    ) -> dict[str, Any]:
        task: dict[str, Any] = {
            "type": "hcaptcha",
            "websiteURL": website_url,
            "websiteKey": website_key,
            "mode": mode,
            "timeoutSeconds": timeout_seconds,
        }
        if rqdata is not None:
            task["rqdata"] = rqdata
        if context:
            task["context"] = context
        return self.solve(task)

    def solve_recaptcha_v2(
        self,
        website_url: str,
        website_key: str,
        *,
        invisible: bool = False,
        context: dict[str, Any] | None = None,
        mode: str = "interactive",
        timeout_seconds: int = 120,
    ) -> dict[str, Any]:
        task: dict[str, Any] = {
            "type": "recaptcha_v2",
            "websiteURL": website_url,
            "websiteKey": website_key,
            "mode": mode,
            "timeoutSeconds": timeout_seconds,
            "isInvisible": invisible,
        }
        if context:
            task["context"] = context
        return self.solve(task)

    def solve_recaptcha_v3(
        self,
        website_url: str,
        website_key: str,
        action: str,
        *,
        context: dict[str, Any] | None = None,
        timeout_seconds: int = 120,
    ) -> dict[str, Any]:
        task: dict[str, Any] = {
            "type": "recaptcha_v3",
            "websiteURL": website_url,
            "websiteKey": website_key,
            "action": action,
            "mode": "auto",
            "timeoutSeconds": timeout_seconds,
        }
        if context:
            task["context"] = context
        return self.solve(task)

    def solve_webview(
        self,
        website_url: str,
        response_selector: str,
        *,
        response_property: str = "value",
        context: dict[str, Any] | None = None,
        mode: str = "interactive",
        timeout_seconds: int = 120,
    ) -> dict[str, Any]:
        task: dict[str, Any] = {
            "type": "webview",
            "websiteURL": website_url,
            "websiteKey": "",
            "responseSelector": response_selector,
            "responseProperty": response_property,
            "mode": mode,
            "timeoutSeconds": timeout_seconds,
        }
        if context:
            task["context"] = context
        return self.solve(task)

    @staticmethod
    def _visual_presentation(
        kind: str,
        image: bytes,
        *,
        media_type: str,
        prompt: str,
        **options: Any,
    ) -> dict[str, Any]:
        if not isinstance(image, bytes) or not image:
            raise ValueError("image must be non-empty bytes")
        return {
            "kind": kind,
            "image": {
                "data": base64.b64encode(image).decode("ascii"),
                "mediaType": media_type,
            },
            "prompt": prompt,
            **options,
        }

    def solve_image_text(
        self,
        image: bytes,
        *,
        prompt: str = "请输入图片中的文字",
        media_type: str = "image/png",
        min_length: int = 1,
        max_length: int = 1_024,
        case_sensitive: bool = False,
        numeric: bool = False,
        numeric_mode: int = 0,
        phrase: bool = False,
        math: bool = False,
        timeout_seconds: int = 180,
    ) -> dict[str, Any]:
        return self.solve(
            {
                "type": "image_text",
                "timeoutSeconds": timeout_seconds,
                "presentation": self._visual_presentation(
                    "image_text",
                    image,
                    media_type=media_type,
                    prompt=prompt,
                    minLength=min_length,
                    maxLength=max_length,
                    caseSensitive=case_sensitive,
                    numericMode=1 if numeric and numeric_mode == 0 else numeric_mode,
                    phrase=phrase,
                    math=math,
                ),
            }
        )

    def solve_coordinates(
        self,
        image: bytes,
        *,
        prompt: str = "请点选目标位置",
        media_type: str = "image/png",
        multiple: bool = True,
        min_clicks: int = 1,
        max_clicks: int = 100,
        timeout_seconds: int = 180,
    ) -> dict[str, Any]:
        return self.solve(
            {
                "type": "coordinates",
                "timeoutSeconds": timeout_seconds,
                "presentation": self._visual_presentation(
                    "coordinates",
                    image,
                    media_type=media_type,
                    prompt=prompt,
                    multiple=multiple,
                    minClicks=min_clicks,
                    maxClicks=max_clicks,
                ),
            }
        )

    def solve_grid(
        self,
        image: bytes,
        rows: int,
        columns: int,
        *,
        prompt: str = "请选择符合提示的格子",
        media_type: str = "image/png",
        min_clicks: int = 1,
        max_clicks: int | None = None,
        timeout_seconds: int = 180,
    ) -> dict[str, Any]:
        return self.solve(
            {
                "type": "grid",
                "timeoutSeconds": timeout_seconds,
                "presentation": self._visual_presentation(
                    "grid",
                    image,
                    media_type=media_type,
                    prompt=prompt,
                    rows=rows,
                    columns=columns,
                    multiple=True,
                    minClicks=min_clicks,
                    maxClicks=max_clicks or rows * columns,
                ),
            }
        )

    def solve_rotate(
        self,
        image: bytes,
        *,
        prompt: str = "请将图片旋转至正确方向",
        media_type: str = "image/png",
        angle_step: float = 1,
        timeout_seconds: int = 180,
    ) -> dict[str, Any]:
        return self.solve(
            {
                "type": "rotate",
                "timeoutSeconds": timeout_seconds,
                "presentation": self._visual_presentation(
                    "rotate",
                    image,
                    media_type=media_type,
                    prompt=prompt,
                    angleStep=angle_step,
                ),
            }
        )

    def solve_funcaptcha(
        self,
        website_url: str,
        public_key: str,
        *,
        api_subdomain: str | None = None,
        data: dict[str, Any] | str | None = None,
        context: dict[str, Any] | None = None,
        timeout_seconds: int = 180,
    ) -> dict[str, Any]:
        task: dict[str, Any] = {
            "type": "funcaptcha",
            "websiteURL": website_url,
            "websiteKey": public_key,
            "mode": "interactive",
            "timeoutSeconds": timeout_seconds,
        }
        if api_subdomain:
            task["funcaptchaApiJSSubdomain"] = api_subdomain
        if data is not None:
            task["data"] = data
        if context:
            task["context"] = context
        return self.solve(task)

    def solve_geetest_v3(
        self,
        website_url: str,
        gt: str,
        challenge: str,
        *,
        context: dict[str, Any] | None = None,
        timeout_seconds: int = 180,
    ) -> dict[str, Any]:
        task: dict[str, Any] = {
            "type": "geetest_v3",
            "websiteURL": website_url,
            "websiteKey": "",
            "gt": gt,
            "challenge": challenge,
            "mode": "interactive",
            "timeoutSeconds": timeout_seconds,
        }
        if context:
            task["context"] = context
        return self.solve(task)

    def solve_geetest_v4(
        self,
        website_url: str,
        captcha_id: str,
        *,
        risk_type: str | None = None,
        context: dict[str, Any] | None = None,
        timeout_seconds: int = 180,
    ) -> dict[str, Any]:
        task: dict[str, Any] = {
            "type": "geetest_v4",
            "websiteURL": website_url,
            "websiteKey": "",
            "captchaId": captcha_id,
            "mode": "interactive",
            "timeoutSeconds": timeout_seconds,
        }
        if risk_type:
            task["riskType"] = risk_type
        if context:
            task["context"] = context
        return self.solve(task)

    def solve_datadome(
        self,
        website_url: str,
        captcha_url: str,
        *,
        context: dict[str, Any],
        timeout_seconds: int = 180,
    ) -> dict[str, Any]:
        return self.solve(
            {
                "type": "datadome",
                "websiteURL": website_url,
                "websiteKey": "",
                "captchaUrl": captcha_url,
                "mode": "interactive",
                "timeoutSeconds": timeout_seconds,
                "context": context,
            }
        )

    def solve_amazon_waf(
        self,
        website_url: str,
        website_key: str,
        iv: str | None = None,
        aws_context: str | None = None,
        *,
        challenge_script: str | None = None,
        captcha_script: str | None = None,
        jsapi_script: str | None = None,
        context: dict[str, Any] | None = None,
        timeout_seconds: int = 180,
    ) -> dict[str, Any]:
        task: dict[str, Any] = {
            "type": "amazon_waf",
            "websiteURL": website_url,
            "websiteKey": website_key,
            "mode": "interactive",
            "timeoutSeconds": timeout_seconds,
        }
        if iv:
            task["iv"] = iv
        if aws_context:
            task["awsContext"] = aws_context
        if challenge_script:
            task["challengeScript"] = challenge_script
        if captcha_script:
            task["captchaScript"] = captcha_script
        if jsapi_script:
            task["jsapiScript"] = jsapi_script
        if context:
            task["context"] = context
        return self.solve(task)


def solve_turnstile(url: str, sitekey: str, **kwargs: Any) -> str:
    """Small registration-script helper returning just the token."""
    client_keys = {key: kwargs.pop(key) for key in ("base_url", "api_key") if key in kwargs}
    return str(CaptchaMeshClient(**client_keys).solve_turnstile(url, sitekey, **kwargs)["token"])


def main() -> None:
    parser = argparse.ArgumentParser(description="Submit one CaptchaMesh Turnstile task")
    parser.add_argument("url")
    parser.add_argument("sitekey")
    parser.add_argument("--broker", default=None)
    parser.add_argument("--api-key", default=None)
    parser.add_argument("--timeout", type=int, default=120)
    args = parser.parse_args()
    started = time.monotonic()
    solution = CaptchaMeshClient(args.broker, args.api_key).solve_turnstile(
        args.url, args.sitekey, timeout_seconds=args.timeout
    )
    print(
        f"ready provider={solution.get('provider', 'unknown')} "
        f"token_length={len(solution['token'])} elapsed={time.monotonic() - started:.1f}s"
    )


if __name__ == "__main__":
    main()
