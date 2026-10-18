# 0.5.14：桌面运行时升级后的作业编译恢复

Windows 正式发布、本机更新和真实作业恢复均已通过。发布批次于 2026-10-18 14:00:22（Asia/Shanghai）完成。

## 修复行为

- 运行时路径优先使用仍存在的可执行文件。Tectonic 的旧配置失效后继续检查环境变量和 PATH；Windows 后台环境也过期时，查询当前用户注册的 ChatGPT 桌面端中的 Codex 安装位置。查询隐藏运行且限制为 20 秒，不遍历受保护安装目录，不新增编译器安装。
- 生产前检查编译依赖，真正编译前再次解析路径，覆盖生成过程中桌面端更新的情况。编译器缺失记在编译阶段；已经编译完成的 PDF 继续审核不再依赖编译器。
- 重试兼容旧任务中“阶段标记滞后于成功检查点”的情况，从第一个未完成阶段恢复。已通过的英文稿、计算审核、译文和翻译审核保留；真正失败的翻译仍从英文稿重新生成。
- 翻译继续按科学正确性和必要内容完整性验收，原三册核心脚本和首次双确认规则不变。

## 验证

- 作业与运行时定向回归：45 passed。本机全套：365 passed、2 skipped、2 warnings；独立 Windows 标准用户同样为 365 passed、2 skipped、2 warnings。
- 隔离测试覆盖失效配置、环境变量、PATH、注册安装回退、有效配置优先、注册查询失败和超时、缺少编译器时提前停止、旧阶段标记恢复和已编译 PDF 的继续审核。
- 本机真实 canary：在配置与进程环境均指向已移除版本时，仍发现当前 Tectonic 并执行成功；LaTeX Doctor 的数学公式编译通过。当前 Codex Exec 随机文件读取 canary 通过。
- 真实失败作业通过安装版的认证重试接口恢复到 `complete`，只新增最终成品审核。原稿、译文和此前审核记录哈希一致；最终 25 页均实际附图审核、覆盖清单和图像哈希一致。三个认证下载响应与本地交付文件哈希一致。
- 其他已完成作业、课程自动化、原基线及凭据保持一致；学校写入为 0。具体课程和作业材料仅留在私密证据目录。
- 独立 Windows 全新启动、安装注册、卸载后数据保留，以及真实 0.5.13 → 0.5.14 签名升级通过。
- GitHub、静态源、TUF 和本机源码身份及产物哈希一致，本机无需重新登录。实际页面在本机 375/1280 宽度、发布页 320/375/414/768/1280 宽度只读验收通过，无脚本错误。已下载回执文件计数为 0，此项不描述为非空附件集验收。
- 最后一次 GitHub 查询曾遇 TLS 握手超时；按相同参数续跑原批次后通过，没有重建或替换已发布文件。

## 发布身份与证据

- 版本：`0.5.14`；源码：`ca27db99583258dda8f27df03bde649d8c1d9103`。
- [Windows 构建与独立验收](https://github.com/Rowan-memoedu/SUSTechCampusDashboard/actions/runs/39100615059)；[独立 Windows 旧版升级](https://github.com/Rowan-memoedu/SUSTechCampusDashboard/actions/runs/39101278506)。
- ZIP：73,133,421 字节，SHA-256 `842fc2f9dbc201589f4ffdf5ff9e99a1d340298920b403361183d567f2870b0c`。
- 安装器：53,211,662 字节，SHA-256 `830c549746369d583afe377dab06626cfb97fdb9be0b53a05a8557625044f362`。
- 运行文件清单 SHA-256：`6d024cb46c22013c5ec0c47bac1dd5d409fa1be2c82ddb6874a2e8ca1ee932a2`。
- 发布批次：`D:/Artifacts/SUSTechCampusDashboard/releases/v0.5.14/state.json`，状态 `complete`。私人作业证据：`D:/Artifacts/SUSTechCampusDashboard/homework-recovery-20261018/acceptance.json`。

继续使用统一入口 `python deploy/release.py --version <版本> --commit <完整提交>`。Linux 分发目标保留为 0.5.9；本次没有扩大 Linux 交付范围。测试中的两条上游弃用提示仍保留；API 模型适配器的真实服务联调仍不在本次验收范围内。
