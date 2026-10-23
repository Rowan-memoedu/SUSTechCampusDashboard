# 直接访问与作业同步恢复

本机访问改为持久授权，补齐 Windows 静默启动项。后台同步、附件扫描和作业详情使用独立 Blackboard 会话；仅共享登录缓存的更新串行执行。作业详情最多并发两份，浏览器读取有超时与重试入口。页面提供立即同步、恢复可见和联网触发，并显示各数据源的成功更新时间。

学校触发人机验证时，停止凭据发送和后台重复尝试。“本机与更新”的“恢复校园登录”使用官方学校窗口，匹配当前校园账号后接回同步；仅 CAS 的安全 Cookie 在本机使用 DPAPI 加密保存，最长十二小时。验证码由用户完成。该接续流程通过隔离测试，真实页面入口已核验；实际校园认证随后自行恢复，本轮没有执行人工验证码。

附件扫描需要原先的 60 秒单次读取等待，交互详情保留 20 秒。0.5.20/0.5.21 的 20 秒统一等待导致一门课程目录扫描超时。独立实测恢复后该课程约 92.8 秒扫描成功；其余四门课程也通过，总共 59 个可见附件。没有用失败扫描替换原附件基线。

## 已核验

- 全套回归 387 项通过、2 项跳过，两条上游弃用提示；新增测试确认附件扫描使用长等待，成功或中断后恢复交互等待。
- 0.5.22 安装版同步期间，两份待提交作业分别约 0.600 秒、0.610 秒返回可提交表单，无警告；状态读取约 0.008–0.015 秒。
- 真实 Chrome 普通网址进入，无需解锁，Cookie 为 180 天有效、HttpOnly、SameSite=Strict。
- Windows 稳定安装入口以独立进程运行，后台优雅停止后通过登记的 Run 命令重新启动，实际运行 0.5.22；跨测试命令退出后仍可访问。电脑与用户浏览器未重启。
- 原有作业、课程信任、自动化设置、凭据、令牌、操作账本和附件基线全部保留；学校业务写入为零。

0.5.22 正式安装版完整同步、后台停启和真实 Chrome 页面终验通过。Blackboard、课表和附件来源均刷新，最终错误列表为空；两轮完整同步后原有记录仍保留。普通网址为 `http://127.0.0.1:18765/`。真实 Chrome 提交控件截图位于私人证据目录的 `assignment-submit-controls-22.png`。

[Windows 构建、标准用户新装与数据保留](https://github.com/Rowan-memoedu/SUSTechCampusDashboard/actions/runs/39980977476)和[独立旧版升级](https://github.com/Rowan-memoedu/SUSTechCampusDashboard/actions/runs/39982329411)均通过，正式批次为 complete。正式 TUF、GitHub stable 与本机运行身份一致。发布页五种宽度、本机两种宽度均通过，脚本错误为零。

ZIP SHA-256：`98806e601899aca2ab3feb2e7bcd53a04447eae905a988296652d63da8a3c112`；安装器 SHA-256：`232ec25ba2200275f7c55ff35dd6e75be360dc90e4b09fa61998957ad6f05bcb`。本机选择记录、正式 TUF 与 GitHub 产物复用同一批文件。

源码：`f892904bb856d9c5ddc1f9fc2ae3c70c2ea8ccf5`。发布批次：`D:/Artifacts/SUSTechCampusDashboard/releases/v0.5.22/state.json`。私人验收证据：`D:/Artifacts/SUSTechCampusDashboard/direct-open-sync-recovery-20261023`。Linux 目标保持原版本。
