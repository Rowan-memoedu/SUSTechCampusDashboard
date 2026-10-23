# 第二阶段执行交接

更新：2026-10-24。**0.5.22 Windows 已通过统一发布流程并同步日常本机，修复普通链接授权、后台启动、同步与作业详情阻塞，并补齐学校人机验证接续和慢目录扫描等待。**当前证据见 [0.5.22 验收](RELEASE-0.5.22-ACCEPTANCE.md)；此前作图题恢复见 [0.5.16 验收](RELEASE-0.5.16-ACCEPTANCE.md)，原有作业与设置均保留；Linux 目标保留为 0.5.9。后续更新仅使用 `python deploy/release.py --version <版本> --commit <完整提交>`，失败按原参数续跑。公共站仅提供静态下载／帮助，运营者托管已退役。旧 Windows 10 实测见 [0.4.3 历史验收](RELEASE-0.4.3-ACCEPTANCE.md)，迁移见 [阶段证据](LOCAL-MODE-ACCEPTANCE.md)。不要重复迁移、清空本机或恢复旧托管。

## 当前路线

用户已确认取消运营者业务托管，公网只保留发布页和原 TUF 更新源，个人功能由本机浏览器提供，保留用户自有 Linux 服务器。范围与资源清单见 [WEB-HOSTED-PLAN.md](WEB-HOSTED-PLAN.md)，工程边界见 [AGENTS.md](../AGENTS.md)。

续签按最新确认执行：**服务器每 20 天续签快照和时间戳，每小时检查／持续重试，告警到南科大邮箱**。根和安装包发布密钥只在本机；安装包清单与根均至 2031-09-30，提前 90 天提醒本机维护。旧 Windows 续签任务已停用，旧 Codex 自动化保持退役。实际签发、公网双平台验签、邮箱接收及故障恢复演练通过。见 [UPDATING.md](../UPDATING.md) 与 [服务器维护](SERVER-RENEWAL.md)。

## 恢复上下文

1. 先读阶段验收记录，再核对 Git 工作树和实际运行版本；保留已有改动。
2. 读取总计划中的迁移顺序、精确删除清单和共享资源边界。旧 [HOSTED-PHASE1.md](HOSTED-PHASE1.md) 仅为历史与退役前证据，不能重新开通托管。
3. 维护者 Python：`D:\Caches\SUSTechCampusDashboard-venv\Scripts\python.exe`；项目：`D:\Projects\SUSTechCampusDashboard`；构建缓存：`D:\Caches\SUSTechCampusDashboard`。
4. 当前发布产物：`D:\Artifacts\SUSTechCampusDashboard\releases\v0.5.22`，直接访问与同步证据：`D:\Artifacts\SUSTechCampusDashboard\direct-open-sync-recovery-20261023`；原提取及作图恢复证据保留在 `D:\Artifacts\SUSTechCampusDashboard\homework-extraction-recovery-20261021`。安装入口：`D:\Applications\fy\SUSTechCampusDashboard`；实际运行包由 `D:\AppData\SUSTechCampusDashboard\updates\current.json` 选择，核对其文件清单，不能以兼容入口版本判断后台版本。真实数据位于同一私密数据根。历史清空重装、迁移、隔离与恢复材料保持原位，不自动恢复或删除；历史路径见对应阶段验收记录。
5. 维护连接：`ubuntu@124.221.144.155`，保持主机密钥校验。公共入口 `/app/`，TUF `/campus-updates/`；nginx 与 GraspMemoEdu 共用，不能整文件重写或停掉共享服务。

## 下一步与执行边界

- 后续版本继续复用 `build_client.py`、`build_installer.py`、`build_site.py` 及原 TUF 根；已发布版本不可同版本换包。0.4.0 用户需用当前安装器更新旧启动器，单独升级后台不会替换旧入口。
- 现有本机后台已经完成正常退出、合并及重启，没有重启浏览器。后台服务重启遵循既有授权；整体电脑或正在使用的用户应用重启仍按用户规则处理。
- 历史 `migrate_local.py` 迁移已完成。随后按用户授权清空应用并重新安装、亲自 CAS 登录；当前凭据／令牌／基线均重新生成，旧配对已移入隔离区，不再原位保留。不要重放历史任务或把旧数据再次合并进当前目录。
- `retire_hosted.py`、`verify_retirement_backup.py`、`sqlite_recovery_digest.py` 保存本轮方法；服务器原始资源与临时阶段目录已清除，不能直接重跑其首次阶段。恢复只能使用受限 D 盘副本，并先明确目标与执行端，防止重复校园写入。
- 保留静态站、TUF、共享 nginx/TLS、其他站点和发布密钥；真实包、安装器、双平台旧版升级及服务器退役均有验收记录。
- 真实校园验收仅限本人读取和附件下载；不提交测试作业、不占用场地、不选退课、不上传或删除打印任务。原 Windows 11 用户清空重装和隔离 Windows 10 标准用户的首次使用见 [0.4.3 历史验收](RELEASE-0.4.3-ACCEPTANCE.md)；当前版本安装、升级及未测边界以本页开头的发布验收为准，不扩大为所有 Windows 架构或组织策略。

已禁用旧运营者部署/升级 CLI 和入口执行命令；`retire_trial.py`、`reset_connections.py` 仍是历史维护工具，不能代替最终退役。前端修改继续使用 Hallmark，长命令按 windows-terminal-timeout 运行。Memory 与 RemNote 不在本轮写入范围。
