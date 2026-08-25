# CaptchaMesh

> 给自己的 Agent 准备一个随身 CAPTCHA 处理器。

CaptchaMesh 是面向个人、低频自动化的开源人工接管工具。脚本遇到 CAPTCHA 时，把挑战端到端
加密后转到自己的 Android 手机；用户手动完成，结果返回原任务，Agent 继续运行。

```text
Agent / 脚本 → 电脑端加密桥 → 不可信 Hub → Android 通知 → 用户手动完成
```

它不自动识别或绕过 CAPTCHA，不提供任务市场，也不会让手机或 Hub 向电脑下发任意命令。
用户必须明确启动自己的工作流；远程可启动项只能来自电脑本机的固定白名单。

## 能做什么

- Android 后台等待，挑战到达时发送不含敏感内容的通知
- 原生处理图片文字、坐标点击、图片网格和旋转题
- 人工处理 Turnstile、hCaptcha、reCAPTCHA、FunCaptcha、GeeTest、DataDome 和 Amazon WAF
- 在需要时携带同一次浏览器任务的 Cookie、User-Agent、请求头、localStorage 和代理上下文
- 提供 Python 客户端与常用 2Captcha API v1/v2 兼容接口
- 通过本机直连、官方 Hub 或自托管 Hub连接电脑与手机
- 使用 AES-256-GCM 端到端加密；Hub 只保存短期密文和路由元数据

完整任务映射见 [2Captcha 兼容说明](docs/twocaptcha-v2-compat.md)。

## 五分钟开始

电脑端要求 Python 3.11+，当前正式支持 Linux。

```bash
./install.sh
captchamesh start
```

打开终端给出的本机地址，用 Android App 扫描 60 秒有效的二维码。配对完成后，本机兼容
接口位于 `http://127.0.0.1:8893`；服务不会监听局域网或公网地址。

已经使用 `2captcha-python` 的程序可以保留调用方式，只替换导入：

```python
from captchamesh import TwoCaptcha

solver = TwoCaptcha()
result = solver.turnstile(
    sitekey="PUBLIC_SITE_KEY",
    url="https://example.com/",
)
```

本机 Key 会从受限配置目录读取，不要把它写进源码。电脑端完整说明见
[本地加密桥](docs/local-bridge.md)，配对与信任模型见
[端到端加密中继](docs/e2ee-relay.md)。

## Android App

预构建 APK 可从项目 Release 获取。自行构建需要 JDK 17 和 Android SDK 35：

```bash
cd app-src
./gradlew --no-daemon :app:assembleDebug
adb install -r app/build/outputs/apk/debug/app-debug.apk
```

本机开发可使用 ADB reverse：

```bash
adb reverse tcp:8890 tcp:8890
```

手机安装、配对和网络选择见 [Android 使用说明](docs/phone-deploy.md)。

## 本机工作流白名单

若希望从 App 启动电脑上的固定工作流，先创建本机配置：

```bash
cp registrations.example.json registrations.json
```

只编辑本机 `registrations.json` 中的固定 `cwd` 和 `command`。该文件已被 Git 忽略；Hub 和
手机只能选择 `id`，不能提供命令、路径或临时参数。协议见
[节点协议](docs/node-protocol.md)。

## 自托管 Hub

Hub 应只监听 `127.0.0.1`，由 HTTPS 反向隧道暴露；不要直接开放 8890 端口。Ubuntu/systemd
部署模板和上线前后检查位于 [deploy/hub](deploy/hub/README.md)。公益 Hub 也无法解密任务
正文，但运营者仍能看到邮箱 ID、方向、时间和密文大小等必要元数据。

## 平台支持

| 组件 | 状态 | 最低环境 |
|---|---|---|
| Android App | 支持 | Android 10（API 29） |
| Linux 电脑端 | 支持 | Python 3.11+ |
| Linux Hub | 支持 | Ubuntu、systemd、HTTPS 隧道 |
| macOS 电脑端 | 实验性 | Python 3.11+，尚未纳入 CI |
| Windows / WSL | 未验证 | 原生 Windows 暂不支持 |
| iOS | 不支持 | 无客户端 |

## 安全与隐私

- 配对密钥只存在电脑端和 Android Keystore，不发送给 Hub。
- 挑战正文、Cookie 和答案在端点加密；通知和服务日志不记录这些内容。
- 本机状态目录为 `0700`，密钥和数据库为 `0600`。
- 回调、SOCKS、带认证代理和任意远程命令默认拒绝。
- 发现安全问题请按 [SECURITY.md](SECURITY.md) 私下报告。

CaptchaMesh 只用于你有权运行的个人工作流，并遵守目标服务条款和适用法律。

## 开发

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
env PYTHONPATH=.venv/lib/python3.14/site-packages \
  .venv/bin/python -m unittest discover -s tests -v

cd app-src
./gradlew --no-daemon :app:testDebugUnitTest :app:lintDebug :app:assembleDebug
```

Python 版本的 `PYTHONPATH` 目录会随解释器版本变化；普通虚拟环境通常无需显式设置。贡献
流程见 [CONTRIBUTING.md](CONTRIBUTING.md)，版本变化见 [CHANGELOG.md](CHANGELOG.md)。

## 项目结构

| 路径 | 内容 |
|---|---|
| `app-src/` | Android App 源码 |
| `local_bridge.py` / `captchamesh_cli.py` | 本机加密桥与命令行 |
| `broker.py` / `broker_asgi.py` | Hub 与任务路由 |
| `relay_protocol.py` | 端到端加密信封协议 |
| `challenge_protocol.py` | 挑战与类型化结果协议 |
| `twocaptcha_compat.py` | 2Captcha API v1/v2 兼容层 |
| `node_agent.py` | 可选的本机白名单工作流节点 |
| `.skill/captchamesh-adapter/` | Agent 接入 Skill |
| `deploy/hub/` | 自托管 Hub 配置 |

当前版本：`0.18.0`。项目采用 [MIT License](LICENSE)。
