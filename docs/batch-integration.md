# 工作流接入

CaptchaMesh 的接入原则是保留原程序的业务与浏览器会话，只把“等待人工完成 CAPTCHA”抽成
一次明确的请求。它适合个人、低频、由用户启动的流程，不提供后台批量任务或自动代答。

## 原生 Python 客户端

工作流由 App 启动时，Node Agent 会注入当前运行的地址、Key 和 `runId`：

```python
from captchamesh_client import CaptchaMeshClient

mesh = CaptchaMeshClient()
solution = mesh.solve_turnstile(
    website_url="https://example.com/",
    website_key="PUBLIC_SITE_KEY",
    mode="interactive",
)
token = solution["token"]
```

不要把 Cookie、代理认证或返回 token 写入日志。失败时记录不含秘密的本机 task ID 和错误码，
并让当前工作流明确失败或由用户决定重试。

## 2Captcha 兼容接口

已有 `2captcha-python` 调用方可使用项目适配器：

```python
from captchamesh import TwoCaptcha

solver = TwoCaptcha()
result = solver.hcaptcha(
    sitekey="PUBLIC_SITE_KEY",
    url="https://example.com/",
)
```

使用其他 v2 客户端时，把 API base URL 指向电脑本机的 `http://127.0.0.1:8893`，Key 可由
`captchamesh config --json` 提供给受信任的本机进程。不要把本机桥映射到局域网或公网。

支持任务、字段映射与明确不支持项见 [2Captcha API 兼容层](twocaptcha-v2-compat.md)。

## 注册到手机列表

复制示例并添加一个固定白名单项：

```bash
cp registrations.example.json registrations.json
```

工作流入口应满足以下要求：

- 一次启动只执行一个用户可理解的流程。
- 从环境读取 `CAPTCHAMESH_URL`、`CAPTCHAMESH_API_KEY` 和 `CAPTCHAMESH_RUN_ID`。
- 命令使用数组形式，不调用 shell，不接收手机提供的参数。
- 收到终止信号后及时清理自己的浏览器和子进程。
- 输出不包含 API Key、Cookie、代理认证、验证码 token 或账号凭据。

节点配置和运行状态见 [注册节点协议](node-protocol.md)。任务字段和类型化结果见
[挑战协议 v3](challenge-protocol-v3.md)。
