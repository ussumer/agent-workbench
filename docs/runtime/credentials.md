# 外部凭据获取手册

本文件只写**怎么取、放到哪**，不记录任何密钥值。`.env` 已被 git 忽略，密钥不得进入任何
文档、receipt、索引或聊天记录。

当前缺口（T15 的 live 验收需要）：

| 变量 | 服务 | 状态 |
|---|---|---|
| `ZHIPU_API_KEY` | 智谱网络搜索 | **key 有效；账户余额不足（code 1113）** |
| `MODELSCOPE_MCP_URL` | ModelScope 图表 MCP 端点 | **已就绪并实测连通** |
| `MODELSCOPE_API_TOKEN` | 同上，鉴权 | **已就绪并实测连通** |

### 用 `scripts/check_external.py` 自检，不要只看 `capability_report()`

```bash
python scripts/check_external.py
```

**`capability_report()` 只判断环境变量是否存在，不判断是否有效。** 一个被撤销的 key、抄错的
Key ID、或余额为零的账户，都会让它显示 `satisfied=True`，然后 gate 放行、第一次真实调用才失败。
`check_external.py` 补上那一步：对两个服务各做一次真实调用，并把失败翻译成"该做什么"。

已实测过的失败形态（都是真实遇到的，不是假设）：

| 现象 | 实际含义 | 修法 |
|---|---|---|
| 所有端点 401 | key 本身无效 | 智谱 key 形如 `{id}.{secret}`，**含一个点**，长度约 49 字符。32 字符无点的多半是 Key ID，不是 key |
| `/chat/completions` 200 但 `/web_search` 429（code 1113） | **账户余额不足或无搜索资源包** | 充值，或购买网络搜索资源包。**搜索是独立计费的**，对话模型的免费额度不覆盖它 |
| 搜索 403 / 权限类错误 | 未开通网络搜索 | 在控制台开通该产品 |

区分顺序很重要：先探 `/chat/completions`（任何有效 key 都能调），再探 `/web_search`。
只有这样才能分开"key 坏了"和"这个产品没额度"——一个裸的失败调用分不出来。

### 图表端点已实测（2026-09-17）

按用户提供的配置写入 `.env` 后实际连过，结论如下 —— T15 可以直接照此实现，不必再猜：

| 项 | 实测值 |
|---|---|
| 传输形态 | **SSE**（`mcp.client.sse.sse_client`），URL 以 `/sse` 结尾 |
| 鉴权 | **`Authorization: Bearer <token>` 请求头**（不是查询串参数） |
| 握手 | 成功，`initialize` + `list_tools` 均正常 |
| 工具数量 | **18 个**（方案预期 26，差异见下） |
| 覆盖要求 | 三个必需类型都在：`generate_bar_chart` / `generate_line_chart` / `generate_pie_chart` |

**18 ≠ 26 是一个需要记录的差异。** T15 步骤 4 要求"保存目录与讲义 26 项概念的对应差异，
**不编造缺失类型**"，所以这 8 个缺口必须如实写进 `chart_params.md`，不能用别名凑数。

实际工具名（T15 的 type 映射要按这些名字建，不叫 `bar`/`line`/`pie`）：

```
generate_echarts  generate_area_chart   generate_line_chart   generate_bar_chart
generate_pie_chart generate_radar_chart generate_scatter_chart generate_sankey_chart
generate_funnel_chart generate_gauge_chart generate_treemap_chart generate_sunburst_chart
generate_heatmap_chart generate_candlestick_chart generate_boxplot_chart
generate_graph_chart generate_parallel_chart generate_tree_chart
```

公共参数：`data`（多数工具的必需项）、`title`、`width`、`height`、`theme`、`outputType`；
直角坐标系类工具另有 `axisXTitle` / `axisYTitle`。`generate_parallel_chart` 额外需要
`dimensions`，`generate_echarts` 需要 `echartsOption`。

### 真实调用已实测（三种图都出了真 PNG）

| 工具 | 可用的 `data` 形状 | 实测结果 |
|---|---|---|
| `generate_bar_chart` | `[{"category": "刹车片", "value": 8}, ...]` | `isError=False`，PNG 43990 字节，magic `\x89PNG` |
| `generate_pie_chart` | `[{"category": "刹车片", "value": 8}, ...]` | `isError=False`，PNG 96129 字节 |
| `generate_line_chart` | **`[{"time": "2026-09-01", "value": 8}, ...]`** | `isError=False`，PNG 45323 字节 |

**返回的是 base64 图片，不是 URL。** `content[0].type == "image"`，`mimeType=image/png`，
`data` 是 base64 串。所以"资产非空可读取"的校验方式是 base64 解码后检查 PNG magic，
不是去 GET 一个链接——按 URL 找会得出"没有可下载地址"的错误结论。

### 一个会让朴素包装器踩坑的 schema 不一致

`list_tools()` 对 `generate_line_chart` 声明的 `required` 只有 `["data"]`，**但这不够**：

- 传 `[{"category": ..., "value": ...}]`（和柱状图一样）→ `isError=True`，
  报 `path: ["data", 0, "time"], "message": "Required"`。
- 传数组形式 `[["刹车片", 8], ...]` → 报 `expected object, received array`。

**折线图每一行要的是 `time` 而不是 `category`，而这个要求没有出现在声明的 schema 里。**
一个"把同一份 data 转发给所有图表类型"的包装器，柱状和饼图会通过、折线图会在运行时失败。
T15 的 type 映射必须按图表族分别构造 `data`，不能只做工具名转发。

> 其余变量（`MODEL_*`、`MONGODB_*`、`FIXTURES_*`、`OPENSANDBOX_*` 等）已可用；
> `ERP_SERVICE_TOKEN` / `MCP_GRANT_SECRET` 由测试载体自己注入，本地跑不需要手填。

