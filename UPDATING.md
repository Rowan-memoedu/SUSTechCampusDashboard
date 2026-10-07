# 运行模式与发布

0.3.0 托管实例的配置、升级、备份及退出方式见 [托管第一阶段交付记录](docs/HOSTED-PHASE1.md)。`local`、`personal_server`、`hosted` 通过 `SUSTECH_EXECUTION_MODE` 区分，未设置时兼容旧 `SUSTECH_CLOUD`。托管空间不能安装共享程序更新；维护者使用逐实例验收及自动回退的 `deploy/hosted_upgrade.py`。个人服务器与 Windows 客户端继续使用下文的 TUF 流程。

## 实例边界

Windows 客户端与 Linux 服务器使用同一套 Python 业务实现和网页资源。每个后端进程只加载一个账号；不在共享进程里切换他人的 CAS 会话。业务查询、上传与预约从实例直接发往学校，发布服务器仅托管静态版本元数据和安装包。

本机服务只绑定 127.0.0.1。启动器经 URL fragment 将本机访问凭证交给页面，兑换 HttpOnly、SameSite=Strict cookie 后清除 fragment；凭证不会进入 HTTP 查询字符串或 Referer。写操作继续检查 Origin 与 CSRF。个人服务器由自己的 HTTPS 反向代理和访问密码保护，不提供他人账号的公共登录入口。

Windows 使用 DPAPI，兼容已有 SecureString 文件。Linux 使用 systemd 加密凭据；首次网页登录可仅存内存。上游凭据文件回退被禁用，上游缓存位于实例目录。CAS 使用 Requests 标准证书和主机名校验。

### 个人服务器的网页登录

0.2.2 增加独立的网站访问会话：默认在浏览器保留 30 天，关闭浏览器、后端重启或版本更新后继续有效；主动退出会撤销当前凭证。随机凭证仅通过 HTTPS 的 Secure、HttpOnly、SameSite=Lax cookie 传输，数据库只保存其 SHA-256 摘要。更换网站访问密码后旧会话失效；登录和退出都检查 Origin 与 CSRF，错误密码有频率限制。

现有配对下载代理继续使用网站访问账号。CAS 凭据保持原有 DPAPI/systemd 管理方式。网站访问密码只保留 Werkzeug scrypt 哈希，由 systemd `LoadCredential=campus-web` 传给进程；设置 `SUSTECH_BROWSER_LOGIN=1` 后启用。

服务器需配套 `deploy/nginx-campus-browser.conf`：Nginx auth_request 每次确认后端许可；若回退到没有认证端点的旧版本则拒绝访问，避免因应用回退失去入口保护。`deploy/enable_browser_login.py` 在新运行时已安装后启用，先检查后端认证端点，再原子切换路由配置并验证 `nginx -t`；失败恢复原来的 Basic 认证配置。它从标准输入接收网站用户名和 scrypt 哈希，不能传入 CAS 密码。原来的 `nginx-campus.conf` 保留用于旧部署。

### 通过配对电脑打印（0.2.3）

个人服务器模式下，打印 API 通过固定的操作队列交给指定电脑执行，不从公网服务器连接校内打印系统。
启动已有配对客户端（`campus-client --agent`），在公网网页“校园打印”选择执行电脑。
电脑需连接校园网或具有打印访问权限的学校 VPN，且本机已通过 DPAPI 保存自己的校园账号。
网站配对密码只用于连接个人服务器；校园账号和学校会话不会进入打印中转协议。
打印查询、使用记录、上传和删除保留原逻辑；文件由个人服务器暂存，完成回执后清理。
离线时显示队列未知；不使用缓存空队列冒充读取成功。
上传和删除在本机 SQLite 中保留操作回执；断线或重启不自动重复执行不确定的学校操作。
网页关闭后可重新进入“校园打印”查询未完成操作。分发用户使用自己的客户端、配对服务器和账号。
本机独立模式保持直接访问学校打印系统，无需配对。

