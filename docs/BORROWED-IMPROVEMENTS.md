# Blackboard 工具借鉴与实施

当前阶段：已接入同步、详情与原有作业区域。本机 v0.5.4 已读取到 5 门课程、10 项含证据字段的作业；其中一门课程的部分内容受权限限制，按部分读取显示。全部校园验收为读取，`school_writes=0`。

独立 GitHub 环境已通过：Windows Server 2025 的真实临时标准用户下 303 passed、2 skipped；Ubuntu 24.04 下 298 passed、7 skipped（含真实 Nginx 回归）。双平台页面在 320/375/768/1280 像素宽度下通过；实际运行包空实例启动、回环认证、正常退出，以及 Windows 安装、当前用户注册、卸载和个人数据保留均通过。

构建及隐私门禁成功记录：https://github.com/Rowan-memoedu/SUSTechCampusDashboard/actions/runs/38275382586 。预览 Release 已发布：https://github.com/Rowan-memoedu/SUSTechCampusDashboard/releases/tag/v0.5.4 ，发布流程完整通过（运行 38278525961）。匿名公开清单和 Windows 安装器完整下载回读通过：安装器 53,070,781 字节，SHA256 `71e6ed51bbe415c9abc0f760b4295b68e4c2c241d2c9fd478d5afb38cc13129c`，与清单及 GitHub 资产摘要一致。

现有静态稳定源仍是 0.4.3。新版 Windows 10/11 的完整交互安装、真人 CAS 登录及原 TUF 源升级仍需独立复验，不能用 Windows Server 的自动化结果代替这些声明。

## 数据读取

- 按课程保留读取结果；失败课程只能复用同一学期、同一课程的本机缓存。权限拒绝不恢复缓存。附件首次基线仍要求完整扫描。
- 截止时间首选评分列，课程日历必须同时对应当前课程与评分列。只提供日期时不生成时分倒计时；不同来源冲突时列出来源，保留评分列时间。
- 区分历史提交证据、最新尝试状态与成绩。成绩不作为提交证明；读取失败显示未知。历史已提交而最新尝试为草稿时同时显示两项状态。
- 保持上游全局认证的串行锁，不照搬其他工具的并行账号请求。

## 分发

复用 PyInstaller、Inno Setup 与现有 TUF。GitHub Actions 从已存在的版本标签构建 Windows/Linux 包，执行测试、干净启动验证及已知隐私路径检查，生成下载摘要。发布者在 `Build and verify distribution` 填写标签并选择是否发布；默认标记为预览 Release。Windows 验收采用官方 PowerShell 临时本地用户与 `Start-Process -Credential`，账户仅创建在一次性 GitHub 运行器中，不涉及维护者本机账户。

公开资产固定为两个运行包、Windows 安装器、`release-manifest.json` 与 `SHA256SUMS`。工作目录、运行数据、凭据、校园登录状态与发布签名私钥不上传。压缩包检查只覆盖已知隐私路径和结构问题，不代表任意二进制内的秘密都能被检测。

GitHub Release 不覆盖已发布同名版本；重试时先检查既有结果。手动触发保留维护者对标签与发布时间的控制，不在普通提交时发布。下载 SHA256 只核对文件完整性；现有 TUF 元数据仍决定程序更新信任，Authenticode 签名状态在清单中如实记录。

独立 Windows 诊断发现：显式 SYSTEM/Administrators 权限会在原来的继承权限修复后保留。修复现在通过 Windows 原生 `SetNamedSecurityInfoW` 设置当前所有者的单一受保护 DACL，并严格回读；已有正确权限跳过修复，其他用户拥有的目录拒绝修改。原 DPAPI 加解密与 TUF 密钥身份不变。显式权限、继承权限、数据字节保留及独立标准用户均通过回归。

CI 的全新临时目录与打包验证不能代替独立 Windows 标准用户验收。正式上线仍执行项目既有独立安装、旧版本升级、Linux/nginx 与个人数据保留门禁；TUF 离线签名、静态源与服务器续签按 `UPDATING.md` 继续执行。

借鉴来源：

- https://github.com/imx123/bb-dashboard-cuhksz ：课程信息聚合、截止时间来源、提交证据呈现。
- https://github.com/sshwy/pku3b ：标签构建与多平台 GitHub Release 分发。
- https://github.com/RizzoHou/pku-captain ：资料与待办聚合；其明文凭据及可选外部模型数据路径没有用于本项目。

研究证据保存在 `D:\Artifacts\SUSTechCampusDashboard\research\2026-10-12`。

本轮真实读取、失败保留与界面证据：`D:\Artifacts\SUSTechCampusDashboard\borrowed-improvements`；双平台构建日志：`D:\Artifacts\SUSTechCampusDashboard\v0.5.4`。
