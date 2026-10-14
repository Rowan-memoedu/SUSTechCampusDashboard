# 0.5.9 统一发布与本机同步验收

日期：2026-10-13；2026-10-14 补记。本批次在会话中断时保持未完成，未标记本机同步成功。用户随后将交付范围限定为 Windows，并报告后台未启动时公网直达本机地址被拒绝；后续修正进入新版本 0.5.10，不替换本版本的源码标签和资产。

## 固定身份

- 版本与标签：`0.5.9` / `v0.5.9`。
- 冻结源码：`5b7423858a90efc548a779f8fce8d001b494f7d7`。
- [构建运行 38377307220](https://github.com/Rowan-memoedu/SUSTechCampusDashboard/actions/runs/38377307220)：Windows、Linux 各构建一次。后续候选验收、正式发布和更新复用这些文件；不重新构建来晋升正式版。
- [Release](https://github.com/Rowan-memoedu/SUSTechCampusDashboard/releases/tag/v0.5.9)；[公开产物清单](https://124.221.144.155/app/downloads/release-manifest.json)。后续文档提交不改变冻结标签或包内源码身份。

对应产物的 SHA256：

```text
windows-x86_64.zip (73,075,819 bytes)
b79382abe0753e7f95464e055a8ca8a56c45dff7e81649154412709e013de246

linux-x86_64.zip (101,203,719 bytes)
fdb2215cda26f454c9cd74497e22e7d9818f787e453ab10e0072d949a18a1acc

SUSTechCampusDashboard-0.5.9-windows-x86_64-setup.exe (53,111,141 bytes)
cd720dc4a037366debafcd2ffc9c8879288d7df2495b16eb501571213eb0a85e

Windows release-files.json
95612537c6b1c7fa2a122a5483f597d950dcd5797af5d32f029671f371c726c6

Linux release-files.json
f239569d9e8672672496203042eeae77778deaa1a0c44857b426204ca217d891
```

## 本轮修正

- 本机新后台开始响应后，继续等待更新器提交版本指针和安装成功记录，再验证运行文件，避免把短暂的提交窗口误判为版本分叉。
- 已记录成功的公开发布在续跑时直接从公开地址验证；后续验收失败不会再次进入 SSH 发布阶段。若公开身份发生变化，停止并保留未完成状态，不覆盖其他版本。
- HTTPS 大文件中断后尝试断点续传；服务器忽略 Range 时重新写入，错误区间和完整哈希不符均拒绝接受。已用现有公共 HTTPS 文件完成独立续传 canary。
- 相关回归 16 项通过，涵盖指针晚提交、缺少成功记录、回滚、公开身份变化、连接中断、服务器忽略 Range、错误区间及完整文件损坏。
- [0.5.8 阶段记录](RELEASE-0.5.8-ACCEPTANCE.md)保留实际失败及后续回读，不改写旧状态或已发布文件。

## 验收状态

- Windows Server 2025 临时标准用户：333 passed、2 skipped、2 warnings；Ubuntu 24.04：328 passed、7 skipped、2 warnings，包含真实 nginx 回归。实际运行包的干净启动、回环认证、正常退出及 Windows 安装注册、卸载和数据保留均通过。
- 独立 Windows 10 标准用户已完成本批安装器安装，核对实际后台的版本、源码提交和 736 个文件；原登录及数据保留，安装器正常启动浏览器。记录为 `windows-candidate.json`。
- 原 TUF 更新源和静态下载页已发布本批文件。两个平台 ZIP 和 Windows 安装器均从公开地址完整下载，上述 SHA256 全部一致；TUF 严格签名、有效期、回退保护和目标身份检查通过。
- [公开旧版升级 38381644165](https://github.com/Rowan-memoedu/SUSTechCampusDashboard/actions/runs/38381644165) 已于 2026-10-13 完成：Windows 标准用户和 Ubuntu 均通过；2026-10-14 回读确认。独立 Windows 原账号升级及日常本机同步没有完成，原批次仍为 `incomplete`。

## 后续更新

```powershell
python deploy/release.py --version <新版本> --commit <40位源码提交>
```

- 每批固定版本、源码提交和平台产物；修改源码后增加版本号，不替换原包。
- 候选在独立实例验收；日常实例从原正式 TUF 源更新。失败后使用相同参数续跑，保留已通过证据。
- 安装目录中的启动入口负责选择运行包；实际后台由 `updates/current.json` 指向。本机验收核对所选运行目录的完整文件清单及下载 ZIP 的哈希，不把旧启动入口的文件版本当作当前运行版本。
- GitHub、静态站、TUF 及本机均通过身份核对后，批次才写入 `complete`。入口和恢复边界见 [UPDATING.md](../UPDATING.md)。

## 证据与边界

- 本机批次目录：`D:\Artifacts\SUSTechCampusDashboard\releases\v0.5.9`。构建日志为 `build-verification.log`，传输 canary 为 `transport-canary.json`，原资产在 `assets`。
- 本轮继续使用原 TUF 根、发布密钥和服务器自动续签。凭据、令牌、Cookie、私人课程资料和私有截图不进入源码或分发包。
- 校园侧不提交作业、不创建测试预约、不重放待核对写入。首次真人 CAS 登录及未改动业务功能沿用既有适用证据，本轮核对升级中的登录和数据保留。
