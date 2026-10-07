# 第二阶段执行交接

更新：2026-10-07。状态：**仅文档交接已完成；代码、部署、迁移与服务器清理待执行**。

用户已确认：去除运营者托管，公网只保留发布页及静态更新，本机浏览器页承载全部校园功能；加上安装后自动启动/打开、已安装唤起、本机更新提醒与点击后自动安装。当前 Thread 已通过问题卡确认只准备文档，由新 Thread 实施。不要将该确认读成“第二阶段已实现”，也不用重新询问是否保留托管。

## 开始时读取

1. [AGENTS.md](../AGENTS.md)：核心工程边界。
2. [第二阶段总计划](WEB-HOSTED-PLAN.md)：范围、用户路径、迁移、资源处置及验收；发生描述冲突时以该计划的最新确认范围为准。
3. [复用与下载规则](SHARED-METADATA-AND-DOWNLOADS.md)、[UPDATING.md](../UPDATING.md)：数据边界和既有发布机制。
4. [第一阶段记录](HOSTED-PHASE1.md)：当前仍在运行的旧架构、历史证据和故障边界；它不是新建托管的授权。
5. 当前代码及真实机器状态。修改发布页前读取 Hallmark skill（维护者路径为 `C:\Users\fy\.codex\skills\hallmark\SKILL.md`）；Windows 长命令按 windows-terminal-timeout 运行。

## 基线与第一步

- 仓库：`D:\Projects\SUSTechCampusDashboard`，功能基线提交 `a87672e` / `0.3.5`。此文档提交会在它之后；执行前看最新 Git 状态并保留并行修改。
- 本地 Python：`D:\Caches\SUSTechCampusDashboard-venv\Scripts\python.exe`。
- 产物：`D:\Artifacts\SUSTechCampusDashboard`；构建/测试缓存：`D:\Caches\SUSTechCampusDashboard`；真实数据：`D:\AppData\SUSTechCampusDashboard`。隔离验收不能覆盖真实目录。
- 现有公共地址：`https://124.221.144.155/app/`；静态更新：`https://124.221.144.155/campus-updates/`。保留这两个用途，公共入口内容需替换。
- 既有维护连接是 `ubuntu@124.221.144.155`，可用只读 SSH 核对。保持主机密钥验证；私密账号、空间 ID、令牌和数据库正文不写入公共日志。
- 2026-10-07 盘点仍有入口、共享元数据和 1 个个人空间服务。nginx 与 GraspMemoEdu 共用配置。候选删除路径、保留路径及约 365 MiB 表观大小见总计划；不要仅凭旧清单删除。
- GitHub 仓库已公开；发布签名私钥仍只在发布者 DPAPI 私密目录。保持原信任根和更新地址，检查续签任务实际状态。

先运行只读 Git/配置/服务/目录盘点，形成精确清单与验证方法；然后直接按总计划 A–D 开发。没有需要预先补答的产品决定，安装器和打开协议的工程默认已在总计划中给出。

## 代码入口与必须处理的关联

- [client.py](../src/sustech_dashboard/client.py)、[runtime.py](../src/sustech_dashboard/runtime.py)、[cli.py](../src/sustech_dashboard/cli.py)
  - 当前 `supervise(..., local_only=False)` 在 Windows 未保存凭据时跳公网；`--local-only` 才走本机，CLI start/serve 也调用该默认值。
  - 统一改为本机默认，兼容已有参数，处理 cloud-access 等旧配对恢复，不清空本机身份/文件，也不误删用户自有服务器配置。
  - 复用单实例锁、访问 fragment、健康检查、更新状态与进程监督；旧启动器/协议可能仍从旧版本目录启动，要验证实际选中版本。
- [protocol.py](../src/sustech_dashboard/protocol.py)
  - 当前仅 connect/login，Windows HKCU 协议调用可执行文件的 --connect-uri。
  - 增加受限本机 open；已停止时启动并等就绪，已运行时打开现有实例；不接受外部任意参数。
  - 旧运营者 connect/login 不能继续初始化凭据或配对。用户自有服务器能力若保留，必须显式区分。
- [deploy/build_client.py](../deploy/build_client.py)、[deploy/install-autostart.ps1](../deploy/install-autostart.ps1)
  - 现有 PyInstaller onedir、Windows 无控制台、ZIP 和 release.json 可复用；安装器尚不存在。
  - 增加当前用户安装、程序/数据分离、快捷入口、原用户启动和卸载保护；自启动仅在用户选择后登记，不静默恢复已撤销旧任务。
- [updates.py](../src/sustech_dashboard/updates.py)、[instance_routes.py](../src/sustech_dashboard/instance_routes.py)、[templates/settings.html](../src/sustech_dashboard/templates/settings.html)、[templates/index.html](../src/sustech_dashboard/templates/index.html)、[static/instance.js](../src/sustech_dashboard/static/instance.js)
  - 设置页已有检查、安装及轮询；新增首页提醒与明确的等待/重连/回退结果，复用后台 API。
  - 已有约 30 秒后检查、约每日检查、签名包安装和启动回退；不要新建平行更新器。
