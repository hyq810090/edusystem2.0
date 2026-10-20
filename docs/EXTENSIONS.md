# 扩展规划：MinerU、RAG 与学生端

本文记录后续接入 MinerU、RAG 和学生端时依赖的结构，以及建议的接入位置。除“已就位”一节外，其余内容都还没有实现。

## 已就位的基础

- **空间就是题库。** 一个空间固定一个学科、一个学段和一套教材，对应一个原生 MathBank 数据库，位于 `data/banks/<空间 id>/`。题目、图片、任务、试卷目录、引用来源都按空间 id 寻址，即 `/bank/<id>/api/*`，以及 `tasks`、`folders`、`paper_folders`、`origins` 这几张表的 `space_id`。外部服务以空间为授权和索引的最小单位，学科、学段、教材可直接用作过滤条件（`store.SPACE_SELECT`）。
- **写入都经过平台。** 浏览器只能通过 `platform_app/bank.py` 访问 worker。每次成功的写操作都会在 `audit` 表留下一条记录：`action = 'bank:<METHOD> <path>'`，`target = 空间 id`。这张表既是审计日志，也是变更流：外部索引器按 `audit.id` 游标增量读取，就能知道哪些空间有变化。
- **角色白名单。** `store.TEACHER_ROLES` 列出能使用题库的角色。以后新增 `student` 等角色时，不会自动获得任何空间权限。
- **worker 按需启动，空闲回收。** 达到 `MATHBANK_MAX_WORKERS` 上限时，平台会停掉最久未用、且没有请求和导入任务的 worker。空间变多不会让常驻进程无限增加。

## MinerU：PDF 与扫描件解析

> 已实现第一步（2026-10-16）：导入中心的“MinerU 解析”把 MinerU 结果整理成 Markdown 后交给原生导入，见 README 的“MinerU 导入”。下文是进一步把 MinerU 接入引擎 PDF 流程的设想。

现在的 PDF 导入走引擎自带的版面分析和视觉模型（`upload/pdf-task`），Word、TeX、Markdown 在本地解析。接入前，可以先用 `tools/mineru_trial/` 里的试用工具，拿真实试卷评估 MinerU 的效果。建议这样接入 MinerU：

1. 新增平台级配置（仅超级管理员可改），保存在 `settings` 表，scope 为 `integrations`。字段包括 `mineru_url`、`mineru_token`，以及 `pdf_parser`（取值 `builtin` 或 `mineru`）。这些配置不下发给 worker。
2. 在 `bank.py` 中拦截 `upload/pdf-task`。当 `pdf_parser = mineru` 时，平台把 PDF 交给 MinerU，拿回 Markdown 和图片，再以 Markdown 导入任务的形式交给引擎；任务照常登记到 `tasks`。之后的核对、原文证据、查重、入库流程不变。
3. MinerU 的原始产物（md 文件、版面 json）存入 `data/banks/<id>/sources/<任务 id>/`，供 RAG 复用。
4. MinerU 调用失败时直接报错，不自动退回内置解析。这与模型配置的原则一致。

## RAG：检索增强

- **索引内容。** 题目的题干、答案、解析、知识点、标签；以后还包括资料文档，例如 MinerU 解析后按标题切块的教材和讲义 Markdown。
- **命名空间与权限。** 以 `space_id` 作为命名空间，元数据包括 subject、stage、textbook、question_id、question_type、difficulty。查询时用 `store.accessible_spaces(user)` 过滤，与跨空间选题使用同一套权限规则。
- **同步方式。** 由独立的 indexer 进程负责，不放进 worker。它从 `audit` 读取 `bank:%` 记录来确定变化的空间，再分页拉取该空间的 `/api/questions`，按题目内容哈希比对后只更新有变化的题；删除可通过 `bank:DELETE questions/<id>` 直接识别。引擎的题目表没有更新时间字段，这个方案不需要改动引擎数据库结构。
- **调用入口。** 平台新增只读接口 `/platform/api/spaces/<id>/retrieve`，供 AI 选题、相似题推荐和学生答疑使用。嵌入模型与现有 AI 配置一样，按“团队配置 → 平台默认”解析。

## 学生端

- **账号。** 学生账号的 `users.role` 为 `student`。这个角色不在 `TEACHER_ROLES` 中，因此进不了工作台，也访问不了 `/bank/*`。
- **班级。** 新增两张表：`classes(id, team_id, name, subject, stage)` 和 `class_members(class_id, user_id, role)`。班级的学科、学段与空间对应，教师从对应空间发布内容。
- **作业与测验。** 发布时冻结试卷快照：题目内容和题图复制到 `data/assignments/<id>/`，做法与跨空间引用相同。学生只能读取快照，不能读取题库；答案和解析何时可见由发布设置控制。
- **提交与批改。** 新增 `submissions` 表。客观题自动判分，主观题由教师批改；之后可以接入 RAG 做讲解。
- **入口。** 学生使用独立的页面，以手机为先，不复用教师工作台，避免把教师功能暴露给学生。
