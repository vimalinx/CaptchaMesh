---
name: captchamesh-adapter
description: 将现有 Agent、浏览器自动化或手机可启动的固定工作流接入 CaptchaMesh 人工 CAPTCHA。适用于识别 2captcha-python、in.php/res.php、createTask/getTaskResult，选择默认本机 Agent API 或可选手机工作流模式并验证结果回传；不用于自动识别或绕过验证码。
---

# CaptchaMesh 接入适配

只替换现有程序的 CAPTCHA 提交与轮询通道，保留其浏览器会话和业务逻辑。手机上的人完成验证，
原程序消费类型化答案或 token 后继续运行。

## 先选择模式

- **Agent API（默认）**：电脑程序已经自行启动。使用 `http://127.0.0.1:8893` 本地桥；手机不需要先启动工作流，不使用 `runId`。
- **手机工作流（可选）**：用户要从 App 启动电脑固定脚本。配置 `registrations.json` 与 Node Agent；脚本使用注入的 Hub 地址、Key 和 `runId`。

用户没有明确要求手机启动电脑脚本时，选择 Agent API 模式。

## 适配步骤

1. 阅读目标项目的 `AGENTS.md`、配置样例和 CAPTCHA provider，不读取或输出真实密钥。
2. 运行检查器并明确模式：

   ```bash
   captchamesh skill inspect \
     <文件或目录> --mode agent-api --json
   ```

3. 根据报告保留 v1 或 v2 调用语义，只替换 endpoint 与 Key 来源。详细做法见
   [模式适配与验证](references/adapter-workflow.md)。
4. 阅读 [协议映射](references/protocol.md)，确认题型、上下文和代理在支持范围；不支持时明确失败，不降级成假成功。
5. 验证静态配置、请求构造、错误格式和一次端到端人工任务。Agent API 模式不得要求活动工作流；手机工作流模式必须由用户启动白名单项目。
6. 按“程序未发任务 → 本机桥/Hub 未入队 → 手机未收到 → 结果未回传 → 目标站拒绝”定位，不用重复过验证码代替排错。

## 约束

- 密钥只从 CaptchaMesh 本机受限配置、受限环境文件或 Node Agent 注入环境读取；不得写入源码、日志、README 或 Skill。
- 不改变目标程序的邮箱、短信、代理、指纹、账号保存和业务提交语义，除非用户明确要求。
- 不把失败请求静默发给第三方 provider。
- DataDome 要求原浏览器 User-Agent 与代理出口一致；过期或缺少上下文时明确失败。
- callback/pingback、SOCKS 和带用户名密码的代理当前禁用。
- 手机工作流只能启动本机白名单中的固定命令数组，不接收手机下发的命令、路径或参数。

## 完成标准

- 所选模式、endpoint 和 Key 来源一致，没有生效的 `2captcha.com` 默认地址。
- 错误 Key、错误任务 ID、未就绪、成功结果和不支持题型均保持调用方可识别的格式。
- Agent API 模式在没有活动工作流时也能通知手机并回传结果。
- 手机工作流模式只执行用户启动的固定白名单项目，并正确绑定 `runId`。
- 日志可关联本机 task ID，但不包含 Key、Cookie、代理认证或 CAPTCHA token。
