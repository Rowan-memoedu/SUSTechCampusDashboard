# 南科大校园面板个人客户端

解压整个安装包，然后运行 `campus-client.exe`（Windows）或 `./campus-client`（Linux）。网页会在本机浏览器打开，首次使用输入自己的学校 CAS 账号。不要仅复制 exe：它需要同目录的 `_internal` 文件夹。

- 每台设备、每个实例只使用一个人的校园账号。无需注册发布者的服务账号。
- Windows 密码用当前用户的 DPAPI 加密；其他 Windows 用户无法直接解密。Linux 网页登录仅保存在本次进程内，长期开机服务器使用 systemd 加密凭据。
- 默认数据位置：Windows 有 D 盘时为 `D:\AppData\SUSTechCampusDashboard`，否则为当前用户的 LocalAppData；Linux 为 `~/.local/share/sustech-campus-dashboard`。
- 默认下载位置：Windows 有 D 盘时为 `D:\download`，其他情况为用户的 `Downloads/SUSTech`。首次完整扫描只建立基线，自动模式下载之后新出现的附件；手动下载包含旧资料。
- 运行前设置 `SUSTECH_DASHBOARD_DATA_ROOT`、`SUSTECH_DOWNLOAD_ROOT` 可指定绝对目录。不同账号必须使用不同目录及 `--port`，不会自动合并记录。
- 客户端启动后和持续运行期间每天检查更新。在“本机与更新”点击安装：校验 TUF 签名、包哈希及版本，等待现有操作完成再重启；启动失败恢复旧版本。运行数据在安装目录之外，不会随更新覆盖。
- 检查只请求公开版本信息和系统对应的安装包，不发送 CAS 账号、校园数据或遥测。版本站会看到普通请求 IP。签名保证来源和完整性，不能证明发布者代码永远无恶意；需要审查时可向发布者索取对应源码。
- 校园打印等校内服务仍需要运行设备连接校园网或学校 VPN。
- 作业提交、预约、选退课等仍由本人在页面点击触发；不确定的回执不会自动重复发送。已撤销的讨论间后台监控不会重新启用。

## 自己的常开服务器

使用同一套程序，运行 `./campus-client --no-browser --port 18771`，设置 `SUSTECH_CLOUD=1`、`SUSTECH_PUBLIC_HOST=自己的域名`、`SUSTECH_DASHBOARD_DATA_ROOT=绝对数据目录`。程序只监听回环地址，外部访问由 HTTPS 反向代理及独立访问认证保护。

服务用 systemd 的 `LoadCredentialEncrypted=sustech-cas:...` 加载本机加密凭据，内容格式为 `{"sid":"学号","password":"密码"}`；不要写入仓库、命令参数或 shell 历史。服务账户只访问自己的数据目录。`SUSTECH_DOWNLOAD_MODE=local` 将资料存到服务器；不设置时保留配对 Windows 下载代理模式，资料在配对电脑保存。

这是非学校官方项目。附带软件的用途及分发条件见 `THIRD-PARTY-NOTICES.md`。
