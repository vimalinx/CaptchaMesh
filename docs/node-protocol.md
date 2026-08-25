# 工作流节点协议 v2

Node Agent 让手机显示电脑本机登记的工作流。用户仍须在 App 中明确选择和启动；Hub 只传递
白名单 ID，不接收 `cwd`、命令或临时参数。

## 信任边界

| 身份 | Header | 能力 |
|---|---|---|
| 手机 / 本机脚本 | `Authorization: Bearer <API_KEY>` | 列表、启动、运行状态、人工挑战 |
| 节点首次登记 | `Authorization: NodeKey <NODE_KEY>` | 换取短期运行 token |
| 在线节点 | `Authorization: Node <NODE_TOKEN>` | 长轮询固定 ID、回报运行状态 |

`API_KEY` 和 `NODE_KEY` 必须不同，并只放在权限为 `0600` 的文件或受限环境中。Hub 对手机
公开工作流名称和说明，但不会接收本机路径与命令。

## 配置白名单

```bash
cp registrations.example.json registrations.json
chmod 600 registrations.json
```

每项配置包含公开元数据和只在本机使用的执行字段：

```json
{
  "id": "captchamesh-demo",
  "name": "CaptchaMesh 自检",
  "summary": "运行一次手机验证链路自检",
  "provides": ["验证链路自检"],
  "details": [{"label": "用途", "value": "验证人工接管链路"}],
  "cwd": ".",
  "command": ["python3", "demo_registration.py"],
  "captchaTypes": ["turnstile"],
  "enabled": true
}
```

`id` 应稳定且只包含本机可信配置。`command` 必须是字符串数组，不经 shell 解释；不要把手机
输入、网络响应或环境变量拼接成命令。修改配置后重启 Node Agent 才会重新登记。

## 启动 Node Agent

```bash
mkdir -p ~/.config/captchamesh
chmod 700 ~/.config/captchamesh
chmod 600 ~/.config/captchamesh/api.key ~/.config/captchamesh/node.key

python3 node_agent.py \
  --hub https://mesh.example.com \
  --api-key-file ~/.config/captchamesh/api.key \
  --node-key-file ~/.config/captchamesh/node.key \
  --node-id personal-pc \
  --name "个人电脑" \
  --registry ./registrations.json
```

节点启动时上传公开 offer，之后长轮询 `start` / `stop`。收到 `start` 时，它只根据
`registrationId` 查本机白名单，设置 `CAPTCHAMESH_URL`、`CAPTCHAMESH_API_KEY` 和
`CAPTCHAMESH_RUN_ID` 后启动固定进程组；停止时只终止对应运行的进程组。

## 失败语义

- 节点离线：工作流仍可见但不可启动，Hub 返回 `ERROR_NODE_OFFLINE`。
- ID 不在白名单：节点拒绝执行并回报失败。
- 同一工作流正在运行：Hub 不创建第二个活动运行。
- 节点异常退出：运行不会被伪装为成功；重连后继续以 Hub 状态为准。

公网部署必须使用 HTTPS，并限制 Hub 的 Host、请求体、并发和速率。参考
[Hub 部署说明](../deploy/hub/README.md)。
