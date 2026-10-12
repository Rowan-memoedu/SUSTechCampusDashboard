# Blackboard 工具借鉴与实施

当前阶段：已接入同步、详情与原有作业区域，针对性测试及本人账号只读核验通过。隔离页面在 320/375/768/1280 像素宽度下通过，未调用校园写入。宿主 Windows 全量测试为 294 passed、2 skipped、2 failed；包含并行作业工作流的未提交测试，两项失败均为既有 PowerShell 启动超时。GitHub Actions 尚未实际运行，以上内容尚未发布到用户安装包。

## 数据读取

- 按课程保留读取结果；失败课程只能复用同一学期、同一课程的本机缓存。权限拒绝不恢复缓存。附件首次基线仍要求完整扫描。
- 截止时间首选评分列，课程日历必须同时对应当前课程与评分列。只提供日期时不生成时分倒计时；不同来源冲突时列出来源，保留评分列时间。
- 区分历史提交证据、最新尝试状态与成绩。成绩不作为提交证明；读取失败显示未知。历史已提交而最新尝试为草稿时同时显示两项状态。
- 保持上游全局认证的串行锁，不照搬其他工具的并行账号请求。

## 分发

复用 PyInstaller、Inno Setup 与现有 TUF。GitHub Actions 可从已存在的版本标签构建 Windows/Linux 包，执行测试、干净启动验证及已知隐私路径检查，生成下载摘要。发布者只需在 `Build and verify distribution` 中填写标签，先运行验证，再选择发布。

公开资产固定为两个运行包、Windows 安装器、`release-manifest.json` 与 `SHA256SUMS`。工作目录、运行数据、凭据、校园登录状态与发布签名私钥不上传。压缩包检查只覆盖已知隐私路径和结构问题，不代表任意二进制内的秘密都能被检测。

GitHub Release 不覆盖已发布同名版本；重试时先检查既有结果。手动触发保留维护者对标签与发布时间的控制，不在普通提交时发布。下载 SHA256 只核对文件完整性；现有 TUF 元数据仍决定程序更新信任，Authenticode 签名状态在清单中如实记录。

CI 的全新临时目录与打包验证不能代替独立 Windows 标准用户验收。正式上线仍执行项目既有独立安装、旧版本升级、Linux/nginx 与个人数据保留门禁；TUF 离线签名、静态源与服务器续签按 `UPDATING.md` 继续执行。

借鉴来源：

- https://github.com/imx123/bb-dashboard-cuhksz ：课程信息聚合、截止时间来源、提交证据呈现。
- https://github.com/sshwy/pku3b ：标签构建与多平台 GitHub Release 分发。
- https://github.com/RizzoHou/pku-captain ：资料与待办聚合；其明文凭据及可选外部模型数据路径没有用于本项目。

研究证据保存在 `D:\Artifacts\SUSTechCampusDashboard\research\2026-10-12`。
