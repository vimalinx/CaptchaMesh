#!/usr/bin/env python3
"""One controlled registration run used to verify the manual phone flow."""
from __future__ import annotations

import os

from captchamesh_client import CaptchaMeshClient


def main() -> None:
    client = CaptchaMeshClient(run_id=os.environ["CAPTCHAMESH_RUN_ID"])
    solution = client.solve_turnstile(
        "https://example.com/",
        "1x00000000000000000000AA",
        mode="interactive",
        timeout_seconds=120,
    )
    if not solution.get("token"):
        raise RuntimeError("manual CAPTCHA did not return a token")


if __name__ == "__main__":
    main()
