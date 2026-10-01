# 运行模式与发布

## 实例边界

Windows 客户端与 Linux 服务器使用同一套 Python 业务实现和网页资源。每个后端进程只加载一个账号；不在共享进程里切换他人的 CAS 会话。业务查询、上传与预约从实例直接发往学校，发布服务器仅托管静态版本元数据和安装包。

本机服务只绑定 127.0.0.1。启动器经 URL fragment 将本机访问凭证交给页面，兑换 HttpOnly、SameSite=Strict cookie 后清除 fragment；凭证不会进入 HTTP 查询字符串或 Referer。写操作继续检查 Origin 与 CSRF。个人服务器由自己的 HTTPS 反向代理和访问密码保护，不提供他人账号的公共登录入口。

Windows 使用 DPAPI，兼容已有 SecureString 文件。Linux 使用 systemd 加密凭据；首次网页登录可仅存内存。上游凭据文件回退被禁用，上游缓存位于实例目录。CAS 使用 Requests 标准证书和主机名校验。

## 更新行为

- 使用固定版本 python-tuf 7.0.0，随程序附带公开信任根；签名、哈希、过期时间及版本回退检查由 TUF 完成。
- Windows 普通用户没有符号链接权限，适配层只将 TUF 已验证的 root_history 复制为 root.json；每次验证仍从内置根开始，不信任任意缓存根。
- 程序启动 30 秒后检查，持续运行时约每天检查一次。默认不自动安装。
- 点击安装后重新刷新签名元数据，按平台下载和验证，拒绝路径越界、符号链接、重复路径、过大文件及格式不兼容版本。
- 更新包仅包含程序。解压至实例 `updates/releases`；更新不覆盖数据、凭据、下载文件或 SQLite 操作账本。数据格式目前为 1，只接受相同格式。
- 阻止新写入，等待正在进行的学校操作、下载及回执结束，再切换子进程。用随机健康令牌核验新进程版本；启动失败恢复此前指针。业务账号暂时离线不触发版本回滚，避免学校故障导致升级循环。
- 已确认及待核对写入记录均保留，升级不重放操作。已撤销的讨论间监控维持撤销。
- 安装目录中的启动程序作为入口；真正运行的版本由 `updates/current.json` 指定。上一版本包保留用于恢复。此版本不做破坏性数据迁移；未来变更数据格式必须另行提供可回滚迁移流程。

## 维护者发布

密钥仅位于发布者 Windows 用户的 `D:\AppData\SUSTechCampusPublisher\release-keys.dpapi.json`，用 DPAPI 加密，不进入仓库、构建包或服务器。公开信任根为 `src/sustech_dashboard/update-root.json`。必须另行安全备份该 Windows 用户可恢复的发布密钥；丢失后不能伪造原有签名链。

1. 修改源码及 `pyproject.toml`、`__init__.py` 版本号；测试通过后在两个目标系统构建。首次发布前仅执行一次 `deploy/publish_release.py --init`。
2. `python deploy/build_client.py --output <产物目录> --work <缓存目录>`。依赖按 requirements-build.txt 安装；源码依赖见 pyproject.toml。Windows 10/11 x64 和 Ubuntu 24.04 x64 已列为当前验收平台，其他 Linux 发行版不能推断兼容。
3. `python deploy/verify_client.py <可执行文件> --cache <隔离验收目录>` 验证真实可执行文件首次启动、访问限制和正常停止。运行完整 pytest；学校侧只执行读取验收。
4. `python deploy/publish_release.py --repository D:\Artifacts\SUSTechCampusDashboard\feed <windows-x86_64.zip> <linux-x86_64.zip>`。同一平台已发布版本不可换包，需增加版本号。
5. 将该 feed 的公开文件同步到静态 HTTPS `/campus-updates/`；先上传目标文件与带版本号的元数据，最后原子替换 timestamp.json。不要上传发布密钥、账号数据或整个工作区。
6. 客户端的“检查更新”应显示新版本。先在隔离实例升级并验证数据保持，再更新自己的服务器。Git 私有仓库和安装包发布彼此独立。

时间戳有效期 30 天；无新版本时可不带安装包重跑发布命令续签，并按上一步发布。过期后客户端会拒绝旧元数据，现有程序仍可运行；新鲜元数据发布后恢复正常检查。根有效期 5 年，更换密钥应通过 TUF 根轮换完成，不能直接替换现有信任根。

### 自动续签

已配置 ChatGPT 桌面端中的 Codex 原生定时任务“校园面板更新通道自动续签”：北京时间每天 09:00 在发布者电脑运行。此电脑及 Codex 需要能够运行；关机期间不能续签。任务位于当前对话，正常检查保持安静，实际续签或执行失败时报告。

- 入口：`D:\Caches\SUSTechCampusDashboard-venv\Scripts\python.exe D:\Projects\SUSTechCampusDashboard\deploy\renew_feed.py`。
- 距离任一非根元数据到期不超过 14 天时续签，时间戳恢复为从续签时起 30 天。不需要续签时不解密密钥。
- 从公网读取并验证已发布的签名链，检查相对本地镜像的版本回退，再用本机 DPAPI 密钥签名。允许对已过期的非根元数据恢复续签；客户端仍严格拒绝过期元数据。根过期或轮换需要维护者处理。
- 续签只延长元数据有效期并递增其版本，安装包内容、软件版本及下载地址不变。签名密钥、CAS 凭据和校园数据不上传。
- 服务器发布持有文件锁并核对旧时间戳、已有包哈希；并发发布冲突直接停止。带版本号的元数据先发布，`timestamp.json` 最后原子切换。公开临时文件保留在服务器 `/tmp/sustech-renew-*` 便于排查，无私密内容。
- 发布后用客户端 TUF 实现从公网检查两个平台。成功后同步本地发布镜像。若发布成功但回读时断网，下次检查可直接恢复验证，不重复续签。
- 最近结果：`D:\AppData\SUSTechCampusPublisher\renewal-status.json`。必须同时满足退出码 0、`client_verified: true`；`healthy` 表示无需续签，`renewed` 表示本次已续签，`error` 需要排查。
- 手动执行使用同一命令。`--force` 仅用于维护者明确要求的即时续签，不放入日常定时任务。正常功能版本更新仍按上面的构建、测试、签名和发布流程进行。

元数据有效期与各角色关系见 [TUF 官方说明](https://theupdateframework.io/docs/metadata/)。

## 安全边界

签名证明更新来自持有发布密钥的人，并防止第三方篡改或回放旧元数据；不能证明维护者发布的代码无恶意。用户应有审查源码及选择是否安装的机会。本仓库当前保持私有，不应将“本地存储”宣传成数学上证明维护者无法写出窃取数据的更新。

云端模式要求本机反向代理只提供已认证的 HTTPS 入口。版本目录是无用户数据的独立静态目录，不与校园数据目录共用。
