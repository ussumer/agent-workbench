# T67 cont5：30次开发尝试完成，正式校准仍未通过

已完成3份独立DS技能和30次业务尝试（5道test×3次重复×base/fewshot），源码cbaea2b0dc09ced8c7588285315160603b063547。模型身份deepseek-flash来自配置，实际sync/async SDK retry均0。同任务、split、Java/H2、Mongo、MCP、OpenSandbox、DeepAgents及预算协议。此为DS/课程栈适配开发校准，不是官方GPT校准、封存泛化或TRACE动态训练收益。

## 可复核业务结果

以下均分按每臂全部15次固定分母计算；4次无有效答案的base尝试按诊断失败0计入，未重跑替换。因此这些是开发尝试均分，不能称为formal avg@3。

| 臂 | 尝试数 | 有效提交并实际评分 | 无提交失败 | 固定分母开发均分 |
|---|---:|---:|---:|---:|
| base | 15 | 11 | 4 | 40.62% |
| few-shot | 15 | 15 | 0 | 64.76% |

开发均分差24.14个百分点。15组配对中few-shot高8组、同分6组、低1组。差距按同一15分母作算术分解：17.44个百分点来自4组base无提交而few-shot完成，其余11组的差额合计6.70个百分点。这不是因果归因，不能把全24.14个百分点称为财务推理或TRACE训练提升。

| 题目 | base 三次（%） | few-shot 三次（%） | base均分 | few-shot均分 |
|---|---|---|---:|---:|
| test_001 | 0.00* / 61.54 / 46.15 | 46.15 / 61.54 / 61.54 | 35.90% | 56.41% |
| test_002 | 57.14 / 57.14 / 57.14 | 57.14 / 57.14 / 57.14 | 57.14% | 57.14% |
| test_003 | 0.00* / 0.00* / 61.54 | 61.54 / 76.92 / 76.92 | 20.51% | 71.79% |
| test_004 | 78.57 / 64.29 / 64.29 | 71.43 / 64.29 / 64.29 | 69.05% | 66.67% |
| test_005 | 30.77 / 0.00* / 30.77 | 61.54 / 76.92 / 76.92 | 20.51% | 71.79% |

星号0*表示没有有效提交的失败占位，不是独立评分器对真实答案判出的0。其余26份实际答案全部经原私有评分器独立重评分一致。四个失败：r1-test_001-base、r1-test_003-base、r2-test_003-base、r2-test_005-base，均为no_progress终止；四次失败记录中的completed沙箱计算均为0，停在来源发现阶段。来源发现中重复记录、空页及猜不存在文档ID仍是弱点；同题后续base也能完成，不能把一次失败视为稳定能力。

## 调用与费用

保守配置费率估算，保留原始usage和缓存token字段，不是提供商实际账单。

| 阶段 | 模型请求 | 输入token | 输出token | 估算CNY |
|---|---:|---:|---:|---:|
| 三份技能生成 | 24 | 1,219,872 | 17,495 | 11.451213 |
| base执行 | 190 | 8,233,695 | 167,025 | 78.612930 |
| few-shot执行 | 116 | 2,804,362 | 169,488 | 29.815434 |
| 合计 | 330 | 12,257,929 | 354,008 | 119.879577 |

few-shot执行加三份技能生成共41.266647元估算；base执行78.612930元估算。两臂单次工具/模型/图步预算相同，费用差来自实际执行路径与上下文。新330请求全部HTTP200、usage完整、pending0、gateway_requests_drained=true。cont4模型费用0。此前155请求/已知部分估79.173954及4个未知usage单列保留；全部历史合计485请求，已知部分保守估199.053531元，历史未知仍未知，actual_cost_cny仍null。

## 核验与真实清理

- 33个独立owner/thread/sandbox（3技能+30尝试），675个MCP工具请求与回调参数/归属逐项对齐；330个模型请求与回调及token用量全部匹配。
- 424份原run manifest文件哈希一致；runtime/gateway/control/modelconfig快照和原Git身份保留，原failed/result/manifest未覆盖。
- 真实完成计算：技能13、base86、few-shot70，共169次；数量不是有效贡献证明，computation_contribution仍unassessed。
- 真实Docker当前列表核对全部33个执行容器和本次控制容器已不存在，两网络已移除，相关MCP worker已退出；原清理记录同为passed。
- 终止保护本轮避免继续空转，但4次直接结束图，没有留下真实终末JSON。组件止损有效不等于业务或正式校准完成。

## 正式验收为何仍blocked

原官方guides/calibration_runtime.md:197要求产生expected answer.json或完整skill；206明确不完整运行不计formal avg@3。guides/calibration_and_review.md:190要求每题3次valid base及3次valid fewshot。DS/栈适配未覆盖这项要求。用户要求全部重复计入、失败保留，因此本报告用完整30尝试作开发诊断，同时明确formal valid提交尚缺4份，不只挑成功样本。

开发均分数值落在暂定base.40–.60、提升.10–.30、fewshot<.80范围内，仍不能替代有效提交条件。原运行时verify已经核对完账本、33会话、26重评分及清理，剩余reasons恰为上述4次无提交，没有另一个隐去的本轮故障。

最终gate：artifacts/tasks/T67/20261015T100631Z/receipt.json，blocked，必需检查exit2，明确指向cont5。正式校准未过，未启动Stage3六票质量评审，不伪造投票或标T67 done。T68不恢复。

## 已登记但尚未实现的下一步

1. 设计并验证no_progress/预算终止后的受限真实Actor收尾提交：仍受原总预算，保留原失败和所有调用，宿主不得编造或修补业务答案。下次运行协议需明确完整有效提交与固定分母诊断的关系，不自动补跑本轮。
2. 区分可核对的探索工具错误和真正模型/账本/隔离故障。verify_model_ledger中对tool_error一律否决的静态分支缺口要有专门回归；本轮账本已全部通过，此分支不是本轮blocked原因，不借此放宽有效提交条件。
3. 本轮三份clean技能/完整原trace可供下一协议核对复用；当前不改严格绑定、不重新生成、不启动新实验。业务评分器、难度区间及原证据保持原样。

证据目录前缀：gdpevo-construction/v4-20261012/task_factory/scratch/runtime/。

- cont5-final-audit.json: SHA-256 76b3cca8e60709541b2c6a099ad7a5094629142f0039923ebcd183afcf5953e3
- cont5-verification.json: SHA-256 87b59d831717411ae2889de18c3fcb57928c3009ad5623889752a7d5725d7624
- cont4-failed-audit.json: SHA-256 82c07d1cb112ef83c8c511f9cb4ef2f76571ece171aee2a79fb63f63c96f3fff
- control-port-green.xml: SHA-256 2578062948772cc2234c75fabd4bf8e0bd5063a50be1f292f65d9e9ad88d6591
- gateway-drain-green.xml: SHA-256 34bc63bb8552b794fa6fd466dbc7a8b15b8144c8675d9ae018d6a593b532d984
