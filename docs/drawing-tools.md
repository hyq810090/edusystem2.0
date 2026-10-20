# 绘图插件评估（2026-10-16）

只做评估，不开发。目标：数学几何图形与物理示意图能在题目里画、能再次修改、能高质量进入 PDF／Word。

## 现状

- 已有 TikZ 绘图工作台：AI 按文字或参考图生成 TikZ，容器内 XeLaTeX 编译出预览图，PDF 导出保留矢量源码（题干、解答各自多幅，见 `engine/AGENTS.md` 3.2）。
- 测试镜像（Debian TeX Live 2022）已带 `pgfplots`、`tkz-euclide`、`tikz-3dplot`、`circuitikz`；没有 `tikzphysics`。
- 预览模板（`engine/main.py` 渲染 TikZ 处）没有加载 `circuitikz` 和 `tkz-euclide`；试卷导出模板加载了 `tkz-euclide`，没有 `circuitikz`。
- 题图只接受光栅图（PNG/JPEG 等），不接受 SVG。

## 候选

| 工具 | 适合 | 嵌入方式 | 可再编辑 | 授权 | 结论 |
|---|---|---|---|---|---|
| **GeoGebra**（几何/函数） | 尺规几何、动点、函数图像 | 自托管 Math Apps Bundle，JS Apps API；`getPNGBase64` 导出，`getBase64`/`setBase64` 存取 .ggb 状态 | 存 .ggb 状态即可重开 | 非商业免费；商业用途须签协议，教师课堂教学属非商业，机构题库是否算非商业需向 office@geogebra.org 书面确认；须标注 “Made with GeoGebra®” | **数学首选**（交互最好、教师熟悉），先确认授权 |
| **JSXGraph**（几何/函数） | 程序化生成的几何与函数图、未来学生端动态练习 | npm/CDN，纯前端 | 存 JS/JSON 构造 | MIT 或 LGPL 任选 | GeoGebra 授权不可行时的替代；没有成品编辑器，需要自己做编辑界面 |
| **TikZ + circuitikz + tikzphysics**（物理） | 电路（circuitikz）；斜面、滑块、弹簧、滑轮、透镜、面镜、棱镜（tikzphysics） | 复用现有 AI 绘图工作台 | 存 TikZ 源码（已支持） | LPPL | **物理首选**：改动最小，PDF 为矢量，AI 可直接生成 |
| **draw.io**（物理手绘补充） | 电路、光路、实验装置、流程示意 | 自托管 `jgraph/drawio`，`embed=1` + postMessage；导出 `xmlpng` | `xmlpng` 把源 XML 存在 PNG 里，载回即可编辑 | Apache-2.0（另有不得用于 Atlassian 集成的条款，与本项目无关） | 教师手绘的补充入口；官方文档写明嵌入模式只在 embed.diagrams.net 上受支持，自托管嵌入需自测 |
| Desmos | 函数图像 | API key | 有限 | 生产使用需申请 key／合作协议 | 不推荐（偏函数，协议不透明） |
| tldraw | 白板 | React SDK | 有 | 生产需 license key，免费版带水印，商用需年费 | 不推荐 |
| Excalidraw | 白板 | React 组件 | 有 | MIT | 不推荐（手绘风格不适合试卷） |
| 几何画板、亿图等 | 桌面绘图 | 不能嵌入网页 | — | 商业软件 | 仅作教师本机工具，导出图片后上传 |

## 建议路线

1. **物理先走 TikZ**（约 1–2 天）
   - 预览与试卷导出模板加载 `circuitikz`；
   - 镜像安装固定版本的 `tikzphysics`（CTAN，2026-09 的 1.2.0；新包接口仍在调整，例如弹簧在不同版本里分别是节点和路径样式）；
   - 绘图工作台的提示词按学科给出可用宏包与示例。
   - 产出仍是现有 TikZ 资产，PDF 矢量、Word 用预览图，不需要新存储。
2. **数学几何接 GeoGebra**（约 3–5 天，先确认授权）
   - 编辑器“新增绘图”里增加“GeoGebra 作图”；
   - 自托管 bundle（不依赖外网 CDN）；
   - 导出 2–3 倍分辨率 PNG 作为题图，同时保存 .ggb 状态，以便再次打开修改。
3. **物理手绘补充 draw.io**（约 3–5 天）
   - 自托管容器，嵌入对话框；
   - 导出 `xmlpng`（图片自带可编辑源），作为普通题图上传，再次编辑时直接载回。

## 统一接入方式（以后开发时）

- 编辑器里同一个“新增绘图”入口，选择 TikZ（AI）、GeoGebra 或 draw.io。
- 题目里只存一张光栅题图，沿用现有的位置、尺寸、选项内排版和导出逻辑。
- 可再编辑的源（TikZ 源码、.ggb 状态、draw.io XML）按图片路径挂在题目上：TikZ 用现有资产字段；GeoGebra 状态由平台按 `空间 + 图片路径` 保存，不改引擎数据库结构；draw.io 的源已经在 `xmlpng` 图片里。
- 跨空间引用题目时，源随图片一起复制。

## 来源

- GeoGebra 授权：https://www.geogebra.org/license
- GeoGebra 嵌入与自托管：https://wiki.geogebra.org/en/Reference:GeoGebra_Apps_Embedding
- JSXGraph 授权：https://cdn.jsdelivr.net/npm/jsxgraph@1.14.0/README.md
- tikzphysics：https://ctan.org/pkg/tikzphysics
- draw.io 嵌入模式：https://www.drawio.com/docs/reference/embed-mode/
- tldraw 授权：https://tldraw.dev/sdk-features/license-key
- Desmos API：https://www.desmos.com/api
