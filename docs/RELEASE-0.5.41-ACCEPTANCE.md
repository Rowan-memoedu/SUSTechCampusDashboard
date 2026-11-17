# 作业冻结恢复与 Windows 0.5.41

本机教材暂存改用固定长度的内容哈希键命名，原始名称保存在元数据中。同课程、同哈希的账本副本可在原下载位置失效时复用，不再随复用次数叠加文件名前缀。发现阶段持久化来源索引、资源映射和成功的资料选择；下载失败后从可校验检查点继续。旧批次只有在所选资料全部属于该课程已明确绑定的本机教材时，才恢复原 input.json 与原 discover 结果，不猜测远程来源地址。

英文生产逐题建立 Given / Required 清单，将参数及其对应关系、全部分问绑定冻结题面。计算脚本输出实际使用的输入，输入审计记录保留题面哈希和分问对应位置；独立审核从原题重新列出要求，交叉核对正文、脚本与记录。审核退回后保留失败稿和真实审核结果，从冻结题面重做失败及依赖阶段，不把旧反馈当作原题或期望答案。

真实作业已在原账本、原 attempt 和独占 worker 锁下完成恢复。已通过的来源与教材确认保持不变，首轮失败候选及全部原件保留。独立源题/解答审核、翻译科学审核、全部成品页视觉审核及安装版下载字节回读均通过；未代替人工确认，未向学校提交或对外发布私人作业。私密作业与证据仅保留本机，恢复入口见[作业工作流](HOMEWORK-WORKFLOW.md)。

86 项作业与工具链专项回归通过。两份独立教材 PDF 连续多轮账本复用、真实 Codex 随机文件读取、真实生产引擎的数学参数与分问回归及独立运动学 Canary 均通过。固定产品提交的[Windows / Linux 源码 CI](https://github.com/Rowan-memoedu/SUSTechCampusDashboard/actions/runs/44041346642)、[Windows 标准用户构建与安装](https://github.com/Rowan-memoedu/SUSTechCampusDashboard/actions/runs/44043814867)和[真实旧版签名升级](https://github.com/Rowan-memoedu/SUSTechCampusDashboard/actions/runs/44046410296)均通过。

统一发布批次 `D:/Artifacts/SUSTechCampusDashboard/releases/v0.5.41/state.json` 为 `complete`。日常本机通过原正式更新器升级到 0.5.41，登录保护材料、真实基线和操作数据保留；本机页面在375、1280宽度，公共页在320、375、414、768、1280宽度通过，无脚本错误。本机、GitHub、静态页与 TUF 的版本、产品提交、平台产物哈希一致，Linux 分发目标仍为0.5.9。

产品提交为 `74c58c0599538ef476bd8dd92812a57d9aee8942`，Windows ZIP 的 SHA-256 为 `a941d73e39595bf2bec203dcbac3f154a9ec2147c852b90fca49c229b47053e0`。受控源码先完成作业，再完成正式安装版升级；升级后重新核对作业状态、所有审核附图哈希、失败归档及下载字节，均保持一致。私密验收位于 `D:/Artifacts/SUSTechCampusDashboard/unblock-20261117`。

0.5.37、0.5.38及0.5.40的未完成批次与失败回执保留，不改写为已通过。当前运行和后续更新以0.5.41为准；此前图片排版与首次故障等待机制见[0.5.39历史验收](RELEASE-0.5.39-ACCEPTANCE.md)。
