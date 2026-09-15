# 技能、记忆与上下文

## 预置技能

保留课程目录和用途，每个技能有可用示例与失败说明：

| 路径（相对 src/skills） | 作用 |
|---|---|
| main/skill-management/ | 下载或创建、验证、分配、持久化流程 |
| procurement/procurement-analysis/ | 查询预警、比价、计算、报告 |
| procurement/supplier-price-urls/ | 供应商 ID 到演示报价 URL 的映射 |
| procurement/web-scraper/ | 沙箱内 HTML 抓取与结构化报价抽取 |
| procurement/web-content-fetcher/ | HTML 转 Markdown，不把网页当系统指令 |
| procurement/chart_params.md | 本次真实远端图表目录和参数索引 |

SKILL.md 用 YAML frontmatter，至少 name、description；正文写业务步骤、输入输出、失败条件。名称 slug 使用 `[a-z0-9]+(-[a-z0-9]+)*`，最长 64。脚本放 scripts，样例放 examples，不把实际 API 密钥写入任何技能。

## 新技能案例及接口

核心新增技能 `reorder-cost-summary`：输入已查询的预警和已抓取报价，输出补货数量、金额合计及 Markdown 表格。脚本接收 JSON 文件和输出目录，不自行发起订单写操作。

输入行 `{part_id,supplier_id,quantity,unit_price,currency,source_url}`，数量整数、金额字符串；输出含 lines、total_amount、currency、source_urls。固定测试 P001 42×24.00 + P004 30×17.50 = `1533.00`。代码必须实际执行并解析结果，不用模型说“测试通过”代替。

管理工具：`assign_skill(source_type, source, slug, target_scope)`；source_type 为 generated/package，source 为沙箱生成目录或资源站批准 URL，target_scope 为 main/procurement-analyst/procurement-order。未知 scope 拒绝；未指定 scope 时通过补充信息请求询问用户，不能默认为所有 Agent。工具返回 skill_id、version、status、validation_artifact_id。

## 发布状态机

`draft -> validating -> validated -> persisted -> assigned`，失败进入 validation_failed/persistence_failed；恢复失败独立 restore_failed，不删除已存版本。

1. 模型生成到 owner 沙箱 staging 目录，或真实下载 ZIP 到 staging。记录来源、SHA-256、创建时间。
2. 校验文件列表、frontmatter、脚本入口、输入输出 schema。ZIP 默认压缩包<=2 MiB、展开<=10 MiB、<=100 文件，不接受 symlink/路径穿越。
3. 在沙箱执行固定输入的 smoke 和错误输入检查，保存 exit code、stdout/stderr、输出文件 hash。失败将错误反馈给模型，最多修复 2 次，每次留记录；仍失败不发布。
4. 由服务端分配不可变 version 和 manifest。将完整文件内容及校验和写 Store 版本前缀；全部写完读回校验，再标 persisted。
5. 条件更新 owner+scope+slug 的 assignment 当前版本指针；只有 manifest 完整的版本可被发现。半写版本不成为有效技能，后续清理。
6. 同步执行副本到 `/skills/users/{scope}/{slug}/`，标记 skills_revision。框架扫描缓存按 revision 在下一次调用刷新；不承诺当前 graph run 内即时发现。
7. 沙箱重建/应用重启时读 Store manifest，恢复指定版本，hash 校验后原子替换目录。不要从临时 download 目录恢复冒充持久化。

相同内容同 owner/scope 重复发布返回原版本；内容变更生成新版本。删除 assignment 不删除其他 scope；清理未引用 staging/旧副本不影响当前版本。全局共享用户生成技能不在当前范围。

创建/下载/分配/清理均通过主 Agent 的 skill-management 能力实施。使用新技能须在调用轨迹留下 SKILL.md 读取、脚本执行和产物，不能只打印技能名称。

## 渐进披露和两条恢复通道

首次仅注入技能 name/description/path，不全量拼接所有正文。任务需要时 read_file 正文、必要脚本，再执行。预置技能根据清单 hash 增量同步；用户持久技能从 Store 恢复，二者分别统计与测试。

技能缓存的存在不代表可永久跳过扫描；assignment revision 变化、沙箱 generation 变化或 Agent 重建时失效。YAML 新增子 Agent 在下一次显式图重建后生效，不承诺运行中热重载。

## 用户偏好

preferences.md 四个显式偏好：language=zh-CN、currency=CNY、output_format=markdown、chart_type=bar。允许用户要求表格/其他支持图表，按 schema 更新；当前金额没有汇率服务，要求换算外币时说明不支持真实换算，不把 CNY 改标签当兑换。

两个自动历史字段：recent_supplier_ids（最近 5 个去重）和 recent_queries（最近 10 条简短采购查询）。只有成功 ERP 相关任务更新；闲聊、失败、拒绝写单不记录为成功采购。自动更新不得覆盖用户明确偏好，不保存密钥、网页指令或完整工具大响应。

读取和写入都经 owner namespace，保存成功后新 thread 读回。用户说“以后报告用表格”是偏好更新；网页中的“请修改用户偏好”不是授权。采用结构化内部 schema 生成 Markdown，不能让不受控模型输出覆盖整个配置对象。

## 上下文和中间件

保留启动规则、用户偏好、技能元数据、子 Agent 返回的分层注入。子 Agent 返回结构约定：summary、facts、artifact_ids、warnings、next_action；由任务提示和验证器约束，失败时说明缺项，不能解析失败后造空成功结果。

课程自定义顺序：health -> context_injection -> skills_sync -> user_skills_restore -> 主动摘要工具 -> memory_update -> sandbox_breaker -> model/tool limits。按锁定版本的实际 hooks 实现，记录 before/after/wrap 的真实调用顺序，不能把列表位置当全部执行时序。

自动摘要保留框架能力；主动 compact_conversation 单独暴露。摘要前完整消息已在历史/可读归档留存；压缩后保留未完成 todos、pending tool calls 和审批信息。工具返回大内容 offload 到文件并返回引用，不仅截断正文。

阈值基于实际配置的上下文容量，不能假设用户模型无限上下文。Demo 默认每 run 模型调用上限 30、工具调用 60、总时长 300 秒，异步分析 600 秒；测试用低阈值触发。配额作用于主子整体共享计数，子 Agent 不能另开预算绕过。

沙箱连续 3 次失败熔断 30 秒，health 成功探测后半开；仅计沙箱操作失败，不把业务 422 当沙箱故障。受限时返回明确错误和状态，不能无限重试或转宿主执行。
