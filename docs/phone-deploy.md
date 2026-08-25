# Android 安装与连接

CaptchaMesh Android App 支持 Android 10（API 29）及以上版本。日常使用推荐通过电脑端的一次性
二维码配对；ADB reverse 仅用于本机开发。

## 安装

从项目 Release 下载 APK，或使用 JDK 17 和 Android SDK 35 自行构建：

```bash
cd app-src
./gradlew --no-daemon :app:assembleDebug
adb install -r app/build/outputs/apk/debug/app-debug.apk
```

Release 页面提供的测试版 APK 使用自动化构建签名。升级时应始终从同一项目发布渠道下载，
并核对发布页中的 SHA-256；签名来源不同的 APK 不能直接覆盖安装。

## 推荐：二维码配对

在电脑运行：

```bash
captchamesh start
```

1. 用电脑浏览器打开终端显示的 `127.0.0.1` 配对链接。
2. 在 App 中扫描二维码；二维码 60 秒后失效且只能领取一次。
3. 允许通知。App 会运行可见的前台服务，等待已配对电脑发来的挑战。
4. 提交一次测试任务，确认手机通知、手动处理和结果回传均正常。

配对完成后，App 默认打开“Agent 任务”页。电脑 Agent 调用本机 API 时会自动通知手机，不要
先去“工作流”页启动项目。“工作流”仅用于从手机启动电脑预先登记的可选脚本。

“设置 → 任务提醒”可以关闭 CAPTCHA 到达弹窗；关闭后任务仍会到达，打开 App 即可处理。
声音、震动和通知渠道由“系统通知设置”控制。Android 要求后台连接保留一条低优先级前台服务
通知，否则系统可能停止后台等待。

二维码包含端点密钥，应当作临时密码处理，不要截图或发送给他人。需要更换电脑或怀疑二维码
泄露时，在电脑删除旧配对状态后重新配对。

## 开发：ADB reverse

USB 或无线 ADB 已连接时：

```bash
adb reverse tcp:8890 tcp:8890
python3 broker.py --host 127.0.0.1 --port 8890
```

在 App 设置中填写 `http://127.0.0.1:8890` 和本机开发 Key。ADB reverse 只在该 ADB 连接
存活期间有效，设备重连后通常需要重新执行。

## 排查

- 扫码页面打不开：确认 `captchamesh start` 仍在运行，并使用终端给出的完整本机链接。
- 扫码提示过期：在配对页面生成新二维码，不要重复使用旧图。
- 没有通知：允许通知与前台服务权限，并关闭针对 CaptchaMesh 的电池限制。
- Hub 可用但任务不来：确认电脑桥和手机连接的是同一个邮箱，查看配对页状态。
- ADB 模式连接失败：运行 `adb reverse --list`，确认 `tcp:8890 tcp:8890` 存在。

网络信任与 Hub 可见信息见 [端到端加密中继](e2ee-relay.md)。
