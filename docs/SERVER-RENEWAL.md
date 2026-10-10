# 服务器续签维护

更新：2026-10-10。现役执行端为发布服务器，软件最新版本为 0.4.1。续签迁移本身未重建 0.4.0 包，随后 0.4.1 修复通过同一通道发布。用户已确认安装包清单与当前根元数据同日到期，并使用已有南科大邮箱告警。

## 权限与到期

- 本机 DPAPI 发布凭据保留四种角色；root、targets 私钥不上传。服务器只接收 snapshot、timestamp 两把在线密钥，加载时检查角色集合、根授权及与离线角色无权限重叠。
- 当前 root、targets 到期：2031-09-30T12:27:17Z（北京时间 20:27:17）。每次本机新版本发布继续将 targets 到期日与当时的可信根一致；不取消客户端到期检查。
- 服务器每 20 天延长 snapshot、timestamp 至运行时起 30 天，不能越过 root／targets 的到期日。小时 timer 用于看门检查、失败重试和通知补发，不是每小时重签。
- root、targets 到期前 90 天每日邮件提醒维护。离线角色到期时服务器停止签发并告警；根轮换必须由本机签名并维护旧客户端的连续根链，不能只换一个 root.json。当前根轮换尚未发生，不把一般续签作为根轮换工具。
- root／targets 不上服务器可防止服务器授权新安装包；在线密钥泄漏仍可阻断或冻结更新，需由本机轮换在线角色。这不是对服务器入侵的全面免疫。

## 运行与告警

- 单元：`sustech-campus-renewal.service`／`.timer`，开机启用、`Persistent=true`。用户 `campus-renewal`；无登录 shell。服务启用 `ProtectSystem=strict`、`ProtectHome=true`、`NoNewPrivileges=true`，安装包目录只读。
- 代码／依赖：`/opt/sustech-campus-renewal`，TUF 7.0.0、securesystemslib 1.5.1、cryptography 48.0.1。只写 `/var/www/sustech-campus-updates/metadata`、`.publish.lock`、`/var/lib/sustech-campus-renewal`。
- `/etc/sustech-campus-renewal/online-keys.json`、`mail.json` 为 root:root 0600；由 systemd `LoadCredential` 注入运行进程。目录 root:campus-renewal 0750；公开 `trust-root.json` 可读。凭据、状态及校园资料不进入源码、公开元数据或安装包。
- 邮箱配置来源：现有 `D:\AppData\Himalaya\config.toml` 南科大账户、其本机加密凭据读取器，以及 Thunderbird 已有 SMTP TLS 465 配置。只向同一邮箱发送此项目告警；不复制其他邮件。邮箱授权凭据更换后，重新运行受限迁移入口刷新服务器凭据。
- 每小时检查公网签名链、两个平台授权及实际时间戳回读内容。失败立即邮件，之后每 24 小时提醒；在线清单将在 3 天内到期或已过期时额外紧急提醒。恢复成功发一次通知。SMTP 失败保留通知队列，每小时补发。
- 每小时持续重试，无 48 次停止限制。签发已提交但公网回读失败时，仅重新验证，不重复签发。状态同时记录下一次签发时间与回退基线。
- 主服务启动失败由 `OnFailure=sustech-campus-renewal-alert.service` 调用独立的系统 Python／标准库邮件处理器；不依赖 TUF 或主虚拟环境。与主服务共用去重队列和恢复标记，正常已处理的错误不重复通知。
- 服务器整体断电／断网时同机邮件通道也不可用；联网后补跑，已排队通知补发。服务器或邮箱凭据整体不可用时，不能保证同机邮件告警送达；外部存活监控不在此次范围。

检查命令（服务器）：

```sh
sudo systemctl status sustech-campus-renewal.timer
sudo systemctl show sustech-campus-renewal.service -p Result -p ExecMainStatus
sudo cat /var/lib/sustech-campus-renewal/status.json
sudo journalctl -u sustech-campus-renewal.service --since '2 days ago'
sudo systemctl start sustech-campus-renewal.service
```

服务成功须同时满足退出码 0、`client_verified: true`、`pending_alerts: 0`；`healthy` 是已验证但尚未到签发日，`renewed` 是签发／待确认签发已验证，`error` 是等待重试。每次运行不打印密码、地址或其他邮件内容。

## 发布兼容与恢复

本机 `publish_release.py` CLI 在准备新版本前先验签同步公网链和回退基线；targets 版本与在线 snapshot、timestamp 版本分别推进。不能把长时间未刷新的本机镜像直接当成最新公网状态。发布与续签共用锁，编号文件不可改写，包哈希检查通过后最后原子替换时间戳；原 0.3.5／0.4.0 客户端仍使用同一根和更新源。

首次配置流程：将本项目公开服务器脚本、单元和公开根上传到私密临时目录；在 `/opt/sustech-campus-renewal/venv` 安装上述固定依赖；本机运行 `deploy/migrate_server_renewal.py <remote-stage>`。脚本验证既有 SMTP 凭据，将仅含在线角色和邮箱配置的 JSON 通过验证主机密钥的 SSH 标准输入送到 root 私密文件，不生成凭据导出文件。之后启用 timer 并验签／验收邮件。`provision_server_renewal.py` 不自动签发安装包授权。

普通失败恢复时修复网络、TLS、公开目录权限或邮箱凭据，然后启动服务或等待小时检查。已过期的 snapshot、timestamp 可以在验签和未过期离线授权保护下恢复；客户端始终严格拒绝过期元数据。

如需退回本机维护，可先 `sudo systemctl disable --now sustech-campus-renewal.timer`，在本机验签同步当前公网链，使用 `renew_feed.py --force` 签发及发布。旧 Windows 任务保持停用，PowerShell 安装／运行入口明确报退役错误；恢复它属于重新变更执行端。回滚不能覆盖较新的编号元数据或降低版本号，原签名密钥和安装包均保持不变。

## 线上验收

- 2026-10-10 服务器实际签发 timestamp／snapshot 版本 14；targets 保持本机签发的版本 13。两个平台仍为 0.4.0，公网严格 TUF 验签及内容回读通过。
- 首次迁移后的在线清单到期 2026-11-09 15:47:28；下一次续签到期点 2026-10-30 15:47:28（北京时间，随后小时检查执行）。timer 已 enabled，正式 service 退出码 0。
- 0.4.1 发布后 timestamp／snapshot 为 15、targets 为 14，双平台均为 0.4.1；在线清单到期 2026-11-09 22:44:42，下一次续签仍为 2026-10-30 15:47:28。实际运行续签服务返回 healthy、client_verified=true、pending_alerts=0，退出码 0；离线密钥、20 天周期和既有授权不变。
- SMTP 验收邮件已进入南科大 INBOX；隔离模拟公网回读失败／恢复，通过生产邮件发送路径发出两封标注“验收”的通知，线上 timestamp 字节未变。模拟邮件失败补发、每日去重、过期恢复、回退阻止、后续本机发布版本兼容均有回归测试。
- 旧 Windows `SUSTechCampusPublisher-Renewal` 已 Disabled。本次未重启客户端、浏览器、共享 nginx 或其他项目服务。
- Windows／Linux 全套各 235 passed、2 skipped；更新及续签测试覆盖 22 个签名／故障场景，包含独立启动失败邮件队列及去重。已知上游 `papers` 弃用警告未改变。
- 本机证据目录：`D:\Artifacts\SUSTechCampusDashboard\server-renewal-20261010`；服务器公开代码／测试临时目录 `/tmp/sustech-server-renewal-cWOIYrEt`、受限故障演练状态仍保留，不自动清除。Memory／RemNote 不写入。
