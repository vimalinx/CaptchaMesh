# 模式适配与验证

## Agent API 模式

这是默认模式。先运行 `captchamesh start` 并扫码配对。

Python 项目优先使用 CaptchaMesh 适配器：

```python
from captchamesh import TwoCaptcha

solver = TwoCaptcha()
```

官方 `2captcha-python` 会固定 HTTPS，不能只把 `server` 改为 loopback。其他 v1/v2 客户端把
base URL 改为 `http://127.0.0.1:8893`，Key 从 `captchamesh config --json` 指向的受限文件读取。
保持 `in.php`/`res.php` 或 `createTask`/`getTaskResult` 的解析逻辑不变。

此模式没有活动工作流、Node Agent 或 `runId`。验证时直接创建一个任务，确认 Android 后台
自动提醒、人工完成并将结果返回原调用方。

## 手机工作流模式

只有用户要求从手机启动电脑脚本时使用。将固定命令加入 `registrations.json`，启动 Node
Agent，并让脚本读取 `CAPTCHAMESH_URL`、`CAPTCHAMESH_API_KEY` 和
`CAPTCHAMESH_RUN_ID`。不要在配置中硬编码这些值。

单个活动工作流可以省略 `runId`；并发时 v2 在顶层或 task 中传 `runId`，v1 传 `runId` 或
`run_id`。不要根据任务顺序猜绑定关系。

## 验证顺序

1. 运行 `captchamesh skill inspect <文件或目录> --mode <模式> --json`，确认协议、题型和残留主机。
2. 执行目标程序自身测试，至少覆盖请求构造、未就绪、错误和结果解析。
3. 检查 CaptchaMesh 健康状态、错误 Key、错误 ID 和不支持题型。
4. Agent API 模式在没有活动工作流时完成一次任务；手机工作流模式只启动一个白名单项目完成一次任务。
5. 目标站拒绝结果时核对 sitekey、页面 URL、action、UA、Cookie、代理出口和时效。

## 常见失败

| 现象 | 优先检查 |
|---|---|
| Agent API 没有手机提醒 | `captchamesh start`、扫码配对、Android 通知设置、本机 `127.0.0.1:8893` |
| `ERROR_NO_ACTIVE_RUN` | 当前是否错误使用 Hub 工作流接口；工作流是否由用户启动 |
| `ERROR_AMBIGUOUS_RUN` | 是否有多个活动工作流；补 `runId` |
| `CAPCHA_NOT_READY` 一直不变 | 手机是否收到任务、挑战是否加载、租约是否过期 |
| 手机完成但目标站拒绝 | 浏览器上下文、出口和 token 时效是否一致 |
| `ERROR_TASK_NOT_SUPPORTED` | 题型或上下文能力是否在支持矩阵 |
