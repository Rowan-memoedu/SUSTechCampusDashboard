# 0.5.12：交作业 PDF 预览

日期：2026-10-16。Windows 正式发布与本机同步完成，批次状态为 `complete`（2026-10-16 19:36:35，Asia/Shanghai）；已安装版本的 PDF 预览交互复验通过。

## 行为与边界

- 选择或拖入 PDF 后，在该文件行右侧、移除按钮之前显示“预览”；普通文件不显示。预览保留已选文件和原提交操作。
- 点击后将这一份文件交给本人运行端的只读预览接口，在新标签页使用浏览器原生 PDF 阅读器，与题目册、答案册的 inline PDF 打开方式一致。
- 预览不调用 Blackboard，不提交作业，不写入操作账本，也不把 PDF 留在磁盘。响应禁止缓存。认证、回环 Host、表单秘密令牌和来源检查均保留；仅此只读表单允许 no-referrer 导航的 `Origin: null`，仍必须匹配秘密令牌，普通写接口继续要求原请求头。
- Headless Chrome 的 PDF 阅读器只暴露原生阅读器外壳，普通 GET PDF 也相同；本轮没有将其空白画布截图作为完整 PDF 视觉验收。接口测试核对 inline 类型及原始字节，浏览器测试核对上传、实际新标签页、PDF 响应和原生阅读器入口。

## 固定版本与文件

- 版本／标签：`0.5.12` / `v0.5.12`。
- 源码提交：`32b8213c63316e8c388454e527e773b02bf9602a`。
- [独立 Windows 构建及安装验收](https://github.com/Rowan-memoedu/SUSTechCampusDashboard/actions/runs/38892087065)：347 passed、2 skipped、2 warnings，PDF 交互验收、全新启动、安装注册、卸载及数据保留通过。
- Windows ZIP：73,128,980 字节，SHA256 `9d4a3fdb761217c177094f19051d547e25dc9cc5684656b15b27a69fffc1f075`。
- Windows 安装器：53,210,063 字节，SHA256 `a40acf633d4bad95d9291cdb04889634492c5ca5aa605f2623a5d15badedd7f5`。
- 发布入口仍为 `python deploy/release.py --version <版本> --commit <完整提交>`。同一份 CI 产物用于 GitHub、静态下载页、原 TUF 和本机更新；本轮保留 Linux 0.5.9。
- 更新验收在后台重启后重新取得 CSRF，修正此前升级成功但最后一次检查使用旧令牌而返回 400 的验收脚本问题。

## 正式源与本机验收

- GitHub 正式 Release、静态下载页与原 TUF 目标一致；公开 ZIP、安装器均完整下载并通过上述 SHA256，严格 TUF 签名校验通过。
- [独立 Windows 旧版升级验收](https://github.com/Rowan-memoedu/SUSTechCampusDashboard/actions/runs/38893457023)：0.5.11 → 0.5.12 实际签名升级、原数据保留、未知写入不重放均通过。
- 日常本机通过正式更新器安装；实际后台版本、源码提交、下载 ZIP 和完整运行文件清单一致。文件清单 SHA256 为 `43bef28884c09152d6089fccf35e68cc4947ca98ec827a782cf30b66419515d2`。原登录、下载基线和操作状态保留；本次可核对的已有下载回执文件为 0，不将其描述为非空附件集验收。校园只读状态返回 12 项作业且无错误。
- 本机页面在 375/1280、公开页面在 320/375/414/768/1280 宽度通过，无脚本错误；私人页面只请求回环资源。
- 已安装包使用隔离样例核对 PDF 文件行、预览新标签页、320/375/414/768 宽度、移除按钮和脚本错误，全部通过；未提交真实作业。报告为 `pdf-preview-20261016/installed/report.json`，文件行截图保存在同一受限目录。
- 本批只验证静态发布页的本机打开链接；不把它写成已完成实际 Chrome 外部协议点击。此前该浏览器交互的工具阻塞仍保留历史边界，本次 PDF 预览使用独立无界面 Chrome 验证。

证据目录：`D:\Artifacts\SUSTechCampusDashboard\releases\v0.5.12`、`D:\Artifacts\SUSTechCampusDashboard\pdf-preview-20261016`。旧 0.5.11 未完成记录保留，不将它改写为本次完成证据。
