# MathBank 账号与空间版

- 用户明确要求直接采用完整 MathBank，核心界面和业务代码以 `engine/` 为准，不重写为 Next/React。
- `engine/` 是原项目的源码快照。其开发规范保留于 `engine/AGENTS.md`；平台接入集中在 `platform_app/`，必要的运行时适配集中在 `platform_app/worker.py`。
- 原项目及旧 teacher-prep 数据都不自动迁移、覆盖或删除。测试使用临时数据目录。
- 平台负责认证、权限、空间、教材、团队 AI 和来源记录。每个空间（一个学科 + 一个学段 + 一套教材）使用独立原生 MathBank 数据库、图片和任务，浏览器草稿按账号和空间隔离。
- 所有 worker 仅监听回环，并验证内部随机令牌。外部访问必须经过平台权限检查，不能直接暴露 worker。
- 不复制 `.env`、数据库、上传图片、备份或其他凭据到源码。保留 AGPL 许可和来源清单。
- 修改后运行平台权限/隔离测试、上游核心回归和浏览器工作流验证。没有真实调用模型时不得宣称识别质量已验证。
- Docker/TUN 操作遵守工作区及 `C:/Users/14131/.codex/AGENTS.md`，不要修改宿主机网络。
- 部署（用户 2026-10-20 确定）：本系统独立运行于 `http://localhost:3011`，由本目录 `docker-compose.yml` 管理（项目名固定 `mathbank`、容器 `mathbank`、数据 `./data`）。更新代码用 `docker compose up -d --build`（`deploy/Dockerfile` 以 `mathbank:base` 为底只替换代码），更新前 `docker tag mathbank:current mathbank:previous` 留回滚；新机器先 `docker build -t mathbank:base .`。旧 3010 实例（容器 `mathbank-platform-mathbank-1`、目录 `D:/工作/edusystem-gpt`、镜像 `mathbank-platform:local`）已放弃：不再同步、不读取其数据，也不要停止、删除或重建它，除非用户明确要求。
- Git：本目录为 Git 仓库（主分支 `main`）。`data/`、`test-results/`、上传目录、数据库、`.env` 与任何密钥、MinerU 结果及账号初始密码都不得提交；提交前检查 `git status`。只在用户要求时提交或推送，不加署名行。
- 账号、空间管理在原版工作台的“我的”工作区内完成，公共片段为 `platform_app/web/account.html`；无可访问空间的新账号复用同一片段引导创建空间。禁止恢复独立管理站或把管理页嵌入 iframe。
- 登录与管理界面复用原版主题变量，主题偏好可跨空间共享；草稿和题篮仍按账号及空间隔离。进入“我的”只切换可见工作区，不重载页面或清空编辑状态。
- 当前系统 Q&A 内容唯一来源为 `platform_app/web/qa-data.js`，用纯文本渲染。由平台替换资源引用，保留原项目快照。内容描述已经实现的功能及限制，不继承原版群聊出处或单机更新说明。
- 账号界面改动须执行 `tests_platform/account_ui_smoke.py`，在隔离数据目录验证真实创建账号/空间、权限、密码修改、Q&A、手机和暗色界面，不能对正式业务数据执行测试写入。
- 多租户回归入口为 `tests_platform/test_multitenant.py` 和 `scripts/run_tenant_ui_tests.py`。后者自动创建临时数据、动态回环端口，依次运行首次引导、账号、多浏览器和空间工具（`workspace_ui_smoke.py`：切换、离开确认、跨空间选题、试卷目录树、侧栏收起、选项排列）测试，结束时正常关闭平台与其 worker；可传脚本名只运行其中几个。首次引导必须先于公共空间创建，否则新用户本就能访问公共空间，不应断言为空空间。
- 空间管理入口依据实际空间权限及所属团队显示；成员权限弹窗须从 `/platform/api/spaces/{id}/members` 获取当前有效权限，不能默认覆盖已有编辑授权。该接口仅空间所有者、超级管理员及所属团队管理员可读，不返回密码或会话信息。
- 同一浏览器其他标签页重新登录后，旧页 API 读取携带旧会话令牌时返回 409 与 `X-MathBank-Session-Changed: 1`，前端重新打开当前账号工作台，避免新账号结果写入旧账号草稿命名空间。普通业务冲突 409 不触发此行为。
- “我的”切换必须隐藏全部原工作区容器，包括 div 类型的题库与组卷，不能仅选择 section；保留录入与题篮状态。目录路径按已访问 ID 防环，不能用固定层数上限截断合法路径；目录引用须校验正整数、空间与类型。

