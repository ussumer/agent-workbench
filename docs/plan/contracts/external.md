# 外部服务与异步分析

## 演示报价和技能资源站

独立 `fixtures` 服务，使用真实 HTTP，在 `:8088` 提供 HTML/文件。资料标记“演示数据”，不使用课程私人 IP 或声称是真实商户。

| URL | 内容 |
|---|---|
| /suppliers/S001/quotes | P001=25.50、P002=12.00、P003=68.00、P004=18.00 |
| /suppliers/S002/quotes | P001=24.00、P002=13.00、P004=17.50 |
| /skills/catalog.json | slug、version、url、sha256、size、description |
| /skills/reorder-cost-summary-v1.zip | 有 SKILL.md、scripts 和示例的真实 ZIP |
| /health | 服务健康 |

HTML 报价包含 part_id、sku、currency、unit_price、quoted_at 和供应商名称。S001 使用表格，S002 使用列表，测试不能只匹配一张固定 DOM。缺物料与解析失败要明确区分。测试模式可提供慢响应、500、无价格页面，不在正常站点随机故障。

报价计算规则：只比较同物料同币种，取最低单价；相同价格以 supplier_id 升序确定推荐。P001/P003/P004 按建议数量最低价合计 `2553.00`。报告保留每个来源 URL 和时间，不能把搜索摘要当已抓取报价。

## 智谱搜索

Agent 工具 `web_search(query,count=5)`，调用智谱 web_search API，引擎 **search_std**，返回 title/url/snippet/published_at/provider。query<=70 字符，count<=10。超时、鉴权失败和空结果分开处理。单次读搜索最多 2 次重试；鉴权失败不重试。

引擎原为 `search_pro_sogou`。改成标准档是因为单次成本高出数倍，而演示轮次每一轮都会检索——那一档在这里买的是成本不是质量。**两处文档与代码在同一次改动里一起更新**：`dependencies.md` 禁止暗换引擎，而一个与文档不符的引擎正是"暗换"。无 `search_std` 权限时按阻塞处理，不退回旧引擎。

真实搜索用于采购背景资料，不用于验证本地演示站是否被公网收录。验收断言返回结构和可用引用，不固定搜索排名或具体文本。请求 ID、脱敏状态和结果保存，密钥不入模型上下文。

## ModelScope 图表 MCP

配置真实远端 URL、transport 和 token；不能猜用户端点。启动发现 tools/list，保存名称和 inputSchema。Agent 只看到 `chart_generator(chart_type,title,data,options)`，内部按经过验证的映射派发远端工具。

课程的“26→1”表示多图表统一入口。为实际目录生成完整参数索引；至少实测柱状、折线、饼图。上游数量变化时记录差异；缺课程需要的图表类型先讨论，不造假工具，不以本地图表永久替换已确认服务。

图表测试同时断言调用到远端、返回资产可下载、MIME/文件大小有效、浏览器实际显示。不能只断言返回了 URL。远端资产转存到当前用户 artifact 可改善演示稳定性，但必须保留来源和内容 hash。

## 报告产物

Markdown/CSV 在沙箱生成，包含数据来源、查询时间、预警、补货数量、供应商选择、逐行金额、总额和限制。下载工具只接收 sandbox 相对产物路径，服务端验证路径和 owner 后转存 download/{owner}/{artifact_id}/；文件 hash 与沙箱一致。

失败不能发一个不存在的下载链接。浏览器图表是主要可视资产，采购数据表必须也可阅读，不依赖图片识别数值。

## 异步子 Agent

来源是用户笔记 AsyncSubAgent 部分，PDF 的普通并行 task 不能代替。保留独立 Agent Protocol 服务和正式 SDK；运行端口 8123，`langgraph.json` 指定只读采购分析 graph。锁定版本后的启动命令写入运行手册。

同步 procurement-order 保持原调用方式。异步 analyst 只读业务数据，可生成报告，无订单写工具。通过框架提供的 launch/check/update/cancel 能力运行，不复制另一套自创 Agent 协议。

本地映射记录 owner、parent_thread_id、async_thread_id、async_run_id、状态、artifact_ids。调用从可信上下文传身份，不让模型指定他人 thread。异步服务使用与主服务相同的业务契约和技能读权限，沙箱执行经同一用户租约。

Vue与FastAPI的业务端点固定为：`POST /api/async-tasks` 接收 `{parent_thread_id,request_id,instruction}`，返回 `{task_id,status}`；`GET /api/async-tasks/{task_id}` 返回状态、摘要、artifact_ids和错误；`POST /api/async-tasks/{task_id}/update` 接收 `{request_id,instruction}`；`POST /api/async-tasks/{task_id}/cancel` 接收 `{request_id}`。这层仅做归属、幂等与官方SDK适配，不自创替代Agent Protocol。模型可见工具由框架生成，前端task_id映射到服务端保存的正式thread/run ID。

状态展示 queued/running/completed/failed/cancelled；用户离开父对话后后台可继续。查询和取消必须校验归属。更新说明若 SDK 不支持修改已运行输入，则按官方能力追加指令或后续 run，不能谎称当前任务已改写。

验收：启动后主对话能继续回复；后台完成可查产物；取消后不再发新工具调用；404、服务重启和异常状态明确显示；测试服务本地重启丢失运行时不能显示完成，保存父映射后报告 interrupted/lost。Demo 不承诺 Agent Protocol 开发服务器崩溃后无损续跑。
