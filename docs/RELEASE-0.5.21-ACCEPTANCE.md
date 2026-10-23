# 直接访问、作业同步与学校验证接续

0.5.20 已在日常本机安装。真实 Chrome 已确认普通链接可进入，无需发布页再次授权；Cookie 为 180 天、HttpOnly、SameSite=Strict。Windows 后台启动项已补齐并回读。测试终端退出会清理其子进程，因此另外使用 Windows 独立进程启动，跨命令回读和真实 Chrome 访问均通过。

作业详情与附件扫描已解除长时间锁竞争，源码真实读取两份待提交作业约 2.684 秒和 0.650 秒。后台每五分钟同步，页面增加立即同步、恢复可见和联网触发、分来源更新时间及读取超时重试。原有正式生产材料和账户数据保持原位。

真实安装验收发现学校 CAS 登录页要求滑块人机验证，账号密码接口返回登录页。0.5.20 发布批次因此保留 incomplete，没有把校园实时读取宣告通过。

0.5.21 增加官方窗口人工验证接续：检测到验证码立即停止凭据 POST 和重复尝试；在“本机与更新”点击“恢复校园登录”，用户亲自在学校窗口登录。读取 Blackboard 本人身份并与当前账号匹配后，才接回同步。仅 CAS 域的安全 Cookie 在原 Windows 用户下以 DPAPI 加密保存，最长十二小时；其他账号、其他域与不安全 Cookie 不保存。没有自动解验证码，也没有学校业务写入。

新增认证测试覆盖验证码前停止、重复尝试停止、SSO 服务 Cookie 范围、账号及过期边界、拒绝不安全会话、人工登录账号不匹配。全套 384 项通过、2 项跳过，新增账号不匹配测试另外通过；最终完整测试由正式 Windows CI 运行。

固定源码 `42871bbedd9b6390b4f296b0832d745b7d5ef078`；[Windows 构建](https://github.com/Rowan-memoedu/SUSTechCampusDashboard/actions/runs/39976474452)和[独立升级](https://github.com/Rowan-memoedu/SUSTechCampusDashboard/actions/runs/39979311398)通过。正式批次 complete，本机运行 0.5.21，真实 Chrome 恢复入口可见。进一步等待完整同步发现附件目录需要更长读取等待，最终修正见 0.5.22；本版不能替代该项终验。

证据：`D:/Artifacts/SUSTechCampusDashboard/releases/v0.5.21/state.json`；私人只读记录位于 `D:/Artifacts/SUSTechCampusDashboard/direct-open-sync-recovery-20261023`。Linux 目标保留原版本。电脑和用户浏览器未重启，没有提交测试作业。
