# 会话、审批与 Vue 契约 v1

## 标识和状态

thread_id、run_id、request_id、interrupt_id、event_id 均有独立含义；服务器生成 thread/run，客户端生成请求去重 UUID。用户来自会话 Cookie。

run 状态：`running -> interrupted | completed | failed | cancelled`；interrupted 表示一次运行停在 checkpoint，resume 产生新 run 并关联原 thread/pending action。每次写入使用条件更新，重复 resume 不能启动两个运行。

## HTTP

| 端点 | 请求或用途 |
|---|---|
| POST /api/demo/session | `{user_id:"demo-a"}`，仅两种固定演示身份 |
| POST /api/chat/stream | `{thread_id?:string,request_id:string,message:string}` |
| POST /api/chat/{thread_id}/resume | `{request_id,interrupt_id,resume:{supplement:string}}` 或 `{request_id,interrupt_id,resume:{decisions:[{type:"approve"或"reject"}]}}` |
| GET /api/chat/{thread_id}/state | 最新 run 状态、todos、pending_interrupts、last_event_id |
| GET /api/history | 当前用户 thread 列表，page/page_size |
| GET /api/history/{thread_id} | 展示消息、运行概要、待办中断 |
| DELETE /api/history/{thread_id} | 活动运行时 409；空闲时删除该 thread 展示和 checkpoints，保留跨会话偏好/技能及业务订单 |
| POST /api/chat/{thread_id}/cancel | 停止继续推理/工具调度，不回滚已提交 ERP 写 |
| GET /api/artifacts/{artifact_id} | 按归属下载，缺失返回 404，不接受 path 查询参数 |

拒绝空白消息或超过 20000 字符；同一 thread 活动时新请求 409。相同 request_id 和同正文查原 run；相同 ID 不同正文 409。其他用户的 thread/artifact 返回 404。

## SSE

采用 POST + fetch ReadableStream；原生 EventSource 不支持本请求体。UTF-8 用流式 TextDecoder，解析器跨 chunk 保存缓冲区，支持多行 data 和空行分隔，不假设一网络 chunk 等于一事件。

```text
id: run-uuid:12
event: tool_result
data: {"v":1,"thread_id":"...","run_id":"...","seq":12,"source":"procurement-order","tool_call_id":"...","payload":{"ok":true,"data":{}}}

```

统一 envelope：v=1、thread_id、run_id、seq、source、payload；source 是 main/子 Agent 名，不从文本猜。所有事件递增 seq，事件 ID 唯一；15 秒 heartbeat 用 SSE 注释。

| event | payload | 规则 |
|---|---|---|
| run_started | request_id、status | 每 run 一次 |
| token | text、message_id | 仅展示文本块；推理块不当作回答 |
| tool_start | name | 每 tool_call_id 一次 |
| tool_args | delta | 保留分片，完整后才解析 JSON |
| tool_result | ok、data/error 或 artifact_id | 对应同一 tool_call_id |
| tool_end | status | 成功/失败均终结，不重复 |
| todos | items[{content,status}] | pending/in_progress/completed |
| interrupt | interrupt_id、interrupt_type、提示与候选动作 | 类型见下方 |
| artifact | artifact_id、name、mime、size | UI 用下载 API，不显示宿主路径 |
| error | code、message、retryable | 脱敏，不能接 completed |
| done | status、interrupted、content? | 每 run 恰好一次；表示本次流终结，不一定任务完成 |

框架入口使用 `stream_mode=["messages","values"]`、`subgraphs=True`、v2；todos 可从 values 变化提取。归一化集中在一个适配器。values 的 interrupts 先处理并持久化，再发送 interrupted done；不能仅凭终端 token 判断完成。消息块可能是结构化 list，不能 `str(content)` 全部输出。

不保证两个独立事件源全局先后，只保证自身状态机一致：消息按 message_id/call_id 聚合，interrupt 状态和终结不可被迟到 token 覆盖。以 T06 捕获的真实 chunk 构造回归 fixture。

## 两层人工介入

第一层 `order_info_supplement`：缺字段时调用 `request_order_info`，payload 包含 missing_fields、已有候选值、自然语言说明。补充无效则再次中断；严禁猜物料、数量或价格后下单。

第二层 `hitl_approval`：展示 supplier、lines、每行金额、总额、创建/修改、旧/新值。图必须在 MCP 写执行前停住。一个待审写动作一次审批；批量并发写在本 Demo 不支持。approve/reject 只能由 resume 接口可信输入产生，不从普通聊天文字自动解析为批准。

