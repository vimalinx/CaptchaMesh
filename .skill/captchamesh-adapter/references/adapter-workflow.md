# 注册机改造与验证

## Python 官方 2Captcha SDK（v1）

保留调用 `recaptcha()`、`hcaptcha()`、`turnstile()` 和 `get_result()` 的业务代码，仅把 key 与 server 配成 CaptchaMesh：

```python
import os
from twocaptcha import TwoCaptcha

solver = TwoCaptcha(
    os.environ["CAPTCHAMESH_API_KEY"],
    server=os.environ.get("CAPTCHAMESH_SERVER", "mesh.vimalinx.com"),
)
```

该 SDK 的 `server` 是主机名，不要填 `https://` 或路径。若项目封装层不暴露 `server`，在 provider 构造器中增加配置，不要修改 sitekey 或目标站 URL。

## v1 原始 HTTP

把原 `https://2captcha.com/in.php` 和 `/res.php` 的 host 改为 `CAPTCHAMESH_SERVER` 配置的 Hub，保持表单参数、`OK|...` 解析和 `CAPCHA_NOT_READY` 轮询逻辑不变。轮询间隔建议 2–5 秒，总超时 180 秒以上。

## v2 JSON

把 API base URL 改为已配置的 CaptchaMesh Hub，`clientKey` 从环境读取。创建后保存数字 `taskId`，轮询 `getTaskResult`，只在 `status=ready` 时提取 solution。不要把 HTTP 200 当作成功；先检查 `errorId`。

## 自动绑定与并发

正常人工用法：先在 App 注册机列表选一个并点击开始，再让电脑脚本提交 CAPTCHA。此时 SDK 无需认识 CaptchaMesh run。

若必须并发多个注册机：

- v2 在请求顶层或 task 中增加 `runId`。
- v1 增加非标准表单字段 `runId`（也接受 `run_id`）。
- 不要靠任务创建顺序猜绑定关系。

## 验证顺序

1. 运行 `inspect_registration.py`，确认协议类型、题型及残留主机。
2. 执行注册机自身单元测试；若没有测试，至少覆盖请求构造和 token 解析。
3. 对 Hub 做无副作用检查：v1/v2 balance、错误 key、错误 ID、不支持题型。部署前后各检查一次健康状态。
4. App 只启动一个注册机，用一个真实任务做端到端验证。记录 task ID、状态变化与最终注册结果，日志不记录 token。
5. 若目标站拒绝 token，先核对 sitekey、页面 URL、action、UA、cookie、代理出口和 token 时效；不要连续让人重做验证码。

## 常见失败定位

| 现象 | 优先检查 |
|---|---|
| `ERROR_NO_ACTIVE_RUN` | App 是否先启动注册机，节点是否在线 |
| `ERROR_AMBIGUOUS_RUN` | 是否有多个活动 run；补 `runId` |
| `CAPCHA_NOT_READY` 一直不变 | 手机是否取到任务，网页是否正确加载，worker lease 是否过期 |
| 手机完成但目标站仍拒绝 | sitekey/URL/action 与浏览器上下文是否一致，token 是否被重复使用或过期 |
| `ERROR_TASK_NOT_SUPPORTED` | 题型或 Enterprise/data-s/代理能力不在手机范围 |
| 409 | 先看错误码；通常是 run 生命周期或上下文冲突，不要靠重复过验证码解决 |