- [app.py](../src/sustech_dashboard/app.py)、[shared_metadata.py](../src/sustech_dashboard/shared_metadata.py)
  - hosted 的快照 pack/unpack 使用 `_metadata_ref`；导出时按本人有效授权展开后再关闭共享服务，验证脱离旧服务可读。
  - 删除本机对运营者共享层的依赖，不把引用 JSON 直接当完整迁移数据。
- [paths.py](../src/sustech_dashboard/paths.py)、[materials_store.py](../src/sustech_dashboard/materials_store.py)、[materials_local.py](../src/sustech_dashboard/materials_local.py)、[download_agent.py](../src/sustech_dashboard/download_agent.py)
  - 保留数据目录、个人基线、源版本、文件存在/修改状态和哈希；把运营者队列执行退役与本地下载复用分开处理。
  - Windows 有 D 盘时沿用既有 D 盘数据默认，无 D 盘时使用用户目录；不得把维护者固定绝对路径硬编码为所有人的安装位置。
- [entry.py](../src/sustech_dashboard/entry.py)、[templates/entry.html](../src/sustech_dashboard/templates/entry.html)、[static/entry.js](../src/sustech_dashboard/static/entry.js)
  - 当前是动态登录入口。改为独立静态发布页后，公共站不得继续启动入口 Flask 服务或加载旧配对登录脚本。
- [deploy/hosted_admin.py](../deploy/hosted_admin.py)、[deploy/entry_admin.py](../deploy/entry_admin.py)、[deploy/hosted_routes.py](../deploy/hosted_routes.py)、[deploy/hosted_upgrade.py](../deploy/hosted_upgrade.py)
  - 用于读取现有服务/配置/备份规则；退役过程只移除精确校园块和独占资源。
  - `retire_trial.py` 会重建空服务，`reset_connections.py` 会清连接；二者都不能直接代替本阶段迁移/最终清理。
- [deploy/publish_release.py](../deploy/publish_release.py)、[deploy/publish_feed.py](../deploy/publish_feed.py)、[deploy/renew_feed.py](../deploy/renew_feed.py)、[deploy/install_update_feed.py](../deploy/install_update_feed.py)
  - 保留 TUF 源、版本单调性和续签；静态更新目录不属于托管业务垃圾。不要重新 --init 或覆盖信任根。

## 执行顺序与验证

1. 盘点现状、保护真实数据和共享站点；记录可恢复方案。
2. 在隔离目录完成本机默认启动、安装/唤起、更新提醒、旧配对退役及迁移代码。
3. 运行 Windows/Linux pytest、真实分发包检查和全新用户安装流程。新增回归围绕启动、迁移和更新的实际失败条件，保留业务写入保护。
4. 发布新签名版本，在隔离环境验证旧客户端升级，准备并预览静态发布页及下载/打开入口；先具备可用接替路径，不提前覆盖仍需迁移的旧入口。
5. 暂停旧端新增任务及旧保留 timer，完成一致性备份、迁移与共享引用展开，验证本人数据归属、文件哈希和账本；迁移失败按总计划恢复，不能双端执行。
6. 将受限恢复材料搬到 D 盘并核验，切换静态发布页并验收，再按精确清单退役服务器服务、目录、凭据和旧路由；保留静态更新、TLS和其他站点。
7. 回读服务/端口/路径/网站与实际磁盘回收，完成总计划验收；更新 README、客户端说明、更新说明与阶段状态，写明仍未验证的用户首次试用。

已有测试入口包括 `tests/test_instance.py`、`test_updates.py`、`test_feed_renewal.py`、`test_materials.py`、`test_material_alignment.py`、`test_local_files.py`、`test_hosted.py`、`test_entry.py` 和 Linux 的 `test_nginx_upload.py`。旧托管测试可调整到迁移/退役或删除确已移除功能的覆盖，但须说明原因，不能通过删除仍有意义的测试来制造通过。

已有真实包验收入口是 `deploy/verify_client.py`。它目前不证明安装器、浏览器协议首次打开或完整升级体验，新阶段必须补这些实际验收。旧 `verify_connection.py` 和 `verify_entry_flow.cjs` 证明的是旧托管链路，不能直接充当新链路证据。

文档交接不要求运行产品测试；执行阶段不得将本文列出的验收当作已运行结果。需要重启用户正在使用的应用时，遵循现行协作规则；无关网站和服务始终保持。

## 可直接用于新 Thread 的启动指令

> 在 D:\Projects\SUSTechCampusDashboard 执行第二阶段。先读取 AGENTS.md、docs/LOCAL-MODE-HANDOFF.md 和 docs/WEB-HOSTED-PLAN.md，再核对当前代码与线上状态。用户已确认取消运营者托管，公网改为静态发布页及签名更新源；全部校园功能由本机浏览器个人页提供，保留用户自有 Linux 服务器。实现安装后自动启动打开、受限本机唤起、首页更新提醒和点击后自动安装，保持现有业务、个人数据及 TUF 机制。按计划迁移并核验恢复后，删除清单内服务器托管资源，保留静态更新与 GraspMemoEdu 等共享资源。上一 Thread 只完成文档，没有实施这些变化。直接按计划推进与验收，保留并行改动，学校真实验收仅限读取/下载；遇到超范围数据或共享资源影响时再明确提出。
