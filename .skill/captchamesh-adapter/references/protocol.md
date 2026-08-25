# CaptchaMesh 的 2Captcha 兼容面

## 两种地址

| 模式 | API 地址 | Key 来源 | `runId` |
|---|---|---|---|
| Agent API（默认） | `http://127.0.0.1:8893` | `captchamesh config --json` 返回的受限文件 | 不使用 |
| 手机工作流（可选） | `CAPTCHAMESH_URL` | Node Agent 注入的 `CAPTCHAMESH_API_KEY` | 使用注入值 |

Agent API 通过电脑本地桥端到端加密后再访问 Hub。不要把普通 Agent 直接改到公共 Hub，也不要
要求用户先在 App 启动工作流。

## 能力矩阵

| 对外类型 | v1 提交 | v2 task.type | 手机返回 |
|---|---|---|---|
| reCAPTCHA v2 | `method=userrecaptcha` | `RecaptchaV2TaskProxyless` / `RecaptchaV2Task` | `gRecaptchaResponse` / token |
| reCAPTCHA v3 | `method=userrecaptcha&version=v3` | `RecaptchaV3TaskProxyless` | `gRecaptchaResponse` / token |
| hCaptcha | `method=hcaptcha` | `HCaptchaTaskProxyless` / `HCaptchaTask` | `gRecaptchaResponse` / token |
| Turnstile | `method=turnstile` | `TurnstileTaskProxyless` / `TurnstileTask` | token |
| 图片文字 | — | `ImageToTextTask` | `text` |
| 坐标 | — | `CoordinatesTask` | `coordinates[]` |
| 网格 | — | `GridTask` | `click[]` |
| 旋转 | — | `RotateTask` | `rotate` |
| FunCaptcha | — | `FunCaptchaTaskProxyless` / `FunCaptchaTask` | token |
| GeeTest v3/v4 | — | `GeeTestTaskProxyless` / `GeeTestTask` | v3 三字段 / v4 五字段 |
| DataDome | — | `DataDomeSliderTask` | Cookie |
| Amazon WAF | — | `AmazonTaskProxyless` / `AmazonTask` | voucher / existing token |

带代理 task 只接无认证 HTTP(S) `host:port`。CyberSiARA、reCAPTCHA Enterprise、`data-s`、
自定义 Google API domain、带认证代理、SOCKS 和 callback/pingback 必须返回显式错误。

## v1 与 v2

v1 使用 `/in.php`、`/res.php`，保留 `OK|<taskId>`、`CAPCHA_NOT_READY` 和 `json=1` 格式。
v2 使用 `/createTask`、`/getTaskResult`，错误保持 HTTP 200，并通过 `errorId`、`errorCode` 和
`errorDescription` 表达。图片资源只在短期 Worker 租约内读取，不持久化。

## 工作流绑定

绑定规则只用于手机工作流模式：显式 `runId` 优先；否则自动绑定唯一活动工作流。没有活动项
返回 `ERROR_NO_ACTIVE_RUN`，多个活动项返回 `ERROR_AMBIGUOUS_RUN`。Agent API 本地桥不使用
这些规则。
