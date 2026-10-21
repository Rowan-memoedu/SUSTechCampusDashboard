# 本机作业制册

## 已确定范围

- 首版在已有本机账号运行，默认调用已登录的 Codex CLI；保留 OpenAI-compatible API 适配器。API 适配器尚未做真实服务联调。
- 首次启用实时读取 Blackboard 全部当前作业建立基线，不直接采用可能滞后的成功缓存；仅自动接收此后的新作业。已有作业需逐项点击制册；首次接入验收曾显式选取大学物理 ch9 和线代第五次作业。
- 每门课程首次需要两次真实确认：教材/版次/题号，然后生成后的三册。第二次确认后按整门课程自动处理。来源不明确时暂停并收集补充信息；补充后的候选仍需确认。
- 从课程全部附件、正文、公告及公告附件寻找教师指定教材。只处理课程教师资料，不把学生历史提交交给 AI，不代交作业。
- Blackboard 只给出书名而未提供原件时，可显式将已有本机 PDF 绑定到该课程。不会扫描个人目录猜测教材；课程绑定保存在私密账本中，AI 仅接触被选中并按哈希核对的暂存副本，原件保留不变。
- 新任务由现有 Blackboard 同步结果触发，当前同步周期约 30 分钟；不是学校实时推送。

## 使用与产物

首页 Blackboard 作业区下方有“自动制册”。展开任一作业可选择“为这次作业制册”。“稍后核对”只关闭提示，不确认。后台任务重启后恢复到未完成阶段，重复点击同一作业不会创建第二份任务。

题目册保留原题语言和原图；作答册每题两页，第二页只有横线；答案册中文解释保留 English technical terms 和完整推导。完整英文底稿保留并可下载。源文、独立源题/解答审核、翻译审核和每页 PDF 视觉审核均为独立步骤，机械结构检查不能替代实际审核。

独立源题/解答审核直接附上全部来源页图；最终审核直接附上全部成品页图。运行记录保存附图路径和哈希，再核对返回的逐页覆盖清单。

英文生产逐题检查明确的作图要求，使用确定性绘图工具生成实际答案图并嵌入对应 Solution；坐标表、映射数组或文字作图说明不能代替所求图形，教材原图也不能代替答案图。答案图和生成脚本留在制作目录，原题配图保持独立。独立源题/解答审核还直接接收英文稿引用的全部图片，核对方向、比例、标签、分解与映射关系。

面板明确告知执行器当前阶段及后续调度关系。提取阶段实际核读原页、保存候选题面与来源记录后即可继续；独立源题/解答审核由面板随后单独调用。尚未执行的后续审核不能被当作当前阶段的资料歧义，也不能提前伪造通过。教材、版次、题号、题干或附图确实不明确时仍暂停；独立审核失败仍阻止翻译和交付。此边界也覆盖旧任务中已缓存的整套工作流手册，无需改写原工具快照。

附图先定位原页像素裁切框，由程序直接裁切，再独立对照原页检查。排除误带入的相邻题干与图注，保留原图内自带的批注、手写标记和水印；不重画或擦除。视觉审核未通过时，保留旧候选，把上一轮裁切范围、候选图及审核意见一并送入重试，从原页重新生成附图和 PDF。只有原教材、题面、来源页及已通过的解答均未变化，才保留此前的教材题号确认；派生图片变化及其独立审核另留记录，最终 PDF 仍需首次人工确认。

私密账本及任务原件位于实例数据目录的 `homework/`。当前机器是 `D:/AppData/SUSTechCampusDashboard/homework`。Windows D 盘已有时，默认成品在 `D:/Artifacts/SUSTechCampusDashboard/homework/output`，按课程和题目/作答/答案分类；其他机器使用实例 `homework/output`。可选本地双册副本不包含答案。每次任务另保留完整 ZIP 和交付哈希；不向公众上传。现有同名文件发生变化时另存，保留手写内容。

用户纠正产生新的 attempt；生产阶段重试保留失败候选，保留已通过的前序阶段，只从对应原始输入重新运行失败及依赖阶段。失败不会签收为完成。任务日志留在私密目录 `jobs/<id>/attempt-*/agent-runs` 和 `logs`。

提取阶段重试也先归档旧候选、识别结果和失败原因，再从同一份哈希核对的原件重新提取。既有课程信任和 attempt 保持不变；重试本身不构成教材题号确认或审核通过。

英文生产或独立解答审核失败时，先归档旧稿，再移除当前英文候选及依赖它的译文；执行器按冻结题面和审核反馈重新生成完整解答及所需图形，不在旧候选上局部修补后视作新生产。

编译器缺失会在生成前检查；长时间生成之后、真正编译之前会再次解析当前路径。编译依赖失败记录在编译阶段，重试保留已通过的英文稿、计算审核及译文。旧任务若在成功检查点之后才报错，也从第一个未完成阶段恢复，不因遗留阶段标记重新翻译。已完成编译的任务进入成品审核时不再要求编译器存在。

