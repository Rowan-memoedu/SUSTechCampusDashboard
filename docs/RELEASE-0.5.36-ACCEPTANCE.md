# CS109 Lab 自动化与 Windows 0.5.36

CS109 Lab 复用作业制册的 Blackboard 认证、附件下载回执、任务账本、Codex 执行器和恢复后台。从当前 Week 9 开始持续读取 Labs，按真实 OJ training 获取题目；不回填历史周次。Lab 分支只提取 PDF 文本，不渲染页面，不生成图片或题册 PDF。工作机制和配置前提见 [Lab 自动化](LAB-AUTOMATION.md)。

题面为中文，文件与目录保留英文。练习骨架使用 //Todo，与参考题解并列。原题 API、包名、入口和输出保留，不额外增加校验或辅助设计。独立来源审核和本地 Java 检查通过后才写入已有 Academy 工程；已有学习者答案保留，来源修订冲突暂停核对。

Week 9 的真实来源获取、重新生成、独立审核和本地检查已通过。两道 OJ 题共执行七组 Java 样例与边界检查；只在隔离工作区编译参考实现。原有四份主要源码保留，八份原示例保留在注册清单中。Lab 工作区没有页面图片，唯一 PDF 为原始来源文件。未提交 OJ，也未向 Blackboard 写入。

本地回归 449 项通过、2 项跳过。隔离页面覆盖 320、375、414、768、1280 宽度及八种状态，无横向溢出和脚本错误。原生模块匹配在实际 IDEA 的三组练习与题解模块上通过，完整名称匹配不会把 _solution 计入原练习。

IDEA 完整加载已通过原生回执：原题为 EduTask，参考题解与概览为 TheoryTask；四个 Java 任务各有三个 Gradle 模块，源码与示例均在可编辑源码根内，两道原题的 Hydro Check 入口均可用。验证只调用 canCheck，没有提交代码。此前启动及 Gradle daemon 遇到 Java 原生内存分配失败，任务保留在 connect 阶段；可用内存恢复后从该阶段通过，没有重新生成题目或覆盖答案。未调整 IDEA 永久内存配置、操作系统内存或页面文件设置。

0.5.36 已完成统一发布，批次状态为 complete，本机实际运行、GitHub 正式版、静态下载页与 TUF 更新源的版本、源码身份和产物哈希一致。源码身份为 834565d3a23f309b8f182b79c1bfbb66d93c5205；统一批次位于 D:/Artifacts/SUSTechCampusDashboard/releases/v0.5.36/state.json。[Windows 普通用户构建与安装](https://github.com/Rowan-memoedu/SUSTechCampusDashboard/actions/runs/42781858176)和[真实旧版签名升级](https://github.com/Rowan-memoedu/SUSTechCampusDashboard/actions/runs/42783684629)均通过。本机登录、私密数据与校园读回检查通过，主页、设置页和公共下载页的既定窄屏与桌面尺寸验收通过，无脚本错误。

本机在提交最后安装回执时曾遇到 Windows 拒绝访问并退出。两版完整安装文件核验通过后，保留已签名的 pending 包，恢复已验证的前一选择，由原正式启动器重放该次更新。最终 installed 回执由更新器写出，没有人工填写成功状态；原批次继续通过完整发布验收，没有重建或替换同版本安装包。

安装后已启用 Lab 自动化，实际 375、1280 宽度页面显示 Week 9 已完成、IDEA 已连接，原有四个主要文件保留。重新读取当前 Blackboard PDF 与 OJ training 成功，来源去重后仍只有一条已完成 Week 9 任务，没有重复生成。开关、完成回执和已有作业设置均持久保留。

真实来源、生成、发布保留性和原生诊断证据位于 D:/Artifacts/SUSTechCampusDashboard/lab-automation-20261110。课程材料、凭据、私密配置和运行账本不进入仓库；既有无关未跟踪文档保持原状。
