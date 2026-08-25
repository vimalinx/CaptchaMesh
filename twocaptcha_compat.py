"""Translate supported 2Captcha API v1/v2 requests to CaptchaMesh tasks.

This module contains no HTTP or database code.  Keeping translation separate
makes it possible to test the compatibility contract without starting a Hub.
"""
from __future__ import annotations

from typing import Any


class TwoCaptchaCompatError(ValueError):
    def __init__(self, code: str, description: str):
        super().__init__(description)
        self.code = code
        self.description = description


TASK_TYPES: dict[str, tuple[str, bool]] = {
    "ImageToTextTask": ("image_text", False),
    "CoordinatesTask": ("coordinates", False),
    "GridTask": ("grid", False),
    "RotateTask": ("rotate", False),
    "RecaptchaV2TaskProxyless": ("recaptcha_v2", False),
    "RecaptchaV2Task": ("recaptcha_v2", True),
    "RecaptchaV3TaskProxyless": ("recaptcha_v3", False),
    "HCaptchaTaskProxyless": ("hcaptcha", False),
    "HCaptchaTask": ("hcaptcha", True),
    "TurnstileTaskProxyless": ("turnstile", False),
    "TurnstileTask": ("turnstile", True),
    "FunCaptchaTaskProxyless": ("funcaptcha", False),
    "FunCaptchaTask": ("funcaptcha", True),
    "GeeTestTaskProxyless": ("geetest_v3", False),
    "GeeTestTask": ("geetest_v3", True),
    "DataDomeSliderTask": ("datadome", True),
    "AmazonTaskProxyless": ("amazon_waf", False),
    "AmazonTask": ("amazon_waf", True),
}


def _text(value: Any, name: str, *, required: bool = False) -> str:
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise TwoCaptchaCompatError("ERROR_BAD_PARAMETERS", f"{name} must be a string")
    value = value.strip()
    if required and not value:
        raise TwoCaptchaCompatError("ERROR_BAD_PARAMETERS", f"{name} is required")
    return value


def _cookies(value: Any) -> list[dict[str, str]]:
    if value is None or value == "":
        return []
    source = _text(value, "task.cookies")
    cookies: list[dict[str, str]] = []
    for part in source.split(";"):
        part = part.strip()
        if not part:
            continue
        separator = "=" if "=" in part else ":" if ":" in part else ""
        if not separator:
            raise TwoCaptchaCompatError(
                "ERROR_BAD_PARAMETERS", "cookies must use name=value or name:value pairs"
            )
        name, cookie_value = part.split(separator, 1)
        name = name.strip()
        if not name:
            raise TwoCaptchaCompatError("ERROR_BAD_PARAMETERS", "cookie name is empty")
        cookies.append({"name": name, "value": cookie_value.strip(), "path": "/"})
    return cookies


def _proxy(task: dict[str, Any]) -> str:
    proxy_type = _text(task.get("proxyType"), "task.proxyType", required=True).lower()
    if proxy_type not in {"http", "https"}:
        raise TwoCaptchaCompatError(
            "ERROR_PROXY_FORMAT",
            "CaptchaMesh phone mode currently supports unauthenticated HTTP(S) proxies only",
        )
    address = _text(task.get("proxyAddress"), "task.proxyAddress", required=True)
    port = task.get("proxyPort")
    if isinstance(port, str) and port.isdigit():
        port = int(port)
    if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535:
        raise TwoCaptchaCompatError(
            "ERROR_PROXY_FORMAT", "task.proxyPort must be between 1 and 65535"
        )
    if task.get("proxyLogin") or task.get("proxyPassword"):
        raise TwoCaptchaCompatError(
            "ERROR_PROXY_FORMAT",
            "authenticated proxies are not supported by the Android WebView worker",
        )
    if any(character in address for character in "/:@?#"):
        raise TwoCaptchaCompatError("ERROR_PROXY_FORMAT", "task.proxyAddress is invalid")
    return f"{proxy_type}://{address}:{port}"


