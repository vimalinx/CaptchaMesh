# 两种模式与工作流接入

CaptchaMesh 有两个入口。两者都由手机上的人完成 CAPTCHA，但启动方向不同。

| 模式 | 启动方 | CAPTCHA 接口 | 活动工作流 |
|---|---|---|---|
| Agent API（默认） | 电脑 Agent | `http://127.0.0.1:8893` | 不需要 |
| 手机工作流（可选） | 手机用户 | Node Agent 注入的 Hub 地址 | 必须由用户启动 |

## 模式一：Agent API（默认）

适用于已经在电脑运行的 Agent、浏览器自动化或脚本。先启动并配对一次：

```bash
captchamesh start
```

Python 调用方可以使用项目适配器：

```python
from captchamesh import TwoCaptcha

solver = TwoCaptcha()
result = solver.hcaptcha(
    sitekey="PUBLIC_SITE_KEY",
    url="https://example.com/",
)
```

其他 v1/v2 客户端将 API base URL 指向 `http://127.0.0.1:8893`。Agent 创建任务后，本机桥
自动加密并通知手机，不要先在 App 启动工作流，也不要添加 `runId`。

## 模式二：手机工作流（可选）

适用于希望从手机选择并启动电脑固定脚本的场景。复制白名单示例：

```bash
cp registrations.example.json registrations.json
chmod 600 registrations.json
```

每项只登记固定 `id`、`cwd` 和命令数组。启动 Node Agent 后，App 的“工作流”页会显示这些
项目。用户点击启动时，Node Agent 根据本机白名单执行命令，并注入：

- `CAPTCHAMESH_URL`
- `CAPTCHAMESH_API_KEY`
- `CAPTCHAMESH_RUN_ID`
- `CAPTCHAMESH_REGISTRATION_ID`

脚本从环境读取这些值并提交 CAPTCHA。只有此模式使用活动 `runId`；单个活动工作流可以自动
绑定，并发工作流必须显式传 `runId`。

工作流入口应满足以下要求：

- 一次启动只执行一个用户可理解的流程；
- 命令使用数组形式，不调用 shell，不接收手机提供的参数；
- 收到终止信号后清理自己的浏览器和子进程；
- 输出不包含 API Key、Cookie、代理认证、CAPTCHA token 或账号凭据。

节点配置见 [工作流节点协议](node-protocol.md)，本机 Agent 接入见
[电脑端本地桥](local-bridge.md)，题型与结果见 [2Captcha 兼容层](twocaptcha-v2-compat.md)。
