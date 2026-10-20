# MinerU 试用工具

用你自己的 MinerU API Key 解析试卷，把识别结果和原文并排对照，看公式、配图、表格是否可用，再决定要不要把 MinerU 接入 MathBank。这个工具独立运行，不改动平台和题库。

## 启动

在 `mathbank-platform` 目录运行：

```powershell
python tools/mineru_trial/app.py
```

然后打开 http://127.0.0.1:3031 。程序只监听本机。

## API Key

在 mineru.net 的 API 管理页生成 Key，用下面任一方式提供：

- 直接粘贴在页面的 “API Key” 输入框。
- 启动前设置环境变量：`$env:MINERU_API_KEY="你的 Key"`，页面上的输入框可以留空。

Key 只保存在这个程序的内存里，不会写入文件或解析记录。

## 选项

- **模型**：`vlm` 是官方推荐的大模型解析；`pipeline` 是传统的分步流水线，可以拿来对比。
- **强制 OCR**：扫描件、拍照件请勾选；电子版 PDF 不用勾。
- **页码范围**：例如 `1-3,5`，留空表示全部页。试用时先挑几页，省额度。

## 结果

每次解析保存在 `test-results/mineru-trial/<时间>-<文件名>-<模型>/` 目录下：

- 原文件
- `pages/`：原文页面预览
- `result/`：MinerU 返回的全部内容，包括 `full.md`、`images/` 和 `*_content_list.json`
- `job.json`：选项、耗时和统计

页面会显示页数、耗时、公式数、图片数、表格数，以及 KaTeX 渲染失败的公式数（这些公式会标红）。

## 在 MathBank 里试导入

MathBank 的导入中心本来就能导入 Markdown 加配图，接入前可以先手动走一遍：

1. 在测试实例（例如 http://127.0.0.1:3011）打开 “导入中心”。
2. 上传结果目录里的 `full.md`。
3. 按提示从 `images/` 文件夹选择配图，每次最多 20 张。
4. 检查拆题、选项、答案、公式和配图是否正确。

## 注意

- 文件会上传到 mineru.net 解析。
- 官方文档写单文件不超过 200 MB；页数上限各页面说法不一（200 页或 600 页）。每天前 2000 页优先处理，测试期规则可能会调整。
- 测试使用模拟的 MinerU 接口，不需要 Key，也不联网：`python -m pytest -q tools/mineru_trial`。
- API 客户端在 `platform_app/mineru_client.py`，与平台的 MinerU 导入共用。
