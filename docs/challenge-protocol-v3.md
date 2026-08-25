# CaptchaMesh challenge protocol v3

v3 keeps the existing run binding, Worker lease and manual-start lifecycle, but separates how a
challenge is presented from how its answer is returned.

## Task envelope

`POST /v1/tasks` accepts the existing widget fields plus a typed `presentation`. Visual tasks may
omit `websiteURL`; Hub assigns a non-routable `manual.captchamesh.invalid` URL for domain filtering.

```json
{
  "type": "grid",
  "runId": "RUN_ID",
  "timeoutSeconds": 180,
  "presentation": {
    "kind": "grid",
    "prompt": "请选择所有自行车",
    "image": {
      "data": "BASE64_IMAGE",
      "mediaType": "image/png"
    },
    "rows": 3,
    "columns": 3,
    "multiple": true
  }
}
```

Hub decodes each image, enforces a 5 MiB limit and replaces inline bytes with a descriptor before
the task is persisted or leased:

```json
{
  "kind": "grid",
  "image": {
    "assetId": "a-RANDOM",
    "mediaType": "image/png",
    "byteLength": 12345
  },
  "rows": 3,
  "columns": 3
}
```

Only the Worker currently leasing the associated task can read
`GET /v1/assets/{assetId}` using `Authorization: Worker <token>`. Responses use `Cache-Control:
no-store`. Assets are memory-only and are deleted when the task succeeds, fails, expires or is
cancelled.

## Presentation kinds and solutions

| `task.type` / `presentation.kind` | Required task data | Worker solution |
|---|---|---|
| `image_text` | image; optional prompt, length, phrase and numeric-mode rules | `{"text":"..."}` |
| `coordinates` | image; optional min/max click bounds | `{"coordinates":[{"x":1,"y":2}]}` |
| `grid` | image, rows, columns; optional min/max click bounds | `{"click":[1,5,9]}` |
| `rotate` | image; optional angle step | `{"rotate":42}` |
| `widget` token families | URL, public key and provider fields | `{"token":"..."}` |
| `geetest_v3` | URL, `gt`, fresh `challenge` | challenge, validate, seccode |
| `geetest_v4` | URL, captcha ID; optional risk type | captcha ID, lot number, pass token, generation time, output |
| `datadome` | URL, trusted captcha URL, proxy and matching UA | `{"cookie":"datadome=..."}` |
| `amazon_waf` | URL/key plus trusted `jsapiScript`, or fresh iv/context and both trusted interstitial scripts | captcha voucher and existing token |
| `webview` | URL and response selector | `{"token":"..."}` |

Coordinates are expressed in original image pixels. Grid indexes are one-based and row-major,
matching 2Captcha v2. Rotate values are degrees from 0 through 360.

Amazon WAF supports both script families documented by 2Captcha. `jsapiScript` uses the public
`AwsWafCaptcha.renderCaptcha` integration. The interstitial family builds `window.gokuProps` from
the fresh key/iv/context, obtains the current `ChallengeScript` token, and returns that token with
the voucher produced by `CaptchaScript`. CaptchaMesh requires the concrete script URLs for the
interstitial family instead of guessing a tenant- and region-specific AWS URL.

## Mobile rendering

`image_text`, `coordinates`, `grid` and `rotate` use Android native controls. The controls provide
48dp touch targets, visible selected state, clear/undo actions and a non-drag alternative for
rotation.

Token and provider-session tasks load a minimal local challenge shell whose base URL is the target
origin, then inject only the validated provider script. The target site's full page is opened only
for the explicit legacy `webview` type.

## Security and failure semantics

- Tasks are accepted only inside the existing user-started run lifecycle; compatibility requests
  still require exactly one active run unless they provide `runId`.
- Provider URLs are HTTPS and host-restricted: Arkose, GeeTest, DataDome and AWS WAF hosts only.
- Context and assets are process-memory-only. Restarting Hub fails dependent pending/leased tasks
  with `ERROR_CONTEXT_LOST`.
- Worker submissions are validated against the leased task type before persistence. A token cannot
  stand in for coordinates, an angle or a provider cookie.
- Callback/pingback, SOCKS, authenticated proxy and arbitrary command delivery remain disabled.
