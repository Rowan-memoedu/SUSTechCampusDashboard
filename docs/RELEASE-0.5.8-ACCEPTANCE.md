# 0.5.8 统一发布验收

日期：2026-10-13。本页保留 0.5.8 阶段证据。公开发布、双平台旧版升级和独立 Windows 升级均通过；本机验收因提前读取尚未提交的更新指针停止，原批次状态保留 `incomplete`。修正验收时序和续跑逻辑后增加版本号为 0.5.9，继续执行完整统一流程；未替换 0.5.8 的原资产或标签。

## 固定源码与产物

- 版本与标签：`0.5.8` / `v0.5.8`。
- 源码提交：`af1fd58cbee5eec945b1064412155ee88bd7f021`。
- 应用源码身份以此标签和产物清单为准；后续文档收尾提交不改写标签，也不把文档提交冒充运行包源码提交。
- [构建运行 38361648294](https://github.com/Rowan-memoedu/SUSTechCampusDashboard/actions/runs/38361648294)：Windows、Linux 各构建一次；后续安装、下载和签名更新复用这批文件，正式晋升没有重新构建。
- [Release](https://github.com/Rowan-memoedu/SUSTechCampusDashboard/releases/tag/v0.5.8) 与[该版本固定清单](https://github.com/Rowan-memoedu/SUSTechCampusDashboard/releases/download/v0.5.8/release-manifest.json)记录完整提交、文件大小、包哈希与运行文件清单哈希。

SHA256：

```text
Windows ZIP (73,073,294 bytes)
87affd715ceab62b8d927f9816bbfa99ae9b6d05980877229cae4963e0c9eddc

Linux ZIP (101,203,564 bytes)
3279d7003085acb4c92ecd1fc0ddfa7672c7c5fcd4ad8930022292f3ef02a019

Windows installer (53,112,231 bytes)
6b3af3e10c8f8792d03c885a4260a25e8a913f268ecb44a7d4cf3c31a8131297

Windows release-files.json
236ca26309757e5de6c62a7767eac90c82f9dc6d6044a96ef07afc4765073c74

Linux release-files.json
323d8271b430902836570fdb56c34fdcf3ca52dc8fb467e3d519e68181c17a10
```

## 已通过的验收

- Windows Server 2025 临时标准用户全量回归：325 passed、2 skipped、2 warnings；Ubuntu 24.04：320 passed、7 skipped、2 warnings，包含真实 nginx 用例。实际可执行包的空实例启动、回环认证、正常停止及 Windows 安装注册、卸载和数据保留均通过。
- 独立 Windows 10 标准用户：运行本批安装器，核对实际后台的版本、完整源码提交和 736 个文件；原登录和数据保留，安装器正常启动浏览器。记录为 `windows-candidate.json`。基础虚拟磁盘只读，本轮使用独立差分盘。
- 本轮新增的 `current` / `ahead` / `mismatch` 更新状态及原作业区域通过界面验收；业务功能未变动的既有只读证据继续保留，见[借鉴实施](BORROWED-IMPROVEMENTS.md)与[增量功能验收](INCREMENTAL-SERVICES-ACCEPTANCE-20261012.md)。

- 公开地址的 Windows ZIP、Linux ZIP 和 Windows 安装器均完整下载并核对上述 SHA256；TUF 的签名、有效期、回退保护和两个目标身份校验通过。安装器两次网络中断后从同一 HTTPS 地址续传，完整文件哈希通过后继续原批次，没有替换构建产物。
- [公开升级运行 38368391432](https://github.com/Rowan-memoedu/SUSTechCampusDashboard/actions/runs/38368391432)：Windows 标准用户与 Ubuntu 均从原 0.4.3 公开 ZIP 实际升级至 0.5.8；源码提交、平台 ZIP 哈希、数据保留及未知写入不重放均通过，未重新构建。

- 独立 Windows 10 原账号也从 0.4.3 完成真实签名升级；736 个运行文件、完整源码提交、原凭据及已有资料摘要全部通过。随后重新检查正式更新源，状态为 `current`，版本为 0.5.8；记录为 `windows-upgrade.json` 和 `windows-stable-channel.json`。完成后仅关闭本轮独立虚拟机。

本机已从原正式更新源运行 0.5.8。原验收在新后台响应后立即读取指针，早于监督进程提交指针，因而中止。随后现场回读确认运行身份、`current.json` 与安装成功记录均为 0.5.8，正式通道状态为 `current`；这些后续事实没有用来改写原失败记录。0.5.9 已加入等待指针和成功记录提交的回归测试，并沿用同一发布入口继续验收。

## 后续更新入口

```powershell
python deploy/release.py --version <新版本> --commit <40位源码提交>
```

- 修改源码后增加版本号，不能移动既有标签或替换已发布文件。
- 同一批次失败后使用相同参数继续，保留已通过的构建和验收证据；上传先核对远端哈希，跳过已完整传输的文件。
- 候选使用独立实例；日常本机通过原正式 TUF 更新器安装。安装目录中的旧启动入口与实际选中的运行包分开核对，运行包由 `updates/current.json` 指定。
- 只有公开渠道、本机运行身份和对应产物哈希全部通过，批次才写入 `complete`。具体顺序与恢复边界见 [UPDATING.md](../UPDATING.md)。

## 证据与隐私

- 批次目录：`D:\Artifacts\SUSTechCampusDashboard\releases\v0.5.8`；包含状态日志、原构建资产、公开回读文件和独立 Windows 验收结果。
- 构建日志：`build-verification.log`；公开升级日志：`public-upgrade-verification.log`；界面报告：`D:\Artifacts\SUSTechCampusDashboard\unified-ui\coursework-ui.json`。
- 原 TUF 根、发布密钥和服务器续签继续使用。凭据、Cookie、令牌、私人课程内容、数据保留摘要及含私人信息的截图不进入仓库或公开产物。
- 本轮没有提交作业、创建预约或其他校园写入，`school_writes=0`。首次真人 CAS 登录沿用原独立 Windows 验收；本轮验证已有登录的升级保留。
- 0.5.5 至 0.5.7 保留为未晋升的候选与失败证据。修复了安装器等待子进程、异步浏览器启动误判和批量上传中断后的恢复；未改写旧失败记录。一次冷启动失败未确定根因，后续原包启动成功，不将其表述为已定位修复。
- 本批次发生过 HTTPS 下载中断、SSH 连接关闭及测试代理不可用，均保持 `incomplete`。Windows 10 首次签名下载返回 `ProtocolError` 后，API 实测仍运行 0.4.3，`configured=true`、`credential_error=false`；原可用版本与登录没有被失败下载替换。
