# CaptchaMesh 的 2Captcha 兼容面

## 服务地址

- Hub：从 `CAPTCHAMESH_URL` / `CAPTCHAMESH_SERVER` 读取；公共默认值为 `https://mesh.vimalinx.com`
- v1：`<Hub>/in.php`、`<Hub>/res.php`
- v2：`<Hub>/createTask`、`getTaskResult`、`getBalance`、`reportCorrect`、`reportIncorrect`
- API key：使用用户手机本地保存、Hub 配置的 CaptchaMesh key；代码只从 `CAPTCHAMESH_API_KEY` 等受限配置读取。

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
| Amazon WAF | — | `AmazonTaskProxyless` / `AmazonTask`；`jsapiScript` 或 fresh iv/context + 双脚本 | voucher / existing token |

带代理 task 目前只接无认证 HTTP(S) `host:port`。代理上下文在内存中短暂传给手机，不持久化。

图片和交互题型目前走 v2；不要把它们降级成 v1 token。以下能力仍必须返回显式错误而不是入队：CyberSiARA、reCAPTCHA Enterprise、`data-s`、自定义 Google API domain、带认证代理、SOCKS、callback/pingback。

图片资源不写入 SQLite，也不内嵌在手机轮询 JSON。Hub 解码后生成随机 `assetId`，仅持有任务租约的 Worker 能从 `/v1/assets/{assetId}` 读取；任务终止、过期或 Hub 重启后资源立即失效。

## v1 线协议

提交支持 GET/表单 POST，成功为 `OK|<数字任务ID>`；`json=1` 时为：

```json
{"status": 1, "request": "123"}
```

轮询 `res.php?action=get&id=123`：未完成返回 `CAPCHA_NOT_READY`，成功返回 `OK|<token>`。`action=get2` 追加成本字段；`getbalance` 返回合成正余额，供会先检查余额的 SDK 正常启动；`reportgood`、`reportbad` 记录本地反馈。`header_acao=1` 添加 CORS 响应头。

支持的常用映射：

| v1 参数 | 内部字段 |
|---|---|
| `pageurl` | `websiteURL` |
| `googlekey` / `sitekey` | `websiteKey` |
| `action` | reCAPTCHA v3 / Turnstile action |
| `data` | hCaptcha rqdata / Turnstile cData |
| `pagedata` | Turnstile chlPageData |
| `cookies` | 临时浏览器 cookie |
| `userAgent` | 临时 WebView User-Agent |
| `proxy` + `proxytype` | 临时 HTTP(S) 代理 |
| `runId` / `run_id` | CaptchaMesh 并发注册扩展 |

## v2 线协议

`createTask` 接受标准 `clientKey`、`task`，返回数字 `taskId`。`getTaskResult` 返回 `processing` 或 `ready`；错误保持 HTTP 200，并用 `errorId`、`errorCode`、`errorDescription` 表达。CaptchaMesh 扩展允许在请求顶层或 task 内传 `runId`。

## 任务绑定规则

App 先启动一个注册机 run，Hub 收到 SDK 任务时自动绑定唯一活动 run。没有活动 run 返回 `ERROR_NO_ACTIVE_RUN`；同时存在多个活动 run 返回 `ERROR_AMBIGUOUS_RUN`，此时注册机必须显式传 CaptchaMesh `runId`。

轮询是当前稳定配置。`callbackUrl`、v1 `pingback` 与 pingback 管理动作均返回 `ERROR_CALLBACK_NOT_SUPPORTED`，避免 Hub 被利用向任意内网地址发请求。