- 一个空间 = 一个学科 + 一个学段 + 一套教材 = 一个题库（用户 2026-10-15 确认）。三者由创建时选择的教材决定，`spaces.textbook_id` 有触发器禁止修改；学科、学段一律从教材联表读取，不在空间表冗余。空间名称仅所有者或所属团队管理员可改。
- 旧多学段空间由 `migrations.single_bank_spaces` 一次性升级：先在线备份 platform.db，新空间沿用旧分区 id（题库目录、链接、浏览器草稿不变），多学段空间按学段拆分并复制成员与试卷目录，拆分明细写入备份旁 JSON。题库文件不移动。迁移在单个事务内完成并做外键校验，失败即回滚。
- 迁移统一登记在 `platform_migrations`，新安装直接建当前结构并标记全部已完成；新增结构变更须追加迁移步骤，不得在启动时做无标记的重复迁移。
- 平台界面元素放进原版工具栏时使用原版控件类（glass-btn、glass-select、records-secondary-action），平台自有样式只写在 `platform_app/web/platform.css` 并使用主题变量；不得注入内联样式表或另起一套配色。原版渲染器会重建的区域用 MutationObserver 重新挂载，不改引擎脚本。
- 后续 MinerU、RAG、学生端按 `docs/EXTENSIONS.md` 的约定接入：以空间为授权与索引单位，变更流取自 `audit` 中的 `bank:%` 记录，新角色不在 `store.TEACHER_ROLES` 内即无题库权限。

- PhysicsBank 合并采用 per-worker `mathbank.subject_profile`。必须在导入引擎前配置学科/学段/元数据；热更新仅在 worker 空闲时更新。保留当前原文核对、公式锁、逐图核验与 Word/PDF 保真链路，不用旧物理分支整文件覆盖。
- 上游有意修改的文件以 `platform-adaptations.json` 记录哈希，来源清单 `upstream-manifest.json` 不覆盖。物理适配须验证数学隔离、管理员题型与难度、单位方向、实验/计算/简答题和真实导出。不得把协议/假响应测试描述为模型质量验收。

- 试卷以 metadata_json.question_figure_layouts_v1 按题 ID 保存独立题图排版。题篮也必须持久化快照，刷新题库与导出前重验不能覆盖。普通配图调整继续更新题库默认，仅当前题篮同步；旧卷启动迁移先 SQLite 在线备份，再固定当前默认值。保持上游数据库 schema 版本不变。专项 tests_platform/paper_layout_ui_smoke.py 验证 A/B 试卷隔离、刷新、失败回滚与快捷导出。

- 配图 choices_right 为“选项右侧”：题干通栏，下方选项单列与独立配图分栏（题末或紧邻选项前）。正文、选项内部锚定图片不得挪走。录入/组卷共用渲染器与 CSS，经过富文本清洗后仍须验证分栏和公式；PDF 使用左右 minipage，Word 使用无边框两列表格并保留可编辑公式。无选项时下方显示，不丢图。验收脚本 choices_right_ui_smoke.py、choices_right_export_acceptance.py。

- “选项右侧”仅在题型为 single_choice/multi_choice 且识别到选项时尝试分栏，其他情况与 bottom_right 完全等效，保留原设置值。题型变化须即时重绘；录入、题库、组卷、Word/PDF 使用同一规则。跨空间只读选题复用题库默认排版和逐图配置，不读取当前题篮，不提供修改来源题目的排版按钮。

