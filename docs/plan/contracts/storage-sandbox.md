# 持久化、文件路由与沙箱

## MongoDB 分工

| 内容 | 逻辑存储 | 主键/索引 |
|---|---|---|
| 图状态及待恢复中断 | MongoDBSaver，使用官方插件自己的集合 | 插件索引，thread_id 经归属校验 |
| 长期文件 | LangGraph MongoDBStore | namespace + key，插件维护 |
| 会话 | threads | thread_id 唯一，owner+updated_at |
| 展示消息 | display_messages | thread_id+message_id 唯一 |
| 运行与请求去重 | runs | run_id 唯一；owner+request_id 唯一 |
| 待审批动作 | pending_actions | interrupt_id+tool_call_id 唯一 |
| 沙箱登记 | sandbox_registry | user_id 唯一；sandbox_id 唯一 |
| 技能发布元数据 | skill_versions、skill_assignments | owner+scope+slug+version，当前指针条件更新 |
| 下载文件 | artifacts | artifact_id 唯一，owner+created_at |

数据库分别为开发 rush_harness_demo、测试 rush_harness_test；每次集成测试使用独立后缀或明确命名空间。不启用向量检索，不要求 Atlas。插件必须通过普通本地 MongoDB 的精确 namespace put/get/list 测试。

skill_versions 复用既有版本唯一索引预占编号，新增 publication_status=reserved/failed/persisted 与 reservation_id。预占/失败记录不作为已发布元数据返回，不删除其编号或半写文件；旧无状态版本兼容。assignment 仅引用可查询的已发布版本，complete 固定起始 revision 后一次 CAS；存储与指针不构成跨文件事务。没有增加新的数据库或进程内状态存储。

课程图示出现 InMemoryStore 与 MongoDB 两种写法。为满足已确认的重启持久化目标，运行 profile 必须使用 MongoDB-backed Store；InMemoryStore 仅用于明确标注的单元测试。checkpoint、Store、展示消息互不替代。

## 文件路由

| Agent 虚拟路径 | 后端 | 用途 |
|---|---|---|
| 默认，包括 /workspace/、/skills/、/AGENTS.md | 用户 SandboxBackendProxy | 代码执行和临时分析 |
| /memories/ | StoreBackend，用户隔离 namespace | preferences.md、历史摘要引用 |
| /persisted-skills/ | StoreBackend，课程 skills namespace | 发布后的技能包文件 |

保留课程 `('skills',)` 的逻辑 namespace，可在 key 路径下加入 `/users/{user_id}/{scope}/{slug}/{version}/...` 隔离用户创建技能；公共预置技能来自只读 src/skills。Store 路由适配器只让当前 owner 访问自己的 key 前缀，不能靠提示词限制读取。

偏好 namespace 显式包含 user_id，例如 `('memories',user_id)`。不要仅在文件名写 user_id 却允许任意 glob 到其他用户。命名空间获取使用 T06 验证过的 runtime API。

沙箱 shell 不会自动看见 MongoDB 虚拟文件；执行所需技能/偏好由应用复制到沙箱受控目录。持久文件变化先写 Store，确认成功后更新执行副本。不能让“同步回去”成为保存唯一途径。

## 五层组件

| 文件 | 责任 | 不应承担 |
|---|---|---|
| custom_opensandbox.py | 实现已安装 Backend/Sandbox 协议，SDK I/O、错误映射 | 用户池、业务授权 |
| sandbox_setup.py | 镜像和工作目录初始化、工具环境检查 | 每次请求 pip install |
| sandbox_proxy.py | 显式同步/异步委托、replace_backend 稳定句柄 | 跨用户共享可变 backend |
| sandbox_manager.py | 预热、认领、登记、重连、重建、回收 | 直接处理聊天事件 |
| sandbox_health.py | before_agent 探测并请求恢复 | 自己再维护第二套池 |

讲义提到 18 个显式代理方法，以已锁版协议方法列表为准，生成方法覆盖测试；不能只实现 execute/read/write 就宣称等价。不要用盲目 `__getattr__` 隐藏不支持的异步方法。

## 生命周期

`creating -> warm -> claimed -> unhealthy -> recovering -> claimed`；回收进入 `destroying -> destroyed`；失败进入 failed。登记包含 user_id、sandbox_id、generation、状态、image_digest、last_seen、失效时间。

1. get_or_create(user) 加用户锁。先检查内存可用 proxy，再查 Mongo 登记并 SDK 重连，再认领预热池，最后新建。
2. 预热池默认 1 个。认领用锁保证独占，绑定 owner 后后台补充一个；warm 容器不可含其他用户数据。
3. health 失败时在恢复锁内二次检查。只允许一个重建者；创建新容器、初始化、同步预置技能、恢复持久技能、上传运行时 AGENTS.md 后才 replace_backend。
4. generation 加一，Mongo 登记成功后把新 backend 发布给 proxy；处理失败则回收新容器，不能把半初始化容器暴露给运行。
5. 已运行中的旧容器调用返回明确错误；不自动重放可能有副作用的 shell 命令。只在下一次安全调用使用新 generation。
6. 服务重启优先重连有效登记；SDK 返回不存在则重建。停止只销毁本项目标记的沙箱，不停止用户其他容器。

