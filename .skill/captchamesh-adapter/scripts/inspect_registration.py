#!/usr/bin/env python3
"""Inspect Agent or workflow sources for 2Captcha/CaptchaMesh integration signals.

The report contains paths, line numbers and classified signals only. It never
echoes source lines, environment values, API keys, cookies or CAPTCHA tokens.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


TEXT_SUFFIXES = {
    ".py", ".js", ".ts", ".mjs", ".cjs", ".jsx", ".tsx", ".json", ".yaml", ".yml",
    ".toml", ".ini", ".cfg", ".conf", ".env", ".example", ".md", ".sh",
}
IGNORED_PARTS = {".git", ".venv", "venv", "node_modules", "dist", "build", "__pycache__"}
MAX_FILE_BYTES = 2 * 1024 * 1024

SIGNALS = {
    "python_2captcha_sdk": re.compile(r"\b(?:from|import)\s+twocaptcha\b|\bTwoCaptcha\s*\("),
    "v1_submit": re.compile(r"(?:2captcha\.com|/in\.php\b)"),
    "v1_result": re.compile(r"/res\.php\b|\bCAPCHA_NOT_READY\b"),
    "v2_create": re.compile(r"/createTask\b|\bcreateTask\b"),
    "v2_result": re.compile(r"/getTaskResult\b|\bgetTaskResult\b"),
    "captchamesh": re.compile(r"mesh\.vimalinx\.com|CAPTCHAMESH_"),
    "hardcoded_2captcha_host": re.compile(r"(?:api\.)?2captcha\.com"),
}

SUPPORTED = {
    "recaptcha_v2": re.compile(r"userrecaptcha|RecaptchaV2Task|\.recaptcha\s*\("),
    "recaptcha_v3": re.compile(r"RecaptchaV3Task|version[^\n]{0,30}v3"),
    "hcaptcha": re.compile(r"method[^\n]{0,30}hcaptcha|HCaptchaTask|\.hcaptcha\s*\("),
    "turnstile": re.compile(r"method[^\n]{0,30}turnstile|TurnstileTask|\.turnstile\s*\("),
    "image_text": re.compile(r"ImageToTextTask|method[^\n]{0,30}base64", re.IGNORECASE),
    "coordinates": re.compile(r"CoordinatesTask|coordinatescaptcha", re.IGNORECASE),
    "grid": re.compile(r"GridTask|recaptcha[^\n]{0,30}(?:rows|cols)", re.IGNORECASE),
    "rotate": re.compile(r"RotateTask|rotatecaptcha", re.IGNORECASE),
    "funcaptcha": re.compile(r"FunCaptchaTask|funcaptcha", re.IGNORECASE),
    "geetest": re.compile(r"GeeTestTask|geetest", re.IGNORECASE),
    "datadome": re.compile(r"DataDomeSliderTask|datadome", re.IGNORECASE),
    "amazon_waf": re.compile(r"AmazonTask|amazon.?waf", re.IGNORECASE),
}

UNSUPPORTED = {
    "enterprise_or_data_s": re.compile(r"isEnterprise|enterprise[^\n]{0,20}(?:true|1)|recaptchaDataSValue|data-s"),
    "callback_or_pingback": re.compile(r"callbackUrl|pingback"),
    "authenticated_or_socks_proxy": re.compile(r"proxyLogin|proxyPassword|proxytype[^\n]{0,20}socks", re.IGNORECASE),
}


def candidate_files(targets: list[Path]):
    seen: set[Path] = set()
    for target in targets:
        paths = target.rglob("*") if target.is_dir() else [target]
        for path in paths:
            if not path.is_file() or path in seen or any(part in IGNORED_PARTS for part in path.parts):
                continue
            if path.suffix.lower() not in TEXT_SUFFIXES and path.name not in {"Dockerfile", "Makefile"}:
                continue
            try:
                if path.stat().st_size > MAX_FILE_BYTES:
                    continue
            except OSError:
                continue
            seen.add(path)
            yield path


def inspect(targets: list[Path], mode: str = "agent-api") -> dict[str, object]:
    findings: list[dict[str, object]] = []
    kinds: set[str] = set()
    supported: set[str] = set()
    unsupported: set[str] = set()
    scanned = 0
    for path in candidate_files(targets):
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        scanned += 1
        for line_number, line in enumerate(content.splitlines(), 1):
            matched = [name for name, pattern in SIGNALS.items() if pattern.search(line)]
            matched_supported = [name for name, pattern in SUPPORTED.items() if pattern.search(line)]
            matched_unsupported = [name for name, pattern in UNSUPPORTED.items() if pattern.search(line)]
            if matched or matched_supported or matched_unsupported:
                findings.append(
                    {
                        "path": str(path.resolve()),
                        "line": line_number,
                        "signals": sorted(set(matched + matched_supported + matched_unsupported)),
                    }
                )
            kinds.update(matched)
            supported.update(matched_supported)
            unsupported.update(matched_unsupported)

    protocols: list[str] = []
    if kinds & {"python_2captcha_sdk", "v1_submit", "v1_result"}:
        protocols.append("2captcha_v1")
    if kinds & {"v2_create", "v2_result"}:
        protocols.append("2captcha_v2")
    if "captchamesh" in kinds:
        protocols.append("captchamesh")

    actions: list[str] = []
    if mode == "agent-api":
        if "python_2captcha_sdk" in kinds:
            actions.append("Use captchamesh.TwoCaptcha or a loopback-capable client at http://127.0.0.1:8893.")
        elif "2captcha_v1" in protocols:
            actions.append("Replace the active v1 host with http://127.0.0.1:8893; preserve in.php/res.php parsing.")
        if "2captcha_v2" in protocols:
            actions.append("Replace the active v2 base URL with http://127.0.0.1:8893 and preserve JSON error handling.")
        if protocols and not unsupported:
            actions.append("Run captchamesh start, pair once, and test without starting an App workflow or passing runId.")
    else:
        actions.append("Read the endpoint, key and runId injected by Node Agent; do not hardcode them.")
        if "2captcha_v1" in protocols:
            actions.append("Preserve in.php/res.php parsing and pass the injected runId when workflows can overlap.")
        if "2captcha_v2" in protocols:
            actions.append("Preserve v2 JSON error handling and pass the injected runId when workflows can overlap.")
        if protocols and not unsupported:
            actions.append("Register one fixed command, start it from the App Workflows page, then test one task.")
    if "hardcoded_2captcha_host" in kinds:
        actions.append("Remove or make configurable every active 2captcha.com host before verification.")
    if unsupported:
        actions.append("Stop: unsupported transport or browser-context signals require an explicit design decision; do not enqueue them to the phone.")
    return {
        "integration_mode": mode,
        "scanned_files": scanned,
        "protocols": protocols,
        "supported_task_signals": sorted(supported),
        "unsupported_capability_signals": sorted(unsupported),
        "findings": findings,
        "recommended_actions": actions,
        "ready_for_supported_adapter": bool(protocols) and not bool(unsupported),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("targets", nargs="+", type=Path)
    parser.add_argument(
        "--mode",
        choices=("agent-api", "phone-workflow"),
        default="agent-api",
        help="integration direction; defaults to the local Agent API",
    )
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    args = parser.parse_args()
    missing = [str(path) for path in args.targets if not path.exists()]
    if missing:
        parser.error("not found: " + ", ".join(missing))
    report = inspect(args.targets, args.mode)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print("Protocols:", ", ".join(report["protocols"]) or "none detected")
        print("Supported signals:", ", ".join(report["supported_task_signals"]) or "none")
        print("Unsupported signals:", ", ".join(report["unsupported_capability_signals"]) or "none")
        for action in report["recommended_actions"]:
            print("-", action)
    return 0 if report["ready_for_supported_adapter"] else 2


if __name__ == "__main__":
    sys.exit(main())
