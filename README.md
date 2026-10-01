# 南科大校园面板

基于 [`sustech_survival`](https://github.com/dumixthestpd/sustech_survival) 的校园工具。腾讯服务器上的私人网页显示每日概览、Blackboard 课程与作业状态及剩余时间、TIS 考试和待评教、图书馆空闲数与本人预约、E-Hall 可用场地与本人预约，并提供讨论间具体预约安排和后台预约监控。命令行也可以单独查询预约。

Blackboard 课程仅保留 2026 年 9 月 1 日及以后选课、且属于当前学期的课程。作业仅显示截止日期不早于该日且当前可访问的条目；没有截止日期的条目，须能确认内容创建于该日及以后。旧成绩簿任务不计入未提交数量。

今日课表接入 [`dumixthestpd/sustech-calendar`](https://github.com/dumixthestpd/sustech-calendar) 的本科校历，复用现有依赖的 `AcademicCalendar` 和 TIS 全学期课程模式。假期、校历停课日和期末考试期间不显示常规课程；调休补课按原课程日期及单双周转移。期中考试期间继续显示正常课程。页面显示日期性质、下次调休和校历最近核验时间；校历或课程查询失败时显示“暂不可确认”，不会退回按星期推断。缓存保存在应用数据目录，在线校历每轮同步通过 ETag 核验；跨年假期须取得新年度校历。

## 运行

Windows PowerShell 7：

```powershell
pwsh -NoProfile -File 'D:\Projects\SUSTechCampusDashboard\dashboard.ps1' serve
```

打开 `https://124.221.144.155/campus/`，独立的面板登录信息保存在本机受限目录 `D:\AppData\SUSTechCampusDashboard\校园面板登录.txt`。Chrome 的登录框使用该面板用户名和密码。服务器启动时立即同步，运行中每 30 分钟同步一次；页面每分钟更新显示，并分别标明各来源最近成功查询的时间。失败时保留上次成功数据并显示错误。

公网通过现有 HTTPS 443 端口访问，服务器应用仅监听服务器内部的 `127.0.0.1:18771`。本机无需开网页端口。Windows 登录任务 `SUSTechCampusDashboard` 只启动附件下载代理，经私有 HTTPS 接口拉取新附件并保存到 `D:\download`；关机期间网页和预约监控继续运行，本机下次登录后补下载基线之后的新可见附件。

其他命令：

```powershell
pwsh -NoProfile -File 'D:\Projects\SUSTechCampusDashboard\dashboard.ps1' sync
pwsh -NoProfile -File 'D:\Projects\SUSTechCampusDashboard\dashboard.ps1' bookings
pwsh -NoProfile -File 'D:\Projects\SUSTechCampusDashboard\dashboard.ps1' status
```

`sync` 经云端接口下载新附件；`status` 和 `bookings` 读取云端最近成功同步的数据。只有网页上明确提交讨论间监控目标后，远端服务才会在目标时段空闲时尝试预约。

## 讨论间监控

页面可选择今天至后天的讨论间和 15 分钟刻度的预约时段，查看该日的已占用时间。选定后填写预约主题；3 人起的讨论间还须提供至少两位共同申请人的图书馆系统 `accNo`。云端网页通过受锁保护的状态文件将监控目标交给服务器上的独立服务。服务器每 30 秒检查一次，仅当整个目标时段空闲才提交一次预约。成功、时段开始、手动停止或提交结果不确定都会终止自动尝试；结果不确定需到图书馆预约系统核查，程序不会盲目重试，也不会自动取消实际预约。

远端服务运行于独立 `sustechmon` 系统用户、`/opt/sustech-room-monitor` 安装目录与 `/var/lib/sustech-room-monitor` 状态目录。网页通过现有 nginx HTTPS 的 `/campus/` 路由和独立密码保护；其他网站路由保持原状。CAS 凭据副本使用 systemd 主机密钥加密，由服务运行时通过 `LoadCredentialEncrypted` 装载。撤销云端校园登录授权时须同时停用 `sustech-campus-dashboard.service` 和 `sustech-room-monitor.service`；停用服务不取消既有预约。

## 附件起点

首次**完整且成功**的 Blackboard 可访问内容扫描仅记录当时已存在的附件 ID，不下载任何旧附件。Blackboard 对尚未开放的子目录会返回 403，这类目录会跳过并在页面显示数量；它们以后开放时，其附件才会被视为新可见资料。后续扫描按附件 ID 识别新资料，放入 `D:\download\<课程名>\`；同名文件带有稳定 ID 后缀，已有文件不会被覆盖。下载失败不会写入已见记录，后续会重试。

若无法确认当前学期课程、读取课程内容失败或基线文件损坏，程序停止附件同步并在页面上显示原因，不把失败扫描当作空基线。资料、基线、页面快照和凭据均不进入 Git。

## 校园登录

校园数据需要个人 CAS 登录。凭据尚未配置时，页面只显示同步错误。获得用户单独授权后，在本机交互终端运行 `configure`，输入学号和隐藏的密码；工具用 Windows DPAPI 加密密码并保存在 `D:\AppData\SUSTechCampusDashboard\credentials.dpapi.json`，同时将目录访问权限限制为当前 Windows 用户。密码不会作为命令参数、日志、明文文件或 Git 文件传递；运行时解密后只在进程内交给上游库。

```powershell
pwsh -NoProfile -File 'D:\Projects\SUSTechCampusDashboard\dashboard.ps1' configure
```

要撤销本机授权，可移除该凭据文件；这不会删除已经下载的课程资料或页面快照。加密文件仅可由当前 Windows 用户身份在本机解密，重装系统或迁移账户后需要重新配置。

## 项目与验证

- 源码：`D:\Projects\SUSTechCampusDashboard`；依赖环境：`D:\Caches\SUSTechCampusDashboard-venv`。
- 本机状态：`D:\AppData\SUSTechCampusDashboard`；课程附件：`D:\download`。
- 云端快照与附件清单：`/var/lib/sustech-room-monitor/dashboard`；附件内容通过 HTTPS 转发，不在服务器长期保存。原有本机 58 项附件基线保持不变。
- 上游依赖固定在提交 `acd20323af6d3bc91c3e89974283d39d85109678`，避免更新静默改变接口行为。
- 离线测试：`D:\Caches\SUSTechCampusDashboard-venv\Scripts\python.exe -m pytest -q D:\Projects\SUSTechCampusDashboard\tests`。

本机与云端的校园账号只读登录均已验收；自动预约写入尚未触发，须以首次由用户在网页提交的监控任务和图书馆系统回执为准。Blackboard 提交状态依据官方尝试状态判定：无尝试、草稿、已提交、读取失败分别显示，不把读取失败标为未提交。图书馆首页空闲数来自当前类别汇总；指定日期和时段请以讨论间面板查询为准。
