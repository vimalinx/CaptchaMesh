# 完整测试指南

这份指南用于在发布前验证 CaptchaMesh 的公开源码、电脑端、Android App、端到端人工流程和
Hub 安全边界。普通贡献先运行自动测试；涉及配对、通知、网络或挑战界面的变更，再执行对应的
人工测试。

## 准备环境

自动测试需要：

- Python 3.11 或更高版本；CI 同时覆盖 3.11 和 3.14；
- JDK 17；
- Android SDK 35；
- 能执行 Gradle Wrapper 的网络环境；
- 可选的 Gitleaks，用于完整 Git 历史密钥扫描。

在项目根目录创建环境：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt build bandit pip-audit
```

## 一条命令完成自动测试

```bash
./tools/test_all.sh
```

它执行以下门禁：

1. Python 单元、协议、鉴权和安全回归测试；
2. Bandit 对公开 Python 源码的静态检查；
3. `pip-audit` 依赖漏洞检查；
4. wheel 和 sdist 构建；
5. 发布包私有文件、危险路径、链接和敏感标记检查；
6. 从 wheel 隔离安装后的 CLI 冒烟测试；
7. Android 单元测试、Lint 和 Debug APK 构建。

安装 Gitleaks 后增加完整 Git 历史扫描：

```bash
./tools/test_all.sh --security
```

成功时最后一行是：

```text
CaptchaMesh full test suite passed
```

## 分层运行

只测试 Python：

```bash
.venv/bin/python -m unittest discover -s tests -v
```

只验证 Python 发布包：

```bash
.venv/bin/python -m build
.venv/bin/python tools/verify_public_release.py dist
```

只测试 Android：

```bash
cd app-src
./gradlew --no-daemon :app:testDebugUnitTest :app:lintDebug :app:assembleDebug
```

APK 输出位置：

```text
app-src/app/build/outputs/apk/debug/app-debug.apk
```

## 电脑端真实启动测试

安装并启动：

```bash
./install.sh
captchamesh start
```

另开终端验证 loopback 健康检查：

```bash
curl --fail --silent http://127.0.0.1:8893/healthz
```

期望返回包含以下字段的 JSON：

```json
{"ok":true,"protocolVersion":1,"service":"captchamesh-local-bridge"}
```

再运行：

```bash
captchamesh config --json
```

确认输出包含 `apiBase`、`apiKeyFile` 和 `stateFile`，但不包含 `apiKey`。密钥文件和配对文件的
权限应为 `0600`，它们的父目录应为 `0700`。

## Android 与配对测试

1. 从 Release 安装 APK，或者安装本机刚构建的 APK。
2. 运行 `captchamesh start` 并打开完整配对链接。
3. 确认链接中的能力令牌位于 `#` 后；页面打开后地址栏只剩 `/setup`。
4. 用 App 扫描二维码，允许通知和前台服务权限。
5. 确认电脑页面显示手机名称和“手机已连接”。
6. 关闭 App 前台页面，保持后台等待服务运行。

非交互启动测试时，将标准输出重定向到文件：

```bash
captchamesh start >captchamesh-start.log 2>&1
```

日志只能出现受限配对文件路径，不能出现 `#` 后的令牌。测试完成后删除该临时日志。

## 人工端到端任务测试

至少覆盖以下代表性任务：

| 测试 | 电脑端观察 | 手机端操作 | 期望结果 |
|---|---|---|---|
| 图片文字 | 创建数字 task ID | 输入图片文字 | 返回 `text` |
| 坐标点击 | 任务进入 processing | 点击目标位置 | 返回坐标数组 |
| 图片网格 | 任务进入 processing | 选择多个格子 | 返回序号数组 |
| 旋转 | 任务进入 processing | 调整角度 | 返回角度 |
| Turnstile | 保留原浏览器上下文 | 手动完成组件 | 返回 token |
| DataDome | 代理与 UA 匹配 | 手动完成滑块 | 返回 Cookie/token 结果 |

每项都应同时验证：

- Android 在后台时收到不含挑战内容的通知；
- 手机完成前客户端持续得到 `processing` 或 `CAPCHA_NOT_READY`；
- 完成结果只返回发起该任务的电脑；
- 重复查询不会串到其他任务；
- 结果完成或失败 10 分钟后从本机数据库清理；
- 日志中没有 API Key、Cookie、代理认证、挑战正文或答案。

网页挑战必须使用你有权测试的页面。token 是否被目标页面接受还取决于浏览器状态、User-Agent、
Cookie 和出口网络是否与挑战上下文一致。

## ADB 开发链路测试

USB 或无线 ADB 已连接时运行：

```bash
adb devices
adb reverse tcp:8890 tcp:8890
adb reverse --list
python3 broker.py --host 127.0.0.1 --port 8890
```

在 App 中使用 `http://127.0.0.1:8890`。设备重连后再次检查 `adb reverse --list`；不要修改
Clash Verge，也不要把开发端口暴露到局域网。

## Hub 部署验收

自托管 Hub 每次部署前后都执行：

```bash
curl --fail --silent https://你的域名/healthz
./tools/verify_hub_security.sh 你的-SSH-目标 你的域名
```

验收必须确认：

- Hub 应用只监听服务器 loopback；
- 公网只暴露 HTTPS 隧道入口；
- Host、代理头、请求体、速率和鉴权边界测试通过；
- 直接源站 HTTP 不能访问 Hub；
- DNS 不指向源站地址；
- 日志不包含密钥、挑战正文或解密结果。

## 发布前检查表

- [ ] `./tools/test_all.sh --security` 通过。
- [ ] Android 真机安装、通知、扫码和至少一个人工任务通过。
- [ ] `git status --short` 为空。
- [ ] `.secrets/`、`.ai/`、`registrations.json`、APK、数据库和账号数据未进入 Git。
- [ ] `python tools/verify_public_release.py dist` 通过。
- [ ] Release 中的 APK、wheel、sdist 和 `SHA256SUMS` 属于同一提交。
- [ ] GitHub Actions 的 Python、Security、Android 和 Package job 全部为绿色。

如果失败，先保留失败命令和最小日志；不要把密钥、Cookie、二维码、完整配对链接或源站地址
粘贴到公开 Issue。