def translate_create_request(body: Any) -> tuple[dict[str, Any], str]:
    """Return ``(CaptchaMesh task, original 2Captcha type)``."""
    if not isinstance(body, dict):
        raise TwoCaptchaCompatError("ERROR_BAD_PARAMETERS", "request body must be an object")
    if body.get("callbackUrl"):
        raise TwoCaptchaCompatError(
            "ERROR_CALLBACK_NOT_SUPPORTED",
            "callbackUrl is not supported; poll getTaskResult instead",
        )
    task = body.get("task")
    if not isinstance(task, dict):
        raise TwoCaptchaCompatError("ERROR_BAD_PARAMETERS", "task must be an object")
    external_type = _text(task.get("type"), "task.type", required=True)
    mapped = TASK_TYPES.get(external_type)
    if mapped is None:
        raise TwoCaptchaCompatError(
            "ERROR_TASK_NOT_SUPPORTED", f"unsupported 2Captcha task type: {external_type}"
        )
    internal_type, proxy_required = mapped
    if internal_type == "geetest_v3" and task.get("version") in {4, "4"}:
        internal_type = "geetest_v4"

    if internal_type in {"image_text", "coordinates", "grid", "rotate"}:
        presentation: dict[str, Any] = {
            "kind": internal_type,
            "image": {
                "data": _text(task.get("body"), "task.body", required=True),
                "mediaType": _text(task.get("mediaType"), "task.mediaType") or "image/png",
            },
            "prompt": _text(task.get("comment"), "task.comment"),
        }
        instruction = _text(task.get("imgInstructions"), "task.imgInstructions")
        if instruction:
            presentation["instructionImage"] = {
                "data": instruction,
                "mediaType": "image/png",
            }
        if internal_type == "image_text":
            numeric = task.get("numeric", 0)
            if isinstance(numeric, str) and numeric.isdigit():
                numeric = int(numeric)
            if isinstance(numeric, bool) or not isinstance(numeric, int) or numeric not in range(5):
                raise TwoCaptchaCompatError(
                    "ERROR_BAD_PARAMETERS", "task.numeric must be an integer from 0 through 4"
                )
            minimum = task.get("minLength", 0)
            maximum = task.get("maxLength", 0)
            presentation.update(
                minLength=minimum or 1,
                maxLength=maximum or 1_024,
                caseSensitive=bool(task.get("case")),
                phrase=bool(task.get("phrase")),
                numericMode=numeric,
                math=bool(task.get("math")),
            )
        elif internal_type == "coordinates":
            presentation["multiple"] = True
            presentation["minClicks"] = task.get("minClicks", 1)
            presentation["maxClicks"] = task.get("maxClicks", 100)
        elif internal_type == "grid":
            presentation.update(
                rows=task.get("rows"),
                columns=task.get("columns"),
                multiple=True,
                minClicks=task.get("minClicks", 1),
                maxClicks=task.get("maxClicks"),
            )
        elif internal_type == "rotate":
            presentation["angleStep"] = task.get("angle", 1)
        translated = {
            "type": internal_type,
            "websiteURL": task.get("websiteURL") or "https://manual.captchamesh.invalid/",
            "websiteKey": "",
            "mode": "interactive",
            "timeoutSeconds": 180,
            "presentation": presentation,
        }
        run_id = body.get("runId", task.get("runId"))
        if run_id is not None:
            translated["runId"] = run_id
        return translated, external_type

    if internal_type.startswith("recaptcha") and task.get("isEnterprise"):
        raise TwoCaptchaCompatError(
            "ERROR_TASK_NOT_SUPPORTED", "reCAPTCHA Enterprise is not supported yet"
        )
    api_domain = _text(task.get("apiDomain"), "task.apiDomain")
    if api_domain and api_domain not in {"google.com", "www.google.com"}:
        raise TwoCaptchaCompatError(
            "ERROR_TASK_NOT_SUPPORTED", "custom reCAPTCHA apiDomain is not supported yet"
        )
    if task.get("recaptchaDataSValue"):
        raise TwoCaptchaCompatError(
            "ERROR_TASK_NOT_SUPPORTED", "recaptchaDataSValue is not supported yet"
        )

    website_key = task.get("websiteKey")
    if internal_type == "funcaptcha":
        website_key = task.get("websitePublicKey")
    translated: dict[str, Any] = {
        "type": internal_type,
        "websiteURL": task.get("websiteURL"),
        "websiteKey": website_key,
        "mode": "auto" if internal_type == "recaptcha_v3" else "interactive",
        "timeoutSeconds": 180,
    }
    context: dict[str, Any] = {}
    user_agent = _text(task.get("userAgent"), "task.userAgent")
    if user_agent:
        context["userAgent"] = user_agent
    cookies = _cookies(task.get("cookies"))
    if cookies:
        context["cookies"] = cookies
    if proxy_required:
        context["proxy"] = _proxy(task)

    if internal_type == "recaptcha_v2":
        translated["isInvisible"] = bool(task.get("isInvisible", False))
    elif internal_type == "recaptcha_v3":
        action = task.get("pageAction", task.get("action"))
        translated["action"] = action
        translated["isInvisible"] = True
    elif internal_type == "hcaptcha":
        enterprise_payload = task.get("enterprisePayload")
        rqdata = task.get("rqdata")
        if rqdata is None and isinstance(enterprise_payload, dict):
            rqdata = enterprise_payload.get("rqdata")
        if rqdata is not None:
            translated["rqdata"] = rqdata
        translated["isInvisible"] = bool(task.get("isInvisible", False))
    elif internal_type == "turnstile":
        for source, target in (("action", "action"), ("data", "cData"), ("pagedata", "chlPageData")):
            if task.get(source) is not None:
                translated[target] = task[source]
        translated["isInvisible"] = False
    elif internal_type == "funcaptcha":
        for source, target in (
            ("funcaptchaApiJSSubdomain", "funcaptchaApiJSSubdomain"),
            ("data", "data"),
        ):
            if task.get(source) is not None:
                translated[target] = task[source]
        translated["isInvisible"] = False
    elif internal_type == "geetest_v3":
        for field in ("gt", "challenge", "geetestApiServerSubdomain"):
            if task.get(field) is not None:
                translated[field] = task[field]
        translated["websiteKey"] = ""
    elif internal_type == "geetest_v4":
        init_parameters = task.get("initParameters")
        if not isinstance(init_parameters, dict):
            raise TwoCaptchaCompatError(
                "ERROR_BAD_PARAMETERS", "task.initParameters must be an object for GeeTest v4"
            )
        translated["captchaId"] = init_parameters.get("captcha_id")
        if task.get("risk_type") is not None:
            translated["riskType"] = task["risk_type"]
        translated["websiteKey"] = ""
    elif internal_type == "datadome":
        translated["captchaUrl"] = task.get("captchaUrl")
        translated["websiteKey"] = ""
    elif internal_type == "amazon_waf":
        translated["iv"] = task.get("iv")
        translated["awsContext"] = task.get("context")
        for field in ("challengeScript", "captchaScript", "jsapiScript"):
            if task.get(field) is not None:
                translated[field] = task[field]

    run_id = body.get("runId", task.get("runId"))
    if run_id is not None:
        translated["runId"] = run_id
    if context:
        translated["context"] = context
    return translated, external_type


