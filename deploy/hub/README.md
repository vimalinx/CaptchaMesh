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

## 首次安装

服务器支持 Ubuntu 22.04/24.04 或 Debian 12，域名需要先接入 Cloudflare。按以下顺序操作：

1. 在 Cloudflare Zero Trust 创建 Tunnel。
2. 为 Tunnel 添加 Public Hostname，例如 `mesh.example.com`，Service 填
   `http://localhost:8890`。
3. 从 [GitHub Releases](https://github.com/vimalinx/CaptchaMesh/releases) 下载并解压
   `captchamesh-hub-v0.19.0.tar.gz`。
4. 运行安装器：

```bash
cd captchamesh-hub-v0.19.0
sudo ./deploy/hub/install.sh --domain mesh.example.com
```

安装器会安全地提示粘贴 Tunnel token；输入内容不会回显，也不会进入 shell 历史。它会自动安装
Python、Cloudflared、systemd 服务并完成本机与公网健康检查。API Key、Node Key、数据库和
Tunnel token 都只保存在服务器的权限受限目录中。

自动化安装可把 token 放在权限为 `0600` 的文件中：

```bash
sudo ./deploy/hub/install.sh \
  --domain mesh.example.com \
  --tunnel-token-file /root/captchamesh-tunnel.token \
  --non-interactive
```

再次运行同一版本或新版本安装器就是原地升级；已有密钥、配对白名单和数据库不会被覆盖。

如果使用 Tailscale Funnel 或自己的 HTTPS 反向代理，不粘贴 Tunnel token，安装后把公网入口
指向 `http://127.0.0.1:8890`，再执行：

```bash
curl -fsS https://mesh.example.com/healthz
```

## 从开发机部署

维护者也可以从仓库通过 SSH 部署：

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

Cloudflare Tunnel 使用专用的 `cloudflared` 无登录用户运行。令牌文件固定为
`/etc/cloudflared/tunnel.token`，权限为 `0640 root:cloudflared`；生产单元模板见
[`cloudflared.service.in`](https://github.com/vimalinx/CaptchaMesh/blob/main/deploy/hub/cloudflared.service.in)。
不要把令牌直接写入 systemd 单元或命令行。

现有服务升级会保留密钥和数据库，并迁移数据目录所有权。部署前应自行备份数据库。

## 安全默认值

- 允许的 Host 来自 `/etc/captchamesh-hub/hub.env`，部署参数只接受主机名字符。
- 请求体最大 12 MiB；并发、客户端速率和全局速率均有限制。
- 只有来自回环隧道的 `CF-Connecting-IP` 才会参与客户端身份判断。
- systemd 禁止提权、设备、非回环网络、可写系统目录和多数内核接口。
- cloudflared 同样使用专用账户、空 capability 集合和只读系统目录，不以 root 运行。
- Key 为 `0640 root:captchamesh`，数据库目录为 `0700 captchamesh:captchamesh`。
- 日志不得记录 API Key、Node Key、Cookie、代理认证或 CAPTCHA token。

公开一次性配对由 `CAPTCHAMESH_ALLOW_PUBLIC_PAIRING=1` 开启，并有独立的速率、并发与过期
限制。私有 Hub 使用 `--private-pairing` 安装即可关闭免邀请配对。

## 运维

```bash
ssh captchamesh-hub 'systemctl status captchamesh-hub --no-pager'
ssh captchamesh-hub 'sudo journalctl -u captchamesh-hub -n 80 --no-pager'
curl https://mesh.example.com/healthz
```

密钥轮换会使现有直连客户端失效；端到端配对设备 token 的轮换应通过重新配对完成。协议与
信任边界见 [端到端加密中继](https://github.com/vimalinx/CaptchaMesh/blob/main/docs/e2ee-relay.md)。

## 加密备份

生产机可安装 `captchamesh-backup`、对应 service 和 timer。备份使用仅含公钥的专用 GPG
密钥环加密，私钥必须保留在服务器之外。每天生成数据库一致性快照和恢复所需配置，保留
14 天；备份目录为 `/var/backups/captchamesh-hub/`。上线后必须在持有私钥的机器上实际解密
一次，而不是只检查定时器状态。

首次安装或升级时传入只含公钥的文件即可启用：

```bash
sudo ./deploy/hub/install.sh \
  --domain mesh.example.com \
  --tunnel-token-file /root/captchamesh-tunnel.token \
  --backup-public-key /root/captchamesh-backup-public.asc \
  --non-interactive
```