翻译从完整英文稿重新生成，再进行独立双语审核。审核以科学正确性和内容完整性为准：准确的中文术语、连接词汉化、自然语序及等价表达均可接受，保留英文专业词汇只是输出风格偏好。公式抽取对比作为核查证据，由独立审核判断差异是否改变含义；仅实质性的题意、条件、符号、数值、单位、推导或结论错误及必要内容遗漏会退回。失败后保留旧产物，从已通过的英文底稿重跑翻译。

最终 PDF 审核沿用同一翻译标准。答案册采用原排版器已支持的 `solution_line_spread=1.1`，保持原字号与页边距，减少密集推导末尾公式的孤立续页；每次仍对真实 PDF 全页检查。

## 本机运行条件与接入

- 已登录的 `codex exec`、可用 Python（pypdf / pypdfium2 / Pillow）、Tectonic。沿用已有编译器，不附带大体积 TeX 安装。
- 运行时配置仅优先使用仍存在的可执行文件。Tectonic 依次检查本机配置、`CODEX_TECTONIC_PATH`、PATH；均不可用时，在 Windows 查询当前用户已注册的 ChatGPT 桌面端中的 Codex 安装位置。桌面端升级移除旧目录后可发现新版本，不依赖后台进程继承新的环境变量。
- Codex 使用 `--ignore-user-config --ephemeral --json --sandbox workspace-write` 与 JSON schema。显式启用 unified_exec；不复制 ChatGPT 桌面端模型别名到 CLI。未指定模型时使用 CLI 默认模型。
- 只有暂存教师材料进入 AI 工作区；校园凭据保持在面板进程中。禁用该次 CLI 的子代理与无关技能目录注入。材料内容是数据，不是执行指令。
- 私密账本 `settings.runner` 保存非敏感的运行时路径及后端选择。`backend=codex` 为默认。`backend=api` 可配置 `api.base_url`、`api.model`、`api.key_env`；密钥仅从本机环境变量或 Windows 用户环境变量读取，不保存在账本或 Git。API 工具只访问本次作业目录，来源和工具目录不可写。
- API 适配器保留调用与文件工具协议；不同模型的视觉、输出格式及能力仍需单独验收，不能把接口存在视为可用性已经确认。

## 复用来源

主要复用用户已有工作流 `D:/Artifacts/Homework/2026-Fall/workflow`：`render_booklets.py`、`verify_booklets.py`、`finalize_booklets.py`、使用说明和审核模板；翻译规则来自 `D:/Artifacts/TranslationPrompts`。随软件打包在 `homework_assets/workflow`；单次运行复制到隔离工作区，AI 无权改写工具。复用材料保留原始规则快照；其中较严格的术语保留要求，由本文件及翻译、最终审核阶段的统一科学验收指令覆盖。

研究对照参考 [Canvas Pilot](https://github.com/X-isdoingreat/canvas-pilot) 的课程规则复用思路，以及 [math-worksheets-skill](https://github.com/stellawuellner/math-worksheets-skill) 的制品约束思路；没有复制这两个仓库的代码。校园读取、公告附件解析、认证、CSRF、下载、后台生命周期及界面组件均复用本项目。

## 验收命令与边界

- `python -m pytest tests/test_homework.py tests/test_homework_toolchain.py -q`：基线、去重、并发、重启、两次确认、老师改题、原图保护、课程自动化、异常纠正、不可覆盖交付、API 文件边界，以及失效编译器路径与已通过阶段的恢复。
- `python deploy/homework_preflight.py --output <private-output> --codex`：真实 Codex 随机文件读取 canary，不接触学校材料。
- `python deploy/verify_homework_ui.py --output <artifact-dir> --cache <cache-dir>`：独立 Chrome 无界面测试，320/375/414/768/1280 像素，所有学校响应为隔离样例。其点击不构成用户真实确认。
- `python deploy/homework_local_test.py --course-id <id> --content-id <id> --report <private-report>`：使用同一生产工作流处理指定真实作业，停在下一处人类确认，不自动回答。Windows PowerShell 启动异常时可传 `--cmd-shell` 让该次 Codex 使用 cmd；不改变系统默认 Shell。
- `python deploy/homework_reference.py --root <instance-root> --course-id <id> --file <original.pdf> --title <book-title> --expected-sha256 <sha256>`：将已核对的本机教材登记为这门课程的补充来源。同一原件按哈希去重，源文件或暂存副本变化时拒绝使用；登记不启动任务，也不代替教材题号和 PDF 确认。
- `python deploy/verify_homework_live.py --root <instance-root> --job <id> --output <artifact-dir> --stage awaiting_pdf`：只读检查真实安装版第二次确认框，核对下载 PDF 与交付文件逐字节相同；不发送确认。

当前 Windows 正式分发、本机更新和任务阶段恢复见 [0.5.15 验收记录](RELEASE-0.5.15-ACCEPTANCE.md)；编译器发现与已通过稿件的恢复见 [0.5.14 历史验收](RELEASE-0.5.14-ACCEPTANCE.md)。首次接入过程及当时的限制保留在 [作业接入验收](HOMEWORK-ACCEPTANCE-20261012.md)；历史超时不代表当前回归仍失败。
