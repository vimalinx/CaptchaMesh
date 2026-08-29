## 变更内容

说明解决的问题、实现边界和用户可见变化。

## 验证

- [ ] 已运行与变更相关的自动测试。
- [ ] 已说明真实运行、Android、Hub 或浏览器中尚未覆盖的部分。
- [ ] 发布、安全、网络或密钥相关变更已运行 `./tools/test_all.sh --security`。
- [ ] 协议或行为变化已同步更新 README、文档、测试和 CHANGELOG。

## 安全边界

- [ ] 未提交 `.secrets/`、`.ai/`、`registrations.json`、数据库、APK、账号数据或真实凭据。
- [ ] 未引入自动绕过、后台抢任务、任意远程命令或密钥托管。
- [ ] 日志、异常和示例不包含 Key、Cookie、代理认证、二维码或 CAPTCHA token。
