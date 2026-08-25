# 自托管 CaptchaMesh Hub

此目录提供 Ubuntu/systemd 部署模板。Hub 被设计成不可信密文信箱：端点密钥只在电脑和手机
中，Hub 能看到路由元数据，但不能解密挑战正文或答案。

## 推荐拓扑

```text
互联网 → HTTPS 隧道 / 反向代理 → 127.0.0.1:8890 → Hypercorn → Hub
```

不要将 8890 直接绑定到公网，也不要为源站额外创建绕过隧道的公开虚拟主机。公网域名必须
使用有效 HTTPS。Cloudflare Tunnel、Tailscale Funnel 或同等出站隧道均可，具体隧道凭据
不应放入此仓库。

## 部署

服务器需要 Ubuntu、Python 3.11+、systemd、OpenSSL，以及已经配置好的 HTTPS 隧道。部署
脚本要求显式提供 SSH 目标和公网主机名：

```bash
./deploy/hub/deploy.sh captchamesh-hub mesh.example.com
./tools/verify_hub_security.sh captchamesh-hub mesh.example.com
```

脚本会：

- 部署前检查公网 `/healthz`，新服务器尚未上线时只报告状态。
- 创建无登录权限的 `captchamesh` 系统用户。
- 在 `/etc/captchamesh-hub/` 生成权限受限且彼此不同的 API Key 与 Node Key。
- 在 `/opt/captchamesh-hub/` 安装应用，在 `/var/lib/captchamesh-hub/` 保存数据库。
- 只监听 `127.0.0.1:8890`，启动后再次检查本机与公网 `/healthz`。

现有服务升级会保留密钥和数据库，并迁移数据目录所有权。部署前应自行备份数据库。

## 安全默认值

- 允许的 Host 来自 `/etc/captchamesh-hub/hub.env`，部署参数只接受主机名字符。
- 请求体最大 12 MiB；并发、客户端速率和全局速率均有限制。
- 只有来自回环隧道的 `CF-Connecting-IP` 才会参与客户端身份判断。
- systemd 禁止提权、设备、非回环网络、可写系统目录和多数内核接口。
- Key 为 `0640 root:captchamesh`，数据库目录为 `0700 captchamesh:captchamesh`。
- 日志不得记录 API Key、Node Key、Cookie、代理认证或 CAPTCHA token。

公开一次性配对由 `CAPTCHAMESH_ALLOW_PUBLIC_PAIRING=1` 开启，并有独立的速率、并发与过期
限制。私有 Hub 不需要免邀请配对时，从 service 文件删除这一行后重新部署。

## 运维

```bash
ssh captchamesh-hub 'systemctl status captchamesh-hub --no-pager'
ssh captchamesh-hub 'sudo journalctl -u captchamesh-hub -n 80 --no-pager'
curl https://mesh.example.com/healthz
```

密钥轮换会使现有直连客户端失效；端到端配对设备 token 的轮换应通过重新配对完成。协议与
信任边界见 [端到端加密中继](../../docs/e2ee-relay.md)。