一个用户的 shell 命令用执行队列串行化，路径分为 `/workspace/{thread_id}/{run_id}/`；只读 Agent 推理仍可并行。异步子 Agent 也必须经过同一 owner 的沙箱租约，不各自创建无登记副本。不同用户至少验证两套 proxy 和 sandbox_id。

因为Agent Protocol是独立进程，不能共享Python内存锁。T20为异步图提供薄的远端backend适配器，通过FastAPI内部 `/internal/sandbox/operations` 调用主进程的manager/proxy；请求包含可信owner、thread、operation_id和允许的协议操作/参数。内部token与归属验证不可省略，不接受宿主路径或任意manager方法。主进程是唯一沙箱分配与执行租约持有者，异步进程不直接认领/重建容器。它仍使用OpenSandbox，不引入第二种沙箱服务。

## 最小运行约束

- 默认沙箱 1 CPU、1 GiB、命令超时 60 秒、单输出截断展示 32 KiB并可保存完整文件；SDK 支持和实际限制在 T08 证明，不能仅写配置。
- 主机 OpenSandbox 管理进程可访问 Docker，业务执行沙箱不能挂 Docker socket，也不挂项目源码、.env 或宿主根目录。
- 网络只允许演示资源、明确外部抓取/依赖域名；浏览器、容器、宿主的 localhost 不同，真实测试沙箱能访问报价站。
- 下载归档解压检查绝对路径、`..`、符号链接、文件数和解压总大小；普通文件访问规范化后验证根路径，不靠字符串 startswith。
- 执行镜像含 Python 和分析/抓取所需依赖，T08 固定镜像。新增依赖通过有版本的构建变更，不在每次对话静默安装。
- 课程规则文件同步为只读执行副本；模型可创建技能但不能改系统审批策略。容器隔离不替代 ERP 授权与用户数据归属检查。

## 关键验收

真实容器 execute、读写编辑/搜索/上传下载、超时；两个用户隔离；同用户多 thread 使用同一 proxy；预热同时认领不重复；删除容器后恢复；重启 API 后重新连接；技能和偏好恢复而临时文件可以丢失；命令失败不在宿主兜底执行。

## 真实操作熔断边界（T33）

每个 SandboxManager 持有一个 BreakerRegistry，创建/重连的 proxy 绑定同一 owner 的 breaker；live 装配使用 manager.breakers，不再另建无调用方的注册表。proxy 的所有同步/异步 I/O 入口在实际调用前取得原子 admission。连续三个基础设施失败熔断30秒，返回 SANDBOX_CIRCUIT_OPEN；冷却结束仅一个调用能探测，成功关闭，失败重开。manager health 遇到熔断拒绝向上报告，不据此自动销毁和重建容器。热替换保留 proxy 与 breaker 身份。

execute 超时124、异常终止125和明确传输异常计故障；正常运行但业务脚本非零退出、文件不存在/权限/路径错误不计。SDK 文件传输故障在错误映射前用 invocation-local ContextVar 留证，继承的组合文件方法也能观察 execute 故障；不会解析模型文本推测沙箱健康。每个公开 proxy 调用最多记录一次失败。未知应用异常继续抛出，不伪造成功；本地拒绝不证明健康，也不能永久占住半开探测额度。旧调用带 admission 的 opened_count，迟到结果仅计观察数，不改变新轮次熔断状态。进程重启后熔断计数重置，业务数据和租约仍持久于 Mongo；此进程内故障控制不替代数据库。

新增内部参数 SandboxBackendProxy.breaker、SandboxManager.breaker_registry 为可选，默认也启用真实边界保护；文件协议返回类型与内容保持，新增拒绝异常带稳定 code。影响回归：T08/T09/T19/T21/T23/T24，T33 对实际超时、同步异步、隔离、冷却、并发单探测、文件 SDK 故障和迟到成功验证。总调用预算和熔断是两种独立限制；摘要归档和进化评测另包。

健康中间件直接调用 ensure_healthy；manager 用同一个 owner 恢复锁覆盖健康检查与必要重建，已知故障进入 _recover_locked，不重复对死容器探测。并发请求在锁内重新检查新的 generation，保留原“只重建一次”能力。091644 回归暴露的两项恢复失败保留，原 T09 断言不改。

本地 integration profile 的 control_settings 固定 127.0.0.1:18080，遵循其既有 CONTROL_HOST/PORT；不从开发 OPENSANDBOX_DOMAIN 借用远端或别名，开发配置不改。093041 原协议23通过/1端点别名断言失败保留，原固定端点断言保持。

094537 端点修正首次仅固定 DOMAIN，仍被开发 BASE_URL 覆盖，失败1/24保留；最终 profile 同时固定 DOMAIN 和完整 http BASE_URL，先独立验证失败项再完整复跑。

100024 原pool因孤儿扫描同一全局managed-by标记，删除并发预算回归的两个活跃容器而失败；budget-dependency.xml 4通过/2故障完整保留。running_pool 默认工厂现在按数据库SHA-256短摘要设置稳定管理标记，重启同库可找回孤儿，不同库/测试/dev池互不清理。自定义工厂保持其显式标记；生产 OpenSandboxFactory.DEFAULT_MARKER 不改。新真实双池案例检查另一库未登记的warm容器在cleanup后仍可执行。历史全局标记warm不自动扩大扫描删除，旧已登记容器仍可按ID重连；同库多进程孤儿清理仍要求既有单实例运维语义。
