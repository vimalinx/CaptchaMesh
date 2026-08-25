# 电脑端本地加密桥

电脑端的职责是把现有 Agent 使用的 2Captcha 请求在本机转换并加密。Hub 只转发密文，配对
密钥只保存在电脑和 Android Keystore 中。

```text
Agent → http://127.0.0.1:8893 → 本地加密 → Hub → 手机
Agent ← http://127.0.0.1:8893 ← 本地解密 ← Hub ← 手机
```

## 安装与首次启动

从源码目录安装：

```bash
./install.sh
captchamesh start
```

官方 Hub 可以直接创建一次性配对。私有 Hub 如果要求邀请密钥，把受限密钥保存为
CaptchaMesh 状态目录中的 `hub-api.key`，或者首次
启动时显式传入：

```bash
captchamesh start --api-key-file /受限路径/hub-api.key
```

程序只监听 `127.0.0.1:8893`。终端会打印带随机能力令牌的本机配对链接；打开后用手机扫描
二维码。二维码过期时可以在页面内重新生成，手机连接后页面自动更新。
重复运行 `captchamesh start` 会识别已有实例并重新输出状态链接，不会再启动一份服务。

配对状态、本机 API Key 和任务状态数据库默认位于
`~/.config/captchamesh/`，权限分别限制为目录 `0700`、密钥和数据库 `0600`。在项目源码目录
中已有 `.secrets/relay-pairing.json` 时，安装脚本会在目标不存在的前提下安全迁移到用户配置
目录；不会覆盖已有配对。

## Agent 接入

Python `2captcha-python` 用户使用同样的方法名，只替换导入：

```python
from captchamesh import TwoCaptcha

solver = TwoCaptcha()  # 自动读取本机 Key
token = solver.turnstile(
    sitekey="PUBLIC_SITE_KEY",
    url="https://example.com/",
)
```

原始 v2 客户端把 base URL 改为 `http://127.0.0.1:8893`，调用 `/createTask` 和
`/getTaskResult`。需要机器可读配置时运行：

```bash
captchamesh config --json
```

该命令会主动输出本机 Key，应只在受信任的本机进程中使用。官方 `2captcha-python` 会固定
使用 HTTPS，因此不能只把它的 `server` 改成 loopback；CaptchaMesh 提供的 `TwoCaptcha`
适配器保留其调用方法，并把传输安全地限制在本机 HTTP。

## 生命周期

- 每次 `/createTask` 或 `/in.php` 立即返回本机数字任务 ID。
- 单个手机邮箱的任务在电脑端串行发送，避免并发轮询误领其他任务结果。
- 手机未完成时返回 `processing` 或 `CAPCHA_NOT_READY`。
- 答案只保存在权限为 `0600` 的本机数据库中，并在完成或失败 10 分钟后清理。
- 本机桥重启会把未完成任务明确标记为 `ERROR_BRIDGE_RESTARTED`，不会假装成功。
- `/reportCorrect`、`/reportIncorrect`、`reportgood` 和 `reportbad` 只记录本机反馈。