执行主机按实例所有者配对，不绑定开发者的电脑、主机名或固定 IP。
每个用户拥有自己的执行主机、校园凭据和操作账本。主机可为 Windows 电脑或 Linux 常开服务器；
Linux 执行主机用 systemd `LoadCredentialEncrypted` 提供 `sustech-cas` 和 `campus-pair`。
后者的内容格式为 `{"url":"自己的 HTTPS 面板地址","username":"网站访问用户名","password":"网站访问密码"}`，
不使用明文配置文件回退，不通过启动参数传递密码。
后续共享多用户入口必须将请求路由到所属用户的独立实例/数据目录；当前版本仍是每实例一个用户，
不能把一个实例的配对队列直接复用为全站公共队列。

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

已配置 ChatGPT 桌面端中的 Codex 原生定时任务“校园面板更新通道自动续签”：每 25 天在北京时间 09:00 于发布者电脑运行。此电脑及 Codex 需要能够运行；关机期间不能续签。任务位于当前对话，实际续签或执行失败时报告。

- 定时入口：`D:\Caches\SUSTechCampusDashboard-venv\Scripts\python.exe D:\Projects\SUSTechCampusDashboard\deploy\renew_feed.py --force`。
- 每次定时运行都把时间戳续签到从运行时起 30 天，给下一次运行保留约 5 天余量。即使刚发布过新版也继续续签，避免跳过一次后下一次运行晚于到期时间。
- 从公网读取并验证已发布的签名链，检查相对本地镜像的版本回退，再用本机 DPAPI 密钥签名。允许对已过期的非根元数据恢复续签；客户端仍严格拒绝过期元数据。根过期或轮换需要维护者处理。
- 续签只延长元数据有效期并递增其版本，安装包内容、软件版本及下载地址不变。签名密钥、CAS 凭据和校园数据不上传。
- 服务器发布持有文件锁并核对旧时间戳、已有包哈希；并发发布冲突直接停止。带版本号的元数据先发布，`timestamp.json` 最后原子切换。公开临时文件保留在服务器 `/tmp/sustech-renew-*` 便于排查，无私密内容。
- 发布后用客户端 TUF 实现从公网检查两个平台。成功后同步本地发布镜像。若发布成功但回读时断网，下次检查可直接恢复验证，不重复续签。
- 最近结果：`D:\AppData\SUSTechCampusPublisher\renewal-status.json`。必须同时满足退出码 0、`client_verified: true`；`healthy` 表示无需续签，`renewed` 表示本次已续签，`error` 需要排查。
- 手动续签使用同一命令；若只想按需检查，可省略 `--force`，此时仅在剩余有效期不超过 14 天时续签。正常功能版本更新仍按上面的构建、测试、签名和发布流程进行。

元数据有效期与各角色关系见 [TUF 官方说明](https://theupdateframework.io/docs/metadata/)。

## 安全边界

服务器使用浏览器登录代理时，`/campus/` 与内部 `/_sustech_campus_auth` 必须同时配置
`client_max_body_size 256m`。认证子请求仍携带原请求的长度，若内部路径继承较小的服务器限制，
附件会在认证阶段被拒绝并表现为 500。回归测试 `tests/test_nginx_upload.py` 使用真实 Nginx
核对文件完整传递、未登录拦截和超限拦截；需要 Linux 的 Nginx 运行环境。
既有服务器可执行 `deploy/fix_upload_proxy.py`，脚本只修正该内部路径，保存恢复配置并热加载 Nginx。

签名证明更新来自持有发布密钥的人，并防止第三方篡改或回放旧元数据；不能证明维护者发布的代码无恶意。用户应有审查源码及选择是否安装的机会。本仓库当前保持私有，不应将“本地存储”宣传成数学上证明维护者无法写出窃取数据的更新。

云端模式要求本机反向代理只提供已认证的 HTTPS 入口。版本目录是无用户数据的独立静态目录，不与校园数据目录共用。
