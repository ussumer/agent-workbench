# 图表目录与参数索引

本文件是**图表能力的索引**，不是图表服务本身。内容由 T15 从真实 ModelScope 端点发现后写入，
不是凭讲义或印象编写的。

配套证据在 `artifacts/tasks/T15/`（稳定路径，不随 gate 的 attempt 目录变化）：

| 文件 | 内容 |
|---|---|
| `chart-catalog.json` | 机器可读的远端目录快照（18 个工具的名称、required、属性） |
| `chart-bar.png` / `chart-line.png` / `chart-pie.png` | 三种图的真实渲染产物，可直接打开核对 |

这三个 PNG 是 T15 验收时对真实端点调用后落盘的，不是示例图。

> 任何未出现在下表里的图表类型都应报「不支持」，**不得凭空编造工具名或参数**。
> 远端不可用时报错，不改用本地绘图。

## 来源

| 项 | 值 |
|---|---|
| 服务 | 由 `MODELSCOPE_MCP_URL` 配置（未配置时图表能力不可用） |
| 传输 | **SSE**（`mcp.client.sse.sse_client`），地址以 `/sse` 结尾 |
| 鉴权 | `Authorization: Bearer <MODELSCOPE_API_TOKEN>` 请求头 |
| 发现方式 | MCP `initialize` + `list_tools` |
| 实测工具数 | **18** |

## 参数约定

对外只暴露**一个** Agent 层工具 `chart_generator`，签名：

```
chart_generator(chart_type, data=[{label, value}], title?, output_type?)
```

调用方只给中性的 `{label, value}`；由工具按图表族转换成远端要的字段名。**不要**让模型直接
面对 18 个远端工具，也不要让它自己判断该用 `category` 还是 `time`——那是本文件存在的理由。

通用参数：`title`、`width`、`height`、`theme`、`outputType`（`png` / `svg`）；
直角坐标系类另有 `axisXTitle` / `axisYTitle`。

### 数据形状按图表族分两类（这是最容易踩的坑）

| 族 | 成员 | 每行字段 |
|---|---|---|
| **category 族** | `bar`、`pie`、`funnel`、`treemap`、`sunburst`、`radar` | `{"category": "...", "value": 8}` |
| **time 族** | `line`、`area`、`scatter`、`heatmap`、`boxplot` | `{"time": "...", "value": 8}` |

**远端声明的 schema 没有表达这个区别。** `generate_line_chart` 的 `required` 只有 `["data"]`，
但每行必须带 `time`；把柱状图那套 `{category, value}` 传给它，返回的是
`Input validation error: path ["data", 0, "time"] Required`。所以"把同一份数据转发给所有图表类型"
的包装器会**通过柱状和饼图、在折线图上运行时失败**。

### 需要专用参数、本包装器不代构造的类型

`echarts`（要 `echartsOption`）、`parallel`（要 `dimensions`）、`graph`、`tree`、`sankey`、
`gauge`、`candlestick` —— 这些传 `{label, value}` 是错的，会报 `CHART_TYPE_UNSUPPORTED` 而不是
猜一个形状。要用它们需要扩展本文件的映射。

## 实际目录（18 个，实测于 2026-09-17）

| 工具 | 必需 | 主要可选参数 |
|---|---|---|
| `generate_bar_chart` | `data` | axisXTitle、axisYTitle、group、stack、title、width、height、theme、outputType |
| `generate_line_chart` | `data` | axisXTitle、axisYTitle、smooth、showArea、showSymbol、stack、title、… |
| `generate_pie_chart` | `data` | innerRadius、title、width、height、theme、outputType |
| `generate_area_chart` | `data` | axisXTitle、axisYTitle、smooth、showArea、showSymbol、stack、… |
| `generate_scatter_chart` | `data` | axisXTitle、axisYTitle、title、… |
| `generate_radar_chart` | `data` | title、width、height、theme、outputType |
| `generate_funnel_chart` | `data` | title、width、height、theme、outputType |
| `generate_treemap_chart` | `data` | title、width、height、theme、outputType |
| `generate_sunburst_chart` | `data` | title、width、height、theme、outputType |
| `generate_heatmap_chart` | `data` | axisXTitle、axisYTitle、title、… |
| `generate_boxplot_chart` | `data` | axisXTitle、axisYTitle、title、… |
| `generate_gauge_chart` | `data` | min、max、title、… |
| `generate_candlestick_chart` | `data` | showVolume、title、… |
| `generate_sankey_chart` | `data` | nodeAlign、title、… |
| `generate_graph_chart` | `data` | layout、title、… |
| `generate_tree_chart` | `data` | layout、orient、title、… |
| `generate_parallel_chart` | `data`、`dimensions` | title、… |
| `generate_echarts` | `echartsOption` | width、height、theme、outputType |

## 与讲义「26 项概念」的差异（如实记录，不补全）

任务要求"保存目录与讲义 26 项概念的对应差异，**不编造缺失类型**"。必须说清楚：

- **讲义那份 26 项的清单不在本仓库内。** 全仓库检索只有任务文本提到这个数字，没有那份名单
  （见 `docs/plan/tasks/T15.md` 与 `contracts/skills-memory.md` 的引用）。我没有讲义原文。
- 因此**只能单向记录**：下面是本次从真实端点发现的 **18** 项，是端点实际暴露的全部。
  **我不能判断**这 18 项与讲义的 26 项具体差在哪 8 项 —— 那需要讲义原文才能对齐。
- 我**没有**为了让数字凑到 26 而编造工具名、也没有用别名把一项拆成多项。

| 侧 | 数量 | 依据 |
|---|---|---|
| 真实远端目录 | **18** | 本次 `list_tools` 实测 |
| 讲义声称 | 26 | 仅见于任务文本；名单不在仓库内，无法逐项对齐 |

**待用户提供讲义图表清单后**，才能补齐这份差异表。在那之前，任何"缺了哪几项"的说法都是猜测。

## 失败条件

- 未配置 `MODELSCOPE_MCP_URL` / `MODELSCOPE_API_TOKEN` —— 报图表能力不可用，**不改用其它图表服务**。
- 目录快照为空 —— 报「尚未发现图表工具」，请先完成发现步骤。
- 工具返回 `isError` —— 原样转述远端错误，不用文字描述冒充图表已生成。
- 返回内容不是可识别的图片（base64 解码后无 PNG/SVG magic）—— 报 `CHART_BAD_ASSET`，
  **不返回空资产**。
- 图表类型不在上表 —— 报 `CHART_TYPE_UNSUPPORTED` 并列出支持的类型。
