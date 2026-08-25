"""Shared CaptchaMesh v3 challenge and solution contracts.

The module is intentionally free of HTTP, database, and Android concerns.  It
keeps every producer and consumer on the same small set of typed results while
leaving sensitive browser context in the broker's in-memory envelope.
"""
from __future__ import annotations

from typing import Any


VISUAL_TYPES = {"image_text", "coordinates", "grid", "rotate"}
TOKEN_TYPES = {
    "turnstile",
    "hcaptcha",
    "recaptcha_v2",
    "recaptcha_v3",
    "webview",
    "funcaptcha",
}
SESSION_TYPES = {"geetest_v3", "geetest_v4", "datadome", "amazon_waf"}
SUPPORTED_TYPES = VISUAL_TYPES | TOKEN_TYPES | SESSION_TYPES

PRESENTATION_KIND = {
    "image_text": "image_text",
    "coordinates": "coordinates",
    "grid": "grid",
    "rotate": "rotate",
    "turnstile": "widget",
    "hcaptcha": "widget",
    "recaptcha_v2": "widget",
    "recaptcha_v3": "widget",
    "funcaptcha": "widget",
    "geetest_v3": "widget",
    "geetest_v4": "widget",
    "datadome": "widget",
    "amazon_waf": "widget",
    "webview": "webview",
}


class SolutionError(ValueError):
    pass


def _text(
    value: Any,
    name: str,
    *,
    minimum: int = 1,
    maximum: int = 20_000,
) -> str:
    if not isinstance(value, str):
        raise SolutionError(f"{name} must be a string")
    if not minimum <= len(value) <= maximum:
        raise SolutionError(f"{name} length must be between {minimum} and {maximum}")
    return value


def normalize_solution(
    task_type: str,
    task: dict[str, Any],
    solution: Any,
) -> dict[str, Any]:
    """Validate and return the canonical persisted solution for one task."""
    if not isinstance(solution, dict):
        raise SolutionError("solution must be an object")

    if task_type in TOKEN_TYPES:
        return {"token": _text(solution.get("token"), "solution.token", minimum=20)}

    if task_type == "image_text":
        presentation = task.get("presentation") or {}
        minimum = int(presentation.get("minLength", 1))
        maximum = int(presentation.get("maxLength", 1_024))
        text = _text(
                solution.get("text"),
                "solution.text",
                minimum=minimum,
                maximum=maximum,
            )
        numeric_mode = int(presentation.get("numericMode", 0))
        if numeric_mode == 1 and not text.isdigit():
            raise SolutionError("solution.text must contain only numbers")
        if numeric_mode == 2 and not text.isalpha():
            raise SolutionError("solution.text must contain only letters")
        if numeric_mode == 3 and not (text.isdigit() or text.isalpha()):
            raise SolutionError("solution.text must contain only numbers or only letters")
        if numeric_mode == 4 and not (
            any(character.isdigit() for character in text)
            and any(character.isalpha() for character in text)
        ):
            raise SolutionError("solution.text must contain both numbers and letters")
        if presentation.get("phrase") and len(text.split()) < 2:
            raise SolutionError("solution.text must contain at least two words")
        return {"text": text}

    if task_type == "coordinates":
        points = solution.get("coordinates", solution.get("points"))
        presentation = task.get("presentation") or {}
        minimum = int(presentation.get("minClicks", 1))
        maximum = int(presentation.get("maxClicks", 100))
        if not isinstance(points, list) or not minimum <= len(points) <= maximum:
            raise SolutionError(
                f"solution.coordinates must contain {minimum} to {maximum} points"
            )
        normalized: list[dict[str, int]] = []
        for point in points:
            if not isinstance(point, dict):
                raise SolutionError("each coordinate must be an object")
            x, y = point.get("x"), point.get("y")
            if any(isinstance(value, bool) or not isinstance(value, int) for value in (x, y)):
                raise SolutionError("coordinate x and y must be integers")
            if not 0 <= x <= 20_000 or not 0 <= y <= 20_000:
                raise SolutionError("coordinate x and y are outside the supported range")
            normalized.append({"x": x, "y": y})
        return {"coordinates": normalized}

    if task_type == "grid":
        clicks = solution.get("click", solution.get("cells"))
        presentation = task.get("presentation") or {}
        rows = int(presentation.get("rows", 0))
        columns = int(presentation.get("columns", 0))
        maximum = rows * columns
        minimum_clicks = int(presentation.get("minClicks", 1))
        maximum_clicks = int(presentation.get("maxClicks", maximum))
        if not isinstance(clicks, list) or not minimum_clicks <= len(clicks) <= maximum_clicks:
            raise SolutionError(
                f"solution.click must contain {minimum_clicks} to {maximum_clicks} cells"
            )
        if any(isinstance(value, bool) or not isinstance(value, int) for value in clicks):
            raise SolutionError("solution.click entries must be integers")
        if any(value < 1 or value > maximum for value in clicks):
            raise SolutionError("solution.click contains a cell outside the grid")
        if len(set(clicks)) != len(clicks):
            raise SolutionError("solution.click cannot contain duplicate cells")
        return {"click": clicks}

    if task_type == "rotate":
        angle = solution.get("rotate", solution.get("angle"))
        if isinstance(angle, bool) or not isinstance(angle, (int, float)):
            raise SolutionError("solution.rotate must be a number")
        if not 0 <= float(angle) <= 360:
            raise SolutionError("solution.rotate must be between 0 and 360")
        return {"rotate": angle}

    if task_type == "geetest_v3":
        return {
            "challenge": _text(solution.get("challenge"), "solution.challenge", maximum=4_096),
            "validate": _text(solution.get("validate"), "solution.validate", maximum=4_096),
            "seccode": _text(solution.get("seccode"), "solution.seccode", maximum=4_096),
        }

    if task_type == "geetest_v4":
        return {
            field: _text(solution.get(field), f"solution.{field}", maximum=8_192)
            for field in (
                "captcha_id",
                "lot_number",
                "pass_token",
                "gen_time",
                "captcha_output",
            )
        }

    if task_type == "datadome":
        return {"cookie": _text(solution.get("cookie"), "solution.cookie", maximum=16_384)}

    if task_type == "amazon_waf":
        return {
            "captcha_voucher": _text(
                solution.get("captcha_voucher"),
                "solution.captcha_voucher",
                maximum=20_000,
            ),
            "existing_token": _text(
                solution.get("existing_token", ""),
                "solution.existing_token",
                minimum=0,
                maximum=20_000,
            ),
        }

    raise SolutionError(f"unsupported CAPTCHA type: {task_type}")


def legacy_result(solution: dict[str, Any]) -> str:
    """Render the subset of typed results representable by 2Captcha v1."""
    if "token" in solution:
        return str(solution["token"])
    if "text" in solution:
        return str(solution["text"])
    if "coordinates" in solution:
        return ";".join(
            f"coordinates:x={point['x']},y={point['y']}" for point in solution["coordinates"]
        )
    if "click" in solution:
        return "click:" + "/".join(str(value) for value in solution["click"])
    if "rotate" in solution:
        return str(solution["rotate"])
    if "cookie" in solution:
        return str(solution["cookie"])
    if "captcha_voucher" in solution:
        return str(solution["captcha_voucher"])
    raise SolutionError("solution cannot be represented by the v1 result endpoint")