def _enabled(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _v1_proxy(params: dict[str, Any]) -> tuple[bool, str]:
    raw = _text(params.get("proxy"), "proxy")
    proxy_type = _text(params.get("proxytype"), "proxytype").lower()
    if not raw and not proxy_type:
        return False, ""
    if not raw or proxy_type not in {"http", "https"} or "@" in raw:
        raise TwoCaptchaCompatError(
            "ERROR_PROXY_FORMAT",
            "CaptchaMesh phone mode supports unauthenticated HTTP(S) host:port proxies only",
        )
    host, separator, port_text = raw.rpartition(":")
    if not separator or not host or not port_text.isdigit():
        raise TwoCaptchaCompatError("ERROR_PROXY_FORMAT", "proxy must use host:port")
    port = int(port_text)
    if not 1 <= port <= 65535 or any(character in host for character in "/:@?#"):
        raise TwoCaptchaCompatError("ERROR_PROXY_FORMAT", "proxy host or port is invalid")
    return True, f"{proxy_type}://{host}:{port}"


def translate_v1_request(params: Any) -> tuple[dict[str, Any], str]:
    """Translate a legacy ``in.php`` submission into a CaptchaMesh task."""
    if not isinstance(params, dict):
        raise TwoCaptchaCompatError("ERROR_BAD_PARAMETERS", "parameters must be an object")
    if params.get("pingback"):
        raise TwoCaptchaCompatError(
            "ERROR_CALLBACK_NOT_SUPPORTED",
            "pingback is disabled; poll res.php instead",
        )

    method = _text(params.get("method"), "method", required=True).lower()
    page_url = _text(params.get("pageurl"), "pageurl", required=True)
    site_key = _text(
        params.get("googlekey") if method == "userrecaptcha" else params.get("sitekey"),
        "sitekey",
        required=True,
    )
    proxy_required, proxy_url = _v1_proxy(params)

    if method == "userrecaptcha":
        if _enabled(params.get("enterprise")) or params.get("data-s"):
            raise TwoCaptchaCompatError(
                "ERROR_TASK_NOT_SUPPORTED",
                "reCAPTCHA Enterprise and data-s are not supported by the phone worker",
            )
        is_v3 = _text(params.get("version"), "version").lower() == "v3"
        external_type = "RecaptchaV3TaskProxyless" if is_v3 else (
            "RecaptchaV2Task" if proxy_required else "RecaptchaV2TaskProxyless"
        )
        internal_type = "recaptcha_v3" if is_v3 else "recaptcha_v2"
    elif method == "hcaptcha":
        external_type = "HCaptchaTask" if proxy_required else "HCaptchaTaskProxyless"
        internal_type = "hcaptcha"
    elif method == "turnstile":
        external_type = "TurnstileTask" if proxy_required else "TurnstileTaskProxyless"
        internal_type = "turnstile"
    else:
        raise TwoCaptchaCompatError(
            "ERROR_TASK_NOT_SUPPORTED",
            f"unsupported 2Captcha v1 method: {method}",
        )

    translated: dict[str, Any] = {
        "type": internal_type,
        "websiteURL": page_url,
        "websiteKey": site_key,
        "mode": "auto" if internal_type == "recaptcha_v3" else "interactive",
        "timeoutSeconds": 180,
        "isInvisible": _enabled(params.get("invisible")) or internal_type == "recaptcha_v3",
    }
    context: dict[str, Any] = {}
    user_agent = _text(params.get("userAgent"), "userAgent")
    if user_agent:
        context["userAgent"] = user_agent
    cookies = _cookies(params.get("cookies"))
    if cookies:
        context["cookies"] = cookies
    if proxy_required:
        context["proxy"] = proxy_url

    if internal_type == "recaptcha_v3":
        translated["action"] = _text(params.get("action"), "action") or "verify"
    elif internal_type == "hcaptcha" and params.get("data") is not None:
        translated["rqdata"] = params["data"]
    elif internal_type == "turnstile":
        for source, target in (("action", "action"), ("data", "cData"), ("pagedata", "chlPageData")):
            if params.get(source) is not None:
                translated[target] = params[source]

    run_id = params.get("runId") or params.get("run_id")
    if run_id is not None:
        translated["runId"] = run_id
    if context:
        translated["context"] = context
    return translated, external_type


def translate_solution(external_type: str, solution: dict[str, Any]) -> dict[str, Any]:
    if external_type.startswith(("Recaptcha", "HCaptcha")):
        token = str(solution.get("token", ""))
        return {"gRecaptchaResponse": token, "token": token}
    if external_type.startswith("GeeTestTask"):
        if "captcha_output" in solution:
            return dict(solution)
        return {
            "challenge": solution.get("challenge", ""),
            "validate": solution.get("validate", ""),
            "seccode": solution.get("seccode", ""),
        }
    return dict(solution)
