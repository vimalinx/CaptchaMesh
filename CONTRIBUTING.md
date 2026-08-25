# 参与贡献

欢迎修复缺陷、完善文档和增加人工挑战的安全适配。提交前请先说明使用场景，避免把自动绕过、
后台抢任务、任意命令执行、密钥托管或批量滥用能力带入项目。

## 本地验证

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt build bandit pip-audit
./tools/test_all.sh
```

发布、安全、网络或密钥处理相关改动还应安装 Gitleaks 并运行 `./tools/test_all.sh --security`。
真机配对、通知和 Hub 验收步骤见 [完整测试指南](docs/testing.md)。

提交中不要包含 `.secrets/`、`.ai/`、`registrations.json`、数据库、APK、构建缓存、账号数据或
真实服务凭据。文档示例使用 `example.com`、占位 Key 和通用路径。

## 变更要求

- 协议变更同时更新对应 `docs/` 文档和测试。
- 新挑战类型必须有严格字段校验、明确失败语义和人工交互界面。
- 网络入口默认最小权限，密钥不得出现在 URL、日志或异常文本中。
- 修改 Hub 部署后，部署前后都检查 `/healthz`，并运行 `tools/verify_hub_security.sh`。
