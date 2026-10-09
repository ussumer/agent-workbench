# T68：20组持久计算配对消融，服务失败后的诊断报告

本次没有建立持久计算的质量或成本收益结论。20组配对、40条轨迹、120个业务回合记录齐全，但17个环境失败回合影响比较，4条轨迹没有真实完成计算。任务保留blocked，不把矩阵记录完整当作有效实验完成。

使用合成v3全部20道development test，单次重复。模型由配置读取，为`deepseek-flash`，`https://api.deepseek.com/v1`，temperature=0、thinking disabled。没有Curator、学习技能、few-shot、Selector。每条同线程轨迹依次做初始规划、同输入复核、预算降低10%且revision递增的重规划；两臂原始来源、commitments、工具权限和护栏相同，保留各自历史。A保留派生Mongo JSON，B逐计算调用淘汰；双方每次计算后同样更换真实OpenSandbox容器。

用户因时间限制明确改为20组一次重复。原三轮attempt中17条完整轨迹和第18条B的两回合对话被复用；中断的在飞请求不评分但保留费用。旧protocol、manifest与日志不改写，没有挑最好一次。当前目录为`artifacts/experiments/t68-persistent-paired-20261009-single`；旧目录为`artifacts/experiments/t68-persistent-paired-20261009`。

## 全分母结果

失败回合计0分，下面保留全部20对。三回合全过要求每回合六个评分点都通过；末回合是预算修订后的结果。

| 指标 | A：持久JSON | B：逐调用重建 |
|---|---:|---:|
| 三回合全过 | 12/20（60%） | 13/20（65%） |
| 末回合全过 | 15/20（75%） | 15/20（75%） |
| 末回合平均分 | 0.8269 | 0.8654 |
| 成功取得评分的回合 | 50/60 | 52/60 |
| 环境失败回合 | 9 | 8 |
| 格式失败回合 | 1 | 0 |
| 成功模型callback次数 | 206 | 199 |
| 已知输入tokens（评分轨迹） | 2,240,216 | 1,951,956 |
| 已知输出tokens（评分轨迹） | 117,921 | 111,900 |
| 计算调用尝试 | 155 | 147 |
| 派生JSON真实复用调用 | 2 | 0 |
| 模型代码字节 | 191,667 | 200,354 |
| 业务回合累计耗时（秒） | 1192.9 | 1116.7 |
| 计算执行累计耗时（秒） | 154.2 | 140.5 |
| 容器启动累计耗时（秒） | 374.0 | 336.3 |
| 容器回收累计耗时（秒） | 97.4 | 89.5 |

末回合得分A胜/B胜/平为2/2/16；三回合全过的配对结果A胜/B胜/平为3/4/13，其中包含环境失败，不是纯能力胜负。模型callback不含全部失败请求及中断在飞请求，不能作为完整费用账本。

诊断子集：双方末回合都取得评分的17对，末回合均分A=0.9729、B=0.9593；双方三个回合都取得评分的14对，三回合全过均为11/14、末回合均分A=0.9670、B=0.9505。子集剔除了环境/格式失败，存在选择偏差，只辅助定位，不能替代主表或宣称收益。20个不同任务的一次配对可做开发诊断，不能证明稳定优势；120个相关回合不是120个独立样本。

## 服务失败与验收

- `revisions-test-03/A`的三个回合及`revisions-test-04/05`双方4条轨迹中的12个回合，合计15个回合为OpenAIAPIError。完整网关账本对应45次HTTP 503，当前attempt的`denied_model_requests=0`，没有本地预算拒绝。网关既会传递上游HTTP 503，也会把网络异常映射为503，原日志未保留更细原因，无法确定是供应商还是本机网络/代理。只读无凭据HEAD检查现已获得HTTP 401，说明检查时网络可达，不证明运行时故障原因。
- `quotes-test-05/B`和`kits-test-01/B`首回合为SandboxApiException：事件没有sandbox_id且recycle返回false。manager源码中false表示没有登记proxy；现有证据不能把该事件计为成功回收，也不能据此断言发生文件泄漏。
- `packages-test-03/A`首回合为JSONDecodeError，保留0分。
- `revisions-test-04/05`双方4条轨迹完全没有completed计算证据。不能把这些轨迹作为完成真实计算的对照。

首次gate为`artifacts/tasks/T68/20261009T072005Z/receipt.json`：10项unit通过，live evidence失败于`sandbox residue not excluded`。只读检查器随后改为完整报告计算覆盖/回收缺口并返回blocked，未把缺口放行；ERP前后比较改为`data`订单载荷，排除每次读取都不同的request_id。新增测试同时防止缺失计算误通过及真实订单变更误放行。旧失败receipt与冻结运行源码均保留。

最终gate为`artifacts/tasks/T68/20261009T072352Z/receipt.json`：11项unit通过（无失败/跳过），live evidence exit 2、blocked。任务状态blocked。两个gate均只做测试和只读证据检查，没有模型调用。自动检查没有证明业务效果。

独立诊断核对：manifest哈希、配对输入、评分重算、schema/权限相同、B仅保留task、summary重算全部一致；ERP前后订单载荷一致，均零订单。A的155个已登记容器ID互不相同，B的145个已登记ID互不相同，另两次创建失败没有ID。真实零模型probe通过，只证明保存/加载/淘汰机制。模型轨迹真实复用发生在`packages-test-01/A`的plan和`kits-test-02/A`的result，共2次跨调用读取；不能声称复用广泛发生或已证明跨业务回合收益。v3 commitments是合成资料，本次不下单，不能当真实ERP成交或生产adapter验收。

## 完整费用与交接

旧、新日志按各attempt的call去重，共452次请求：407次HTTP 200且有usage，45次HTTP 503无usage。已知输入4,223,060、输出230,646 tokens，已知部分按实验保守单价估算44.234982 CNY；45次未知用量不能当零，实际账单未知。原`report.json.usage`只含新attempt的238次请求，不是完整账本，不改写原始报告。

逐对数据：`artifacts/tasks/T68/20261009T072005Z/paired-results.csv`；独立诊断与全账本汇总：同目录`diagnostic-summary.json`。这两个是运行后诊断，未覆盖或更改原manifest和receipt。

T65维持done，T64/T65成果与生产ComputationService没有修改，T67仍pending。本次不追加付费模型运行。若用户另行决定补齐，需要先确认模型/沙箱服务正常，在新attempt中按预先约定处理失败配对，保留当前全部失败与费用，不能把补跑当稳定性重复或覆盖旧证据。
