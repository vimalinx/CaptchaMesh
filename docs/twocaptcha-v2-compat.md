# 2Captcha API v1/v2 兼容层

推荐通过电脑端本地加密桥接入：Agent 连接 `http://127.0.0.1:8893`，任务在电脑本地加密后
再发送到 Hub。安装、配对和 Python SDK 适配方式见 [`local-bridge.md`](./local-bridge.md)。

下面的 Hub 直连接口用于旧客户端或明确选择信任自建 Hub 的场景。

CaptchaMesh Hub 同时兼容现成 SDK 常用的 API v1 表单协议和 API v2 JSON 协议。已有程序可以
保留任务提交、轮询、错误解析和反馈结构，只把服务地址改为 CaptchaMesh Hub，并使用
CaptchaMesh API Key。

兼容端点：

```text
GET|POST /in.php
GET|POST /res.php
POST /createTask
POST /getTaskResult
POST /getBalance
POST /reportCorrect
POST /reportIncorrect
```

v1 使用表单或查询参数 `key`。v2 使用 JSON 内嵌鉴权：

```json
{"clientKey": "CAPTCHAMESH_API_KEY", "task": {}}
```

兼容端点不要求 CaptchaMesh 原生接口使用的 `Authorization: Bearer` Header。

## 现有 SDK 直接接入（v1）

官方 `2captcha-python` SDK 走 `/in.php`、`/res.php`，可只改 `server`：

```python
import os
from twocaptcha import TwoCaptcha

solver = TwoCaptcha(
    os.environ["CAPTCHAMESH_API_KEY"],
    server="mesh.vimalinx.com",
)
```

`server` 只填主机名，不带 `https://`。v1 提交成功返回 `OK|<数字任务ID>`；查询未完成返回
`CAPCHA_NOT_READY`，完成后返回 `OK|<token>`。支持 `json=1`、`header_acao=1`、`get2`、
`getbalance`、`reportgood` 和 `reportbad`。余额纯文本响应直接是数字，反馈响应为
`OK_REPORT_RECORDED`。`method=userrecaptcha`、`hcaptcha`、`turnstile`
会映射到下表中的手机 token 任务。

图片文字、坐标、网格、旋转及复杂组件题型使用 v2 JSON；当前不伪装成 v1 token 结果。

## 使用流程

先在手机 CaptchaMesh 的“注册机”页启动一个注册机。注册机随后可以提交标准 v2 请求：

```bash
curl -sS https://mesh.example.com/createTask \
  -H 'Content-Type: application/json' \
  --data-binary @- <<'JSON'
{
  "clientKey": "YOUR_CAPTCHAMESH_KEY",
  "task": {
    "type": "RecaptchaV2TaskProxyless",
    "websiteURL": "https://target.example/register",
    "websiteKey": "PUBLIC_SITE_KEY",
    "isInvisible": false
  }
}
JSON
```

Hub 返回数字 `taskId`。轮询：

```bash
curl -sS https://mesh.example.com/getTaskResult \
  -H 'Content-Type: application/json' \
  --data-binary @- <<'JSON'
{"clientKey":"YOUR_CAPTCHAMESH_KEY","taskId":1}
JSON
```

处理中：

```json
{"errorId":0,"status":"processing"}
```

手机完成后，reCAPTCHA/hCaptcha 返回：

```json
{
  "errorId": 0,
  "status": "ready",
  "solution": {
    "gRecaptchaResponse": "TOKEN",
    "token": "TOKEN"
  }
}
```

Turnstile 的 `solution` 为 `{"token":"TOKEN"}`。

## 运行绑定

标准 2Captcha 请求没有 CaptchaMesh 的 `runId`。为了保持“手机手动选择一个注册机后才接题”
的产品约束，兼容层按以下顺序绑定：