---

## 1. 智谱搜索（`ZHIPU_API_KEY`）

### 取值步骤

1. 打开 <https://bigmodel.cn/apikey/platform>（或开放平台用户中心
   <https://open.bigmodel.cn/usercenter/apikeys>）。
2. 注册 / 登录后创建 API Key，形如 `xxxxxxxx.yyyyyyyy`（含一个点）。
3. **确认账号已开通「网络搜索」**。搜索是独立计费的接口，未开通时调用会返回权限类错误——
   这正是 T15 要区分出来的「无引擎权限」，不能当成空结果成功。

### 填法

```dotenv
ZHIPU_API_KEY=<你的 key>
```

### 接入形态（两种，T15 按方案走 REST）

| 形态 | 地址 | 说明 |
|---|---|---|
| **REST（方案采用）** | `https://open.bigmodel.cn/api/paas/v4/web_search` | 接口文档：<https://docs.bigmodel.cn/api-reference/工具-api/网络搜索> |
| MCP（备用，若 REST 不可用） | `https://open.bigmodel.cn/api/mcp-broker/proxy/web-search/mcp?Authorization=<key>` | 官方 MCP broker，凭据直接放在查询串里 |

### 关键参数（实测文档确认）

| 参数 | 值 | 备注 |
|---|---|---|
| `search_engine` | `search_std` / `search_pro` / `search_pro_sogou` / `search_pro_quark` | 方案指定用 **`search_pro_sogou`**（搜狗，覆盖腾讯生态与知乎） |
| `search_query` | 查询串 | |
| `count` | 1..50，默认 10 | |
| `search_recency_filter` | 如 `noLimit` | |
| `content_size` | `medium`（默认）/ `high` | |

响应里 `search_result[]` 每项含 `title` / `link` / `content` / `media` / `icon` /
`publish_date` / `refer`——T15 要「搜索结果可引用」，引用字段就是这里的 `link` 与
`publish_date`。

**计费参考**：`search_std` 0.01 元/次、`search_pro` 0.03、`search_pro_sogou` 0.05、
`search_pro_quark` 0.05。T15 会限制 `count` 与重试次数，这是原因之一。

---

## 2. ModelScope 图表 MCP（`MODELSCOPE_MCP_URL` + `MODELSCOPE_API_TOKEN`）

远端是开源项目 **AntV `mcp-server-chart`**（<https://github.com/antvis/mcp-server-chart>）
在 ModelScope 上的托管实例。方案要求「从 ModelScope **真实端点**发现工具并保存目录快照」，
所以端点必须来自 ModelScope，不能换成自托管实例（替换外部服务需要单独讨论同意）。

### 取 token 步骤

1. 打开 <https://modelscope.cn/my/myaccesstoken>（个人中心 → 访问令牌）。
2. 新建 / 复制访问令牌，形如 `ms-` 开头的一串字符。

```dotenv
MODELSCOPE_API_TOKEN=<你的 token>
```

### 取 MCP 端点步骤

1. 打开魔搭 MCP 广场 <https://modelscope.cn/mcp>，搜索图表类服务（AntV / chart）。
2. 进入服务详情页，用页面的「**复制 SSE 地址**」或「在客户端中使用」按钮拿完整地址。
3. 地址形式通常是：

```dotenv
MODELSCOPE_MCP_URL=https://mcp.api-inference.modelscope.net/<服务ID>/sse
```

> **注意**：部分是 `/sse`（SSE 传输），新版可能是 `/mcp`（streamable-http）。两者客户端
> 写法不同，**请以服务详情页给出的为准**——抄错传输形态会表现为连得上但收不到消息。
> 把详情页给的原文粘过来即可，我按实际值适配。

### 两个坑（先说在前面）

1. **文档页是 SPA，抓不到正文。** `modelscope.cn/docs/mcp/sdk` 用浏览器打开能看全，
   纯 HTTP 抓取只能拿到外壳。需要查 SDK 用法时请用浏览器。
2. **端点与 token 是不是配套的要确认。** token 是否要附加在 URL 查询串上、还是走
   `Authorization` 头，取决于该服务的配置示例。详情页的示例片段能回答这个问题。

---

## 3. 填完后的自检

```powershell
# 能力判定应当全部 satisfied（此前 search / chart 为 false）
.venv\Scripts\python.exe -c "import sys,json; sys.path.insert(0,'src'); from agent.config import capability_report; print(json.dumps(capability_report(), ensure_ascii=False, indent=1))"

# 或者直接让 gate 判
python scripts/gate.py T15
```

期望结果：`search.satisfied=true`、`chart.satisfied=true`，gate 从 `BLOCKED` 变为可执行。

**如果某项仍为 false**，说明键名或取值没被读到——`agent/env_utils.py` 的 `load_env()`
优先读真实环境变量、其次读 `.env`，且按名称识别密钥。把 `capability_report()` 的输出贴给我即可。

---

## 4. 我拿到凭据后会做什么（T15 步骤）

1. 连真实搜索，规范结果与错误：**鉴权失败必须抛出，不能转成空成功**；限制 query 长度、
   `count` 与重试次数。
2. 发现远端图表 MCP 目录与 schema，建立**完整的图表 type 映射与参数索引**，对外只暴露一个
   `chart_generator`（不把二十多个远端工具塞进主 prompt）。
3. 真调用柱状、折线、饼图，下载并校验返回资产非空可读。
4. 保存目录快照，并在 `src/skills/procurement/chart_params.md` 记录**讲义 26 项概念与实际
   目录的差异**——缺的类型如实标注，**不编造**。
