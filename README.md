# 南科大校园面板

基于 [`sustech_survival`](https://github.com/dumixthestpd/sustech_survival) 的本机只读工具。一个网页显示每日概览、Blackboard 课程与作业状态及剩余时间、TIS 考试和待评教、图书馆空闲数与本人预约、E-Hall 可用场地与本人预约。命令行也可以单独查询预约。

## 运行

Windows PowerShell 7：

```powershell
pwsh -NoProfile -File 'D:\Projects\SUSTechCampusDashboard\dashboard.ps1' serve
```

在本机打开 `http://127.0.0.1:8765/`。启动时立即同步，命令运行期间每 30 分钟同步一次；关闭命令后停止，下次启动会补查期间新增的附件。页面每分钟更新显示，并以同步时间标明数据新旧。只监听 `127.0.0.1`。

其他命令：

```powershell
pwsh -NoProfile -File 'D:\Projects\SUSTechCampusDashboard\dashboard.ps1' sync
pwsh -NoProfile -File 'D:\Projects\SUSTechCampusDashboard\dashboard.ps1' bookings
pwsh -NoProfile -File 'D:\Projects\SUSTechCampusDashboard\dashboard.ps1' status
```

`sync` 只读取校园系统并下载新附件；不提交作业、不预约场地、不评教。`bookings` 输出图书馆当前空闲数、未来 30 天本人图书馆预约、E-Hall 可用场地与本人预约。

## 附件起点

首次**完整且成功**的 Blackboard 扫描仅记录当时已存在的附件 ID，不下载任何旧附件。后续扫描按附件 ID 识别新资料，放入 `D:\download\<课程名>\`；同名文件带有稳定 ID 后缀，已有文件不会被覆盖。下载失败不会写入已见记录，后续会重试。

若无法确认当前学期课程、读取课程内容失败或基线文件损坏，程序停止附件同步并在页面上显示原因，不把失败扫描当作空基线。资料、基线、页面快照和凭据均不进入 Git。

## 校园登录

校园数据需要个人 CAS 登录。凭据尚未配置时，页面只显示同步错误。获得用户单独授权后，在本机交互终端运行 `configure`，输入学号和隐藏的密码；工具会把凭据保存在 `D:\AppData\SUSTechCampusDashboard\credentials.txt` 并将文件读取权限限制为当前 Windows 用户。密码不会作为命令参数、日志或 Git 文件传递。上游库的会话只驻留内存。

```powershell
pwsh -NoProfile -File 'D:\Projects\SUSTechCampusDashboard\dashboard.ps1' configure
```

要撤销本机授权，可移除该凭据文件；这不会删除已经下载的课程资料或页面快照。

## 项目与验证

- 源码：`D:\Projects\SUSTechCampusDashboard`；依赖环境：`D:\Caches\SUSTechCampusDashboard-venv`。
- 本机状态：`D:\AppData\SUSTechCampusDashboard`；课程附件：`D:\download`。
- 上游依赖固定在提交 `acd20323af6d3bc91c3e89974283d39d85109678`，避免更新静默改变接口行为。
- 离线测试：`D:\Caches\SUSTechCampusDashboard-venv\Scripts\python.exe -m pytest -q D:\Projects\SUSTechCampusDashboard\tests`。

当前未配置校园账号，真实课程、附件下载、预约和作业状态仍需登录后验收。Blackboard 提交状态依据官方尝试状态判定：无尝试、草稿、已提交、读取失败分别显示，不把读取失败标为未提交。图书馆空闲数来自当前类别汇总；不表示指定日期和时段可预约。