- 难度全平台固定为字符串 1–5（基础、简单、中等、较难、困难）；unrated 仅表示待确认，不属于第六级。易错/强基/竞赛/挑战为 tags，不能参与难度计算。组卷按已定级题数计算五级比例，待确认单列且不计入分母。旧难度先备份再保留为标签，不猜测等级。教材保存、worker 配置、ORM 写入、AI 输出、导入卡片和旧草稿共用此约束。

- 严禁从 Windows 宿主机用 SQLite（包括只读连接）打开正在由 Linux Docker 使用的 data/*.db 或 data/banks/*/*.db。跨内核 WAL/SHM 生命周期曾导致 SQLITE_CANTOPEN/I/O 错误。运行期核验通过 HTTP API；必要的 SQL、在线备份及完整性检查只在同一容器内执行并显式关闭连接。离线文件备份须先检查活动任务并正常关闭本项目服务，不删除 WAL/SHM。健康验收必须检查每个空间的 questions/categories/stats/papers，不能只看 /healthz 或元数据。

- MinerU 导入只做前置整理：`platform_app/imports.py` 调用 MinerU（或读取结果包），`mineru_markdown.py` 按 content_list 坐标重建 Markdown 并分批上传配图，最终仍走原生 Markdown 导入、AI 拆题、公式核对与审查入库。不得绕过审查直接入库，不改引擎拆题代码。MinerU Key 存为平台设置 `mineru_key`（团队优先），不下发给 worker。图片选项判定规则改动须保持 `tests_platform/test_mineru_import.py` 的行、网格与题干图用例通过，并用真实试卷对照原页复核。
- MinerU 快速拆题（`platform_app/mineru_fastpath.py`，前端 `bridge.js` 在 MinerU 来源的拆题请求上加 `platform_fast_path=mineru`，平台转到 worker 的 `/internal/fast-parse`）只替换 `ai_parse_paper` 中那一次整卷拆题模型调用：本地切段后，每段必须单独通过引擎 `inspect_source_structure` 才保留原文；模型只返回 is_question、题型、教材分类与难度，结果须完整覆盖全部 id、题型与难度合法，否则整卷退回原请求；教材分类名称交给引擎导入校验（`mathbank.import_classification`，移植自上游 3.0.0）逐题对齐或标记人工选择，不因分类名称退回。原文核对、原答案保护、配图映射等后处理一律沿用引擎代码，不得在平台另写。不伪造或复用引擎的 Word/PDF 来源证书；不确定的版式（跳号、重号、共用材料、卷末答案、跨题引用）宁可退回整卷拆题。改动须保持 `tests_platform/test_mineru_fastpath.py` 通过，并用真实 MinerU 结果对比新旧两条路径的题数、题文与分类。MinerU 结果缓存键为文件内容、全部解析选项与接口地址，缓存只存结果包，不存 Key。

