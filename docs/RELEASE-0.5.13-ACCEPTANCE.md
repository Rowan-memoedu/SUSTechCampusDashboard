# 0.5.13：已提交作业的 PDF 预览

日期：2026-10-16。Windows 正式发布与本机同步完成，批次于 Asia/Shanghai 2026-10-16 21:10:51 标记 `complete`；真实已提交 PDF 的只读预览复验通过。

## 行为

- 已提交作业的附件行保留文件名和下载入口；PDF 右侧新增“预览”，在新标签页使用与已选 PDF、题目册和答案册一致的原生 PDF 阅读器。
- 沿用经过白名单校验的 Blackboard 附件地址与本人学校会话。仅预览请求由本人运行端读取；普通下载保留原流程。
- 预览先验证 PDF 文件头，再以 `application/pdf` 和 `inline` 返回。学校登录页、错误 HTML 或其他文件不会伪装为 PDF。连接完成、关闭或首块读取失败均释放上游连接与认证锁。
- 不向学校提交内容，不创建操作，不保存附件副本。原认证、Host 校验、资源路径限制和禁止缓存继续生效。

## 固定身份与验收

- 版本／标签：`0.5.13` / `v0.5.13`。
- 冻结源码：`8585b56aa225cddaa97210cb42b6910adef5ee03`。
- [Windows 构建与独立验收](https://github.com/Rowan-memoedu/SUSTechCampusDashboard/actions/runs/38902602015)。
- 独立 Windows 标准用户：351 passed、2 skipped、2 warnings；PDF 浏览器交互、全新启动、安装注册、卸载和个人数据保留通过。
- Windows ZIP：73,130,070 字节，SHA256 `9af245d3d81f989f7e89067b4d65c21a2ea79b06fabb7b14c65e9b5468c3f01d`。
- Windows 安装器：53,205,456 字节，SHA256 `03e4f80319716f9e221a49ef3120ad7abb9fd395bb88f34d56d68fae8ca9371b`。
- 运行文件清单 SHA256：`0f0429ec5fa8854d20412921ea6ff66230561f4ca522b0d6027193be200f35e2`。
- 本机相关回归 40 项通过；隔离浏览器验证已选 PDF、已提交 PDF 的新标签页打开及原下载入口保留，通过且无脚本错误。
- GitHub 正式 Release、静态下载页、原 TUF 与本机的版本、源码及产物哈希一致；公开 ZIP 和安装器完整回读、严格签名校验通过。
- [独立 Windows 旧版升级](https://github.com/Rowan-memoedu/SUSTechCampusDashboard/actions/runs/38904199359)：0.5.12 → 0.5.13 的真实签名升级、数据保留和未知写入不重放通过。
- 本机从正式源安装，实际运行包及完整文件清单与上述身份一致；原登录、基线和操作记录保留。可核对的已有下载回执文件为 0，不将其描述为非空附件集验收；校园状态读取 12 项作业，无错误。
- 已安装包实际读取一份本人已提交 PDF，通过附件行“预览”打开新标签页；另在内存完整读取同一受保护预览并确认 PDF 可解析。原下载入口、320/375/414/768 宽度和无脚本错误检查通过，学校写入为 0。
- 实际 PDF 的正文、课程、文件名与截图不进入公开材料；私人报告为 `submitted-pdf-preview-20261016/installed/report.json`。本轮未重测历史 Chrome 公网外部协议点击，不把静态链接检查当作该项交互通过。

仍使用 `python deploy/release.py --version <版本> --commit <完整提交>`。Windows 只构建一次，各渠道和本机复用同一批文件；本批 Linux 目标保持 0.5.9。

证据目录：`D:\Artifacts\SUSTechCampusDashboard\releases\v0.5.13`、`D:\Artifacts\SUSTechCampusDashboard\submitted-pdf-preview-20261016`。私人课程、附件和令牌不进入公开报告、Git 或分发资产。