resume 先校验归属、interrupt_id、类型、当前状态及去重；然后 `Command(resume=...)` 使用原 thread 配置。框架可能从节点开头重放，副作用由稳定 operation_id 防重。参数变化/版本冲突导致旧授权失效并要求新审批。

## 断线、崩溃、历史

浏览器断开不自动重发 POST。服务端运行任务由受管理 run registry 持有，前端回来先 GET state/history；可轮询展示当前结果，不要求逐 token 断点重放。事件和展示消息按 ID upsert，不能恢复后重复追加整段历史。

进程退出时 running run 经 checkpoint 对账为 failed/recoverable 或 interrupted；只有持久 checkpoint 已确认可续跑才恢复。若写结果未知，用原 operation_id 对账/重试，不产生新订单。UI 显示实际状态，不能假称停止等于撤销交易。

展示历史独立保存，不随摘要丢弃。删除操作同时删除 checkpointer 的 thread 数据（使用安装版支持 API），无活动 run 才允许。保留业务数据和用户技能属于明确行为。

## Vue 页面

首屏为工作界面：左侧历史和演示账号，中央对话/工具轨迹，右侧或折叠面板显示 todos、审批、报告。Vue 3 + TypeScript + Vite，使用 lucide-vue-next 图标。避免营销首页、假仪表盘和硬编码聊天回复。

必须有空状态、加载、失败、断线、补充、批准/拒绝、生成中、完成、下载失败和历史删除确认。审批按钮发送后禁用；刷新可恢复待办；两个 tab 同时点击只成功一次。Markdown 不启用原始 HTML，图表 URL 校验协议并提供加载失败状态。

桌面 1440×900、窄屏 390×844 验证：历史抽屉收起，长工具参数换行/滚动，批准按钮可点击，图表和报告资产非空，输入不被底栏遮挡。Playwright 截图只作为布局证据，还需 DOM 行为断言。

## 初始化与失败终态（T34）

run_started 在图提供器初始化前发送；图获取、resume读取原问题、用户偏好保存及checkpoint终态读取均在本次时长预算保护内。同步初始化在线程等待，不阻塞取消请求。初始化异常发送error并持久化failed，释放活动线程后发送一次done；失败收尾不重新调用图提供器，失败请求重放只读原run，不重新初始化。

初始化等待期间取消可直接终结为cancelled，未开始的模型/工具不会启动。Python无法强行停止已经运行的同步线程；超时或取消后丢弃迟到结果，禁止其进入该run的astream。已开始的初始化内部SDK/数据库操作仍按自身超时收尾，显式用户偏好写入不冒称回滚。图提供器仍为同步owner到compiled graph接口，生产栈预先装配的图与现有配置保持。

## 规划会话（T40）

POST /api/planning/{thread_id}/goal创建独立规划会话，输入budget/demands、不接受owner/grant；读取实际ERP并归档原始JSON。无规划provider拒绝，其他owner线程404，有旧课程对话的线程不切换。已有课程chat/resume/state/SSE形状保持。

planning_submit可在同一工具中逐单interrupt。LangGraph给同任务连续中断复用其任务ID，故规划事件/state使用工具控制面给出的planning_action_id作为公开interrupt_id，恰是原pending_action ID；不另造审批/operation。恢复仍用官方Command(resume)，按原interrupt顺序消费。API仅允许当前checkpoint正在等待的规划订单被决定；旧单/未展示的后续单返回STALE_INTERRUPT。模型或直接graph resume不能写服务端批准记录。

T41 规划run装配可配置planning_episode_store与显式planning_model_identity；正式Demo已配置，旧课程路径不采集该Episode。目标首次run绑定bank，chat/resume跨run聚合；归档错误通过EPISODE_EVIDENCE_FAILED/failed可见，ERP已发生写入不回滚。API completed与Episode业务成功独立，默认Episode保持open/unscored。完整环境快照与独立反馈接入另验。

T43 增加PATCH /api/planning/{thread_id}/goal：预期revision、局部预算/需求更新及指定物料ERP刷新。修订只改目标/来源/历史对账，不调用模型。旧规划审批/签名grant失效；用户后续发送新的chat消息时，服务器可通过官方Command恢复过期工具并提交新用户输入，旧工具STALE_PLAN不能写入，随后仍逐单重新批准。保持原课程chat/resume/SSE字段，规划Episode/bank不切换。