- 试卷目录最多两级（`spaces.MAX_FOLDER_DEPTH`），同级不重名。旧安装中更深的目录仍须能列出、筛选和删除。按目录筛选包含其子目录（`spaces.folder_subtree`）；删除目录时子目录一并删除，其中试卷移到被删目录的上一级（一级目录的移到未归档），不删除试卷。界面为“试卷管理”左侧目录树（`paper-tools.js`），不再使用目录管理弹窗；拖动移动与目录加载以版本号防止旧加载覆盖新移动。
- 侧栏收起由 `space-picker.js` 写 `mb_nav` cookie，`app.inject` 据此在 `<html>` 上输出 `data-nav="collapsed"`，样式只在桌面宽度生效；切换后派发 resize，让组卷分页和选项栅格重新测量。
- 选择题选项排列以题目内容中的 exam-zh 原生 `\begin{choices}[columns=N]`（1–4）为唯一来源，不新增数据库字段。引擎 `editor.js` 的 `ChoiceLayoutTools` 读写该参数并在预览中固定列数（`data-authored-columns`），自动时只按实测宽度在 4/2/1 中选择；Word 由 `mathbank/choice_layout.py` 读取或按 exam-zh 规则估算；PDF 交给 exam-zh。“选项右侧”配图时三端都固定单列。改估算常数须用真实试卷导出 PDF 对照（见 VERIFICATION.md 2026-10-16）。
- 导入中心布局由 `platform_app/web/import-view.js` 负责：上传区、原卷视图和解析设置由原版控件移动组成，不复制、不改引擎脚本；状态取自 `window.currentPdfFile`／`currentDocxFile`、`#texFileName` 与粘贴模式，清空通过包装 `clearAllImportInputs` 感知。选好试卷后解析设置移到右栏，开始解析后隐藏；原版 `#importLatexContent` 必须始终留在 DOM 中。Word 原卷预览走 `previews.py`（LibreOffice 独立配置目录、单任务锁、120 秒超时、按哈希缓存），MinerU 原卷走 `/mineru/{job}/origin`，只对任务创建者可见。
- 讲义（`handouts.py`、`handout_export.py`、`handout_word.py`、`templates/handout/mathbank-handout.sty`、`web/handouts.js`，设计见 `docs/HANDOUTS.md`）只有一套版式：颜色、字体、间距和编号只在 sty 与 `handout_word.py` 的常量中定义，两者保持一致，不加逐块样式字段。块种类固定；新增或修改块须同时改校验（`clean_block`）、网页、PDF、Word 四处并补测试。编号不存储，三端按顺序计算。
- 题目块是快照（`SNAPSHOT_FIELDS` + `source_id`／`source_hash`），读取和导出时不得用题库当前内容替换；同步只在老师确认后由前端写回。`/changes` 须把题库题目经 `snapshot()` 规范化后再比较哈希，否则默认值会被误报为修改。
- 讲义整份保存并带 `revision`，过期返回 409；前端停止自动保存，让老师选择另存为新讲义或载入最新版本，不得静默覆盖。切换工作区、返回列表、切换空间前先等保存完成。
- 讲义导出在空间 worker 的 `/internal/handout` 内复用引擎函数（题目 LaTeX、配图收集、编译、Word 构建器），不得另写一套题目排版。Word 中题干右侧配图用无边框两栏表格，不用浮动图片。改版式须导出 PDF 与 Word 的学生版、教师版核对，docx 须用 Microsoft Word 打开（LibreOffice 对 OOXML 校验和浮动对象都比 Word 宽松，不能代替）。
- 试卷 Word 的“题干右侧”配图由引擎 `_add_right_figure_row` 输出无边框两栏行（题干左、图右，选项通栏在下），讲义只覆盖其配图尺寸；不得改回 `wp:anchor` 浮动图（Microsoft Word 会把选项推到图下、下一题的图压住上一题选项，且曾因 `wrapText="both"` 拒绝打开整份文件）。题干末尾空括号连同其后的句号由引擎 `clean_choice_stem_parentheses`／`cleanChoiceStemParentheses` 统一去掉，平台不再另写规则。回归见 `tests_platform/test_paper_export_layout.py`，规则详见 `engine/AGENTS.md`。
- 讲义界面改动须执行 `tests_platform/handout_ui_smoke.py`（`run_tenant_ui_tests.py` 默认包含），讲义接口与导出由 `tests_platform/test_handouts.py` 覆盖。
- 绘图（`drawings.py`、`ggb_ai.py`、`web/drawings.js`，表 `drawings`）用自托管 GeoGebra Classic：
  - 程序包在 `platform_app/vendor/geogebra/<版本>/`，不进 Git，由 `scripts/fetch_geogebra.py` 固定版本与 SHA-256 获取。版本路径按一年缓存，升级须换新版本目录，并同步 `drawings.GEOGEBRA_VERSION`、`drawings.js` 与获取脚本。
  - 存入的 .ggb 一律经 `clean_ggb` 去掉 JavaScript（全局脚本重置、对象 `<javascript>` 删除），PNG 经 `clean_png` 校验并裁边。
  - AI 只返回 GeoGebra 指令（`ggb_ai.py` 过滤脚本类指令），由浏览器里的 GeoGebra 执行，服务器不执行。
  - 题目引用一律经引擎原有上传接口复制图片，不直接引用绘图。
  - 改动须保持 `tests_platform/test_drawings.py` 与 `drawing_ui_smoke.py` 通过，后者断网验证不访问任何外部地址。
  - GeoGebra 免费许可仅限非商业用途。