1. 请求顶层或 `task` 中存在扩展字段 `runId` 时，绑定该运行。
2. 未提供时，自动绑定 Hub 中唯一一个活动注册运行。
3. 没有活动运行，返回 `ERROR_NO_ACTIVE_RUN`。
4. 同时存在多个活动运行，返回 `ERROR_AMBIGUOUS_RUN`，调用方必须显式传 `runId`。

因此，从手机列表启动的普通单注册机流程不需要修改任务 JSON。并发运行多个注册机时才需要
CaptchaMesh 扩展字段。

## 支持范围

| 2Captcha task.type | 手机任务 |
|---|---|
| `RecaptchaV2TaskProxyless` | reCAPTCHA v2 |
| `RecaptchaV2Task` | reCAPTCHA v2 + 调用方 HTTP(S) 代理 |
| `RecaptchaV3TaskProxyless` | reCAPTCHA v3 |
| `HCaptchaTaskProxyless` | hCaptcha |
| `HCaptchaTask` | hCaptcha + 调用方 HTTP(S) 代理 |
| `TurnstileTaskProxyless` | Cloudflare Turnstile |
| `TurnstileTask` | Turnstile + 调用方 HTTP(S) 代理 |
| `ImageToTextTask` | 原生图片文字输入 |
| `CoordinatesTask` | 原生坐标点选 |
| `GridTask` | 原生图片网格选择 |
| `RotateTask` | 原生旋转角度选择 |
| `FunCaptchaTaskProxyless` / `FunCaptchaTask` | 只加载 Arkose 挑战组件 |
| `GeeTestTaskProxyless` / `GeeTestTask` | GeeTest v3/v4 挑战组件 |
| `DataDomeSliderTask` | DataDome 挑战；必须提供代理与匹配 User-Agent |
| `AmazonTaskProxyless` / `AmazonTask` | Amazon WAF `jsapiScript` 或 challenge/captcha 双脚本挑战组件 |

字段映射包括：

- Turnstile：`data → cData`、`pagedata → chlPageData`、`action`
- reCAPTCHA v3：`pageAction → action`
- hCaptcha：`rqdata` 或 `enterprisePayload.rqdata`
- 上下文：`userAgent`、`cookies`
- 代理：`proxyType`、`proxyAddress`、`proxyPort`

Amazon WAF 的旧式 interstitial 参数中，2Captcha 把两个脚本 URL 标为可选；CaptchaMesh 不会
猜测租户与区域相关的 URL，因此该模式要求同时传 `challengeScript` 和 `captchaScript`。
新版 `jsapiScript` 模式只需 `websiteKey` 与 `jsapiScript`，两种模式不能混传。

## 明确不支持

- `callbackUrl`：返回 `ERROR_CALLBACK_NOT_SUPPORTED`，避免 Hub 对任意地址发起回调。
- v1 `pingback` 和 pingback 管理动作同样禁用；当前稳定方式是轮询。
- reCAPTCHA Enterprise、`recaptchaDataSValue`、自定义 `apiDomain`。
- SOCKS 和带用户名/密码的代理：当前 Android WebView Worker 只接受无鉴权 HTTP(S) 代理。
- CyberSiARA 及未列入能力矩阵的任务。
- v1 多 ID `ids` 批量查询。

不支持的输入在创建阶段直接失败，不会进入手机队列。

## 与原生协议的关系

“完整协议兼容”指 v1/v2 的任务提交、轮询、鉴权、错误、余额与反馈线格式；不表示手机能够
完成 2Captcha 目录中的所有题型。兼容接口只是外层翻译器：内部仍使用 CaptchaMesh UUID、任务租约、手机 Worker 和仅持久化
token 的安全边界。`getBalance` 返回合成的正余额，仅供会在启动时检查余额的客户端通过；
CaptchaMesh 不计费。图片通过短期 Worker 租约资源端点传输，类型化结果按 2Captcha v2
solution 原样返回。需要 Cookie、localStorage、完整请求头或通用 WebView selector 时，继续使用
[`/v1` 原生客户端](./batch-integration.md)。
