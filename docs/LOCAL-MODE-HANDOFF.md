# 第二阶段执行交接

更新：2026-10-08。0.4.0 已签名发布、安装并接替真实本机数据；公共站已改静态，运营者托管已退役。证据与尚未独立验收的首次交互使用见 [LOCAL-MODE-ACCEPTANCE.md](LOCAL-MODE-ACCEPTANCE.md)。不要重复执行已完成的迁移或恢复旧托管。

## 当前路线

用户已确认取消运营者业务托管，公网只保留发布页和原 TUF 更新源，个人功能由本机浏览器提供，保留用户自有 Linux 服务器。范围与资源清单见 [WEB-HOSTED-PLAN.md](WEB-HOSTED-PLAN.md)，工程边界见 [AGENTS.md](../AGENTS.md)。

续签按最新用户纠正执行：**发布者电脑后台每 20 天续签，不使用 Codex 定时任务**。旧 Codex 自动化已删除，Windows 后台入口已实际续签并通过双平台验签；密钥仍只在本机 DPAPI 目录。见 [UPDATING.md](../UPDATING.md)。

## 恢复上下文

1. 先读阶段验收记录，再核对 Git 工作树和实际运行版本；保留已有改动。
2. 读取总计划中的迁移顺序、精确删除清单和共享资源边界。旧 [HOSTED-PHASE1.md](HOSTED-PHASE1.md) 仅为历史与退役前证据，不能重新开通托管。
3. 维护者 Python：`D:\Caches\SUSTechCampusDashboard-venv\Scripts\python.exe`；项目：`D:\Projects\SUSTechCampusDashboard`；构建缓存：`D:\Caches\SUSTechCampusDashboard`。
4. 发布产物与截图：`D:\Artifacts\SUSTechCampusDashboard\v0.4.0`；私密证据：`D:\Artifacts\SUSTechCampusDashboard\local-mode-private`；真实数据：`D:\AppData\SUSTechCampusDashboard`。恢复副本在 `D:\Quarantine\SUSTechCampusDashboard\hosted-retirement-20261008`，至少保留至 2026-10-15 00:22，不自动删除。
5. 维护连接：`ubuntu@124.221.144.155`，保持主机密钥校验。公共入口 `/app/`，TUF `/campus-updates/`；nginx 与 GraspMemoEdu 共用，不能整文件重写或停掉共享服务。

## 下一步与执行边界

- 后续版本继续复用 `build_client.py`、`build_installer.py`、`build_site.py` 及原 TUF 根；0.4.0 包已发布，不可同版本换包。
- 现有本机后台已经完成正常退出、合并及重启，没有重启浏览器。后台服务重启遵循既有授权；整体电脑或正在使用的用户应用重启仍按用户规则处理。
- `migrate_local.py` 的导出、试合并和提交已真实完成，自动下载初始暂停；旧配对文件原位保留用于恢复但不会再联网。不要重放历史任务或再次覆盖真实目录。
- `retire_hosted.py`、`verify_retirement_backup.py`、`sqlite_recovery_digest.py` 保存本轮方法；服务器原始资源与临时阶段目录已清除，不能直接重跑其首次阶段。恢复只能使用受限 D 盘副本，并先明确目标与执行端，防止重复校园写入。
- 保留静态站、TUF、共享 nginx/TLS、其他站点和发布密钥；真实包、安装器、双平台旧版升级及服务器退役均有验收记录。
- 真实校园验收仅限本人读取和附件下载；不提交测试作业、不占用场地、不选退课、不上传或删除打印任务。新用户首次交互安装与真实使用须单独记录。

已禁用旧运营者部署/升级 CLI 和入口执行命令；`retire_trial.py`、`reset_connections.py` 仍是历史维护工具，不能代替最终退役。前端修改继续使用 Hallmark，长命令按 windows-terminal-timeout 运行。Memory 与 RemNote 不在本轮写入范围。
