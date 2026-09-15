# 运行与诊断规格

以下是 T00/T24 要实现并最终实测的命令合同；本轮交付时应用命令尚未存在。实际启动命令、版本和端口会写入 docs/runtime/runbook.md。

## 配置项

| 变量 | 示例或含义 |
|---|---|
| LLM_BASE_URL | 用户实际网关，不填猜测的默认地址 |
| LLM_MODEL | 用户实际 API model ID |
| LLM_API_KEY | 本地密钥 |
| LLM_PROTOCOL | 经 CAP-01 确认的协议/客户端适配类型 |
| LLM_CONTEXT_TOKENS | 经上游规格确认的容量，不从名称推断 |
| MONGODB_URI | 仅应用服务可达的本地实例 |
| MONGODB_DB | rush_harness_demo |
| ERP_BASE_URL | 网关所见 Java 内网地址 |
| ERP_SERVICE_TOKEN / INTERNAL_SERVICE_TOKEN | 服务间凭据，禁止下发模型/浏览器/沙箱 |
| MCP_ERP_URL | API 所见 MCP URL |
| OPENSANDBOX_DOMAIN / OPENSANDBOX_API_KEY | 管理服务地址和凭据，变量映射到锁定 SDK |
| SANDBOX_IMAGE | 固定 tag/digest |
| FIXTURE_BASE_URL | 沙箱可达的报价站 URL |
| ZHIPU_API_KEY | 智谱搜索 |
| MODELSCOPE_MCP_URL / MODELSCOPE_TOKEN / MODELSCOPE_TRANSPORT | 实际远端配置 |
| ASYNC_AGENT_URL | Agent Protocol 地址 |
| APP_PROFILE | demo / test / live，不自动 fallback |

.env.example 只有说明与无敏感占位，缺必填配置明确报错。日志脱敏 Authorization、Cookie、查询串 token；不为了诊断输出全部环境变量。

## 启动顺序

1. `python3 scripts/doctor.py` 检查工具、配置存在性、端口与 Docker；只报密钥是否设置，不展示值。
2. `uv sync --frozen`；frontend 使用 npm ci；Java 使用固定 wrapper 或构建容器。
3. `docker compose -f infra/compose.yml up -d --build` 启动本地基础服务。OpenSandbox 控制服务按其官方要求单独配置 Docker 访问及执行容器网络，不能假定普通 Compose 容器就足够。
4. `python3 scripts/seed_demo.py --if-empty` 创建数据，不删除已有订单。
5. 分别启动 MCP、Agent Protocol、FastAPI，最后 Vue；T00 建立 scripts/dev.py 管理命令或明确终端说明，服务出错停止并列出故障。
6. `python3 scripts/smoke.py --profile integration`；配置真实模型/外部服务后再 live 检查。
7. 浏览器打开 http://localhost:3000。若端口冲突，修改单一配置并同步代理/API URL，不关闭用户其他进程。

OpenSandbox 的宿主地址、服务内地址、执行沙箱内地址分别记录。localhost 在执行容器内不是宿主；不能用 curl 宿主成功代替沙箱网络测试。

## 常见故障

| 现象 | 先查 | 修复/验收 |
|---|---|---|
| java 存在但构建失败 | javac、JAVA_HOME、wrapper | 用项目JDK构建，执行JUnit，不改全局Java |
| 工具调用没出现 | 模型协议、bind_tools、脱敏原始响应 | CAP-02，不改成字符串伪工具 |
| interrupt 没显示 | v2 chunk、values、子图ns、checkpoint | 重放CAP-05 fixture，不靠token文本猜 |
| resume 重复订单 | operation_id、冻结body、request去重 | V04/V10，查Java数据库 |
| 新技能发现不到 | assignment、revision、Store与沙箱路由 | 下一轮重扫，核对manifest/hash |
| 重启技能消失 | 是否使用InMemoryStore、卷、namespace | 跨进程Store读取与重建测试 |
| sandbox创建成功但execute失败 | execd连通、镜像、网络、SDK配置 | 容器内执行探针，禁止宿主兜底 |
| 图表有URL但页面空白 | 远端鉴权、MIME、跨域、URL失效 | 下载并验证资产、前端加载断言 |
| async一直排队 | Agent Protocol worker配置、运行状态 | 检查真实任务ID，不无限轮询 |

## 清理

服务停止与数据重置分离。默认 down 不加 volumes；仅显式 Demo reset 清理本项目标记的数据、沙箱和产物，需 `--confirm-demo-reset`。不能用 docker system prune、全局 rm 或清空共享 Mongo。重置期间禁止新run，先停止或对账活动写操作。
