---
name: captchamesh-adapter
description: 将现有注册机、浏览器自动化或协议脚本的 2Captcha 接口改接 CaptchaMesh 手机手动验证。适用于识别 2captcha-python/Node SDK、in.php/res.php 旧协议、createTask/getTaskResult v2 协议，选择无侵入适配方式，改造配置并验证类型化答案或 token 回传；不用于自动识别或绕过验证码。
---

# CaptchaMesh 注册机适配

目标是保留注册机原有注册、会话、代理和账号落盘逻辑，只把它的 CAPTCHA 提交/轮询通道接到 CaptchaMesh。手机上的人负责完成验证，注册机继续消费返回的 token。

## 工作流

1. 先读当前项目的 `AGENTS.md`、README、配置样例和启动入口，不读取或输出真实密钥。
2. 对候选脚本运行：

   ```bash
   python3 .skill/captchamesh-adapter/scripts/inspect_registration.py <文件或目录> --json
   ```

3. 根据报告选择适配面：
   - 已使用 2Captcha SDK 或 `/in.php`、`/res.php`：优先走 v1 兼容，只改服务器主机与 API key 来源。
   - 已使用 `/createTask`、`/getTaskResult`：走 v2 兼容，只改 API base URL 与 `clientKey` 来源。
   - 自写 CAPTCHA 回调：在原抽象层增加 CaptchaMesh provider，不把验证码逻辑散落进注册流程。
4. 读取 [协议映射](references/protocol.md)，先核对题型和参数是否在手机能力范围内；发现不支持能力时停止改造并明确报告，不降级成假成功。
5. 按 [改造与验证](references/adapter-workflow.md) 修改配置、代码和示例环境文件。密钥只从受限环境文件或环境变量读取，不写入源码、日志、README 或 Skill。
6. 依次验证：静态扫描 → 本地协议测试 → 服务健康检查 → 单次真实注册。必须先在 CaptchaMesh App 手动启动且只保留一个活动注册任务；若并发运行多个注册机，显式传 `runId` 扩展。
7. 记录失败发生在哪一层：注册机未发任务、Hub 未入队、手机未取到、token 未回传、目标站拒绝 token。不要用反复过验证码代替定位。

## 修改约束

- 不改注册机的邮箱、短信、代理、指纹、账号保存和业务提交语义，除非用户明确要求。
- 不把 Hub API key 当成账号密码或目标站 token；不得打印 `clientKey`、cookie、代理认证、验证码 token。
- 保留原 provider 的可回退配置时，默认仍指向用户明确选择的 provider；不要静默把失败请求发给第三方。
- v1/v2 兼容指请求、查询、鉴权、错误与反馈格式兼容；接入前仍要按协议矩阵核对该题型的必要字段与网络上下文。
- v2 支持图片文字、坐标、网格、旋转、FunCaptcha、GeeTest v3/v4、DataDome 和 Amazon WAF。图片类使用手机原生控件；组件类只加载挑战组件，不打开目标站完整页面。
- DataDome 必须同时提供原浏览器 User-Agent 和代理；挑战参数过期、出口不一致或缺少供应商脚本时应明确失败，不得伪造成功结果。
- 回调/pingback、SOCKS、带用户名密码的代理目前禁用；使用轮询和无认证 HTTP(S) 代理。

## 完成标准

- 检查报告能识别原协议和题型，没有残留生效的 `2captcha.com` 默认地址。
- 错误 key、错误任务 ID、未就绪、成功 token、反馈和不支持题型均返回注册机能识别的协议格式。
- 单次真实任务只需完成一次验证码，注册机拿到同一个 token 并继续注册；失败时日志能关联 task ID，但不包含 token。
- 项目测试、证据链和工作区审计按所属工作区规范完成。
