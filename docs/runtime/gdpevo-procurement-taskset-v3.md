# GDPevo-inspired 采购任务集 v3（T54，已通过确定性 gate）

v3 在冻结的 v2（5 train + 5 held-out test）之外新增四组，每组 5 train + 5 held-out test，共 40 道新主任务；累计 50 道主任务。新增 40 个单因素反事实，累计 48 个。旧 v2 fixture、旧评分和 T53 真实试跑未改写。

四组复用 v2 fixture 的采购字段及旧 oracle，不修改生产 PlanningProblem schema。v3 仅增加任务分组元数据，以及两项独立审计资料与输出（下文说明）。

| 组 | 企业规则与不同决策结构 | 未见训练组合数 |
| --- | --- | --- |
| `quotes` | V 正式版本、E 生效窗口、X 撤销、M 证据相关性；报价的时间、状态和价格区间共同影响选择 | 4 |
| `packages` | U 整包、S 现场盈余、C 容量/交期、O 非贪心覆盖；供应商运费与整数选择耦合 | 4 |
| `kits` | K 闭包、P 优先级、I 空 kit 独立例外、S 现场限制；组选择和独立成员竞争预算 | 4 |
| `revisions` | H 成交事实、D 差量、M 相关补证、C 累计预算；跨修订保留历史、只追加剩余量 | 5 |

B 是公开的权限、历史与字段权威边界，F 是本轮供应商购物车费用规则；两者在每组全部五道 train 中明确给出。审批安全不作为隐藏学习陷阱。这里的 kit 例外使用既有 `kit_id=null`，没有引入工程豁免接口。

训练材料只在 `fixtures/planning/gdpevo-procurement-v3-training.json` 的 train 任务中出现；公共任务在 `fixtures/planning/gdpevo-procurement-v3.json` 中不包含答案、rubric 或私有控制文件。私有答案和控制面分别位于 `fixtures/planning/private/gdpevo-procurement-v3-answers.json` 与 `fixtures/planning/private/gdpevo-procurement-v3-control.json`。`scripts/planning/gdpevo_expansion.py stage` 一次只返回一个允许阶段的 Actor 视图。

每个主任务（含 train）都有六个二值评分点，权重总和 13。为了避免同一错误重复扣分，采购结果的 disposition、allocation、该计划的 freight_cents 和审批 lines 一起只算一个点。其余五点分别审计不同业务资料。

| 评分点 | 权重 | 核验内容 | 训练锚点 |
| --- | --- | --- | --- |
| procurement | 3 | 完整采购/停止决策；接受同目标所有并列最优 | 本题矩阵内全部规则，分别追溯实际 train |
| source_selection | 2 | 不在 demands 中的独立目录关系正式文件审计；采购关系来源不再单独扣分 | 本组 train-01～05 的 B |
| freight_audit | 2 | 独立 `audit_carts` 的门槛、历史金额排除与空购物车费用 | 本组 train-01～05 的 F |
| commitment_ledger | 2 | 原历史成交量、实付和 ID 的保留 | 本组 train-01～05 的 B |
| approval_boundary | 3 | 当前 revision 与禁止复用旧批准，合为一个权限结果 | 本组 train-01～05 的 B |
| erp_claim_conflicts | 1 | 所有原始商务文件 ERP 字段声明的冲突登记 | 本组 train-01～05 的 B |

`audit_carts` 是其它待审购物车，不纳入候选预算；目录审计关系不可新增采购；`erp_claims` 不能覆盖 ERP active/交期/容量。这两种输出扩展为 `freight_audit` 和 `erp_claim_conflicts`，仅供离线资料评测，尚无生产 adapter。数组顺序无意义时忽略；整数与 bool 严格区分。

裁判做两条路径：旧 v2 oracle 作为参考，`scripts/planning/gdpevo_expansion_judge.py` 另行按每份报价的整数包数枚举有限状态，并比较可行性、覆盖、累计成本、pending lower/upper 边界和完整 winner set。每组至少 8 个单因素反事实，实际记录 before/after；本次 40/40 的决策、可行性或最优目标发生变化。报价样板先单独人工核对，再扩展其余组；`artifacts/tasks/T54/quote-sample/README.md` 保留样板证据，早期失败草稿保留在 `artifacts/tasks/T54/design-rejected-*` 和 `resume-rejected-*`。

最容易误判的具体案例与反事实证据：

| 主任务 | 正确取舍 | 只改一个条件后的结果 |
| --- | --- | --- |
| quotes-test-02 | 正式新价 4×130=520 恰好免邮，pending 最低 4×140=560 被支配，执行且不补证 | quotes-cf-03 把区间下界改为 90，最低 360 改变最优成本，变为 needs_information |
| packages-test-05 | 最低单件价组合 270+180运费+240+100运费=790 超预算750；S2整车 400+240=640 免邮，覆盖可选4件 | packages-cf-09 只把 S1 运费改0，混购变为610并优于640；cf-10 只把 S2门槛从640改641，正确成本变740 |
| kits-test-01 | kit成本540但高优先级仅2件；独立成员方案550覆盖高优先级3件，优先执行550方案 | kits-cf-01 只把预算600改540，独立550被排除，改买闭包kit540，覆盖[2,3] |
| revisions-test-01 | 历史4件400不回滚；本revision只补1包2件150，历史不能凑新车免邮，总400+150+120=670，审批revision3 | revisions-cf-02 只把新车门槛550改150，才免邮并降到550；cf-01预算改650后真无解 |

包装组另有非单调边界 `packages-test-02`：必需单买480+100运费=580；加60分可选件后免邮，总540反而可行。`packages-cf-04`预算降至539，则全体方案均不可行。`packages-cf-03`切换depot后增加另一合法并列最优；裁判接受两个最优，而非只接受原参考方案。

确定性 gate：`artifacts/tasks/T54/20261004T145714Z/receipt.json`，138 个测试、0 failed、0 skipped。验证报告包含 40 主任务、40 反事实、240 个 rubric 点、0 model calls。输入 hash：

```text
public     8a731bc56b3795ab61e638866974ea007d2ae2db6e8d9dd78468e3575710fe15
training   ecf39b6b9f08f4d3ed5038192b5f7c4ac93076d811ded6ed644fce0314822c5f
control    2f8721a2ff970955cd7e974a2b2168f1d5bb5de18a23f04f563651b1dadcf7f8
answers    3cc71f859f74fab712be18a0b9a8784f7aa3a77cda859e6bdc26eb5009f58132
```

这是合成业务资料的确定性验证，不是模型校准结果：没有证明 base 40–60%、训练后提升 0.1–0.3 或最终低于 0.8，也没有验证生产 Actor 的工具执行。报价、包装、kit、承诺字段仍需 adapter 映射到 Java ERP、MongoDB planning_goal、MCP 审批和 OpenSandbox 计算；fixture 不是实时 ERP 状态。训练仍隔离在 `/mnt/c/dev/rush-harness-training` 使用 T55，本仓库未修改 `gdpevo_training.py` 或训练运行目录。

规则—训练—测试—评分点完整映射（以下编号均为该组 task 后缀；B/F 在各组单列）：

| 组 | 规则 | train 锚点 | test 重组使用 | 评分点 |
| --- | --- | --- | --- | --- |
| quotes | B | 01, 02, 03, 04, 05 | 01, 02, 03, 04, 05 | approval_boundary, commitment_ledger, erp_claim_conflicts, procurement, source_selection |
| quotes | F | 01, 02, 03, 04, 05 | 01, 02, 03, 04, 05 | freight_audit, procurement |
| quotes | V | 01, 03, 05 | 01, 02, 04, 05 | procurement |
| quotes | E | 02, 05 | 01, 02, 03, 05 | procurement |
| quotes | X | 03 | 01, 03, 04, 05 | procurement |
| quotes | M | 01, 04, 05 | 02, 03, 04, 05 | procurement |
| packages | B | 01, 02, 03, 04, 05 | 01, 02, 03, 04, 05 | approval_boundary, commitment_ledger, erp_claim_conflicts, procurement, source_selection |
| packages | F | 01, 02, 03, 04, 05 | 01, 02, 03, 04, 05 | freight_audit, procurement |
| packages | U | 01, 02, 03, 05 | 01, 02, 03, 04, 05 | procurement |
| packages | S | 02 | 02, 03, 05 | procurement |
| packages | C | 03, 05 | 01, 02, 03, 05 | procurement |
| packages | O | 04 | 01, 02, 04, 05 | procurement |
| kits | B | 01, 02, 03, 04, 05 | 01, 02, 03, 04, 05 | approval_boundary, commitment_ledger, erp_claim_conflicts, procurement, source_selection |
| kits | F | 01, 02, 03, 04, 05 | 01, 02, 03, 04, 05 | freight_audit, procurement |
| kits | K | 01, 03, 05 | 01, 02, 03, 04, 05 | procurement |
| kits | P | 02, 05 | 01, 03, 04, 05 | procurement |
| kits | I | 03 | 01, 02, 04, 05 | procurement |
| kits | S | 04 | 02, 03, 05 | procurement |
| revisions | B | 01, 02, 03, 04, 05 | 01, 02, 03, 04, 05 | approval_boundary, commitment_ledger, erp_claim_conflicts, procurement, source_selection |
| revisions | F | 01, 02, 03, 04, 05 | 01, 02, 03, 04, 05 | freight_audit, procurement |
| revisions | H | 01, 05 | 01, 02, 04, 05 | procurement |
| revisions | D | 02, 05 | 01, 02, 03, 05 | procurement |
| revisions | M | 03 | 02, 03, 04, 05 | procurement |
| revisions | C | 04 | 01, 03, 04, 05 | procurement |

每条规则的触发条件、输出字段和停止适用条件见私有 control 的 `groups.*.rules`；逐个测试评分点的训练锚点见 `rubrics.*.rule_anchors`。验证报告为四组分别保留 `rule_train_test_scoring_map`、每题 `main_validation` 和反事实完整 before/after。固定手算预期与计算过程位于 control 的 `manual_checks` 和 `counterfactuals.*.manual_expected/calculation`，并非运行时从 oracle 填入。

阶段视图命令示例：

```bash
python3 scripts/planning/gdpevo_expansion.py stage --task quotes-test-02 --stage test --output /tmp/actor-input.json
```

命令只读取公共输入及阶段材料，不读取私有答案或控制面；输出只有该题和公共契约。后续真实模型实验仍必须在独立容器中只挂载该文件及该次合法学得技能，不能挂载整个仓库。此次只做阶段视图和确定性检查，未启动实际 Actor/沙箱运行，未验证生产工具执行。

生产映射边界：预算/需求/priority/required、ERP active/lead、历史 commitments、revision 和逐单审批语义已有 T37/T39/T43 对应接口；实际数据必须从真实 Java ERP/MCP/Mongo 读取。商务版本/有效期/撤销/价格区间、最小包装/盈余/运费、kit 闭包和独立目录/票据/冲突审计仍需 source/constraint adapter。现有生产约束不允许超买且不计运费，不能直接把含包装盈余、运费的 fixture 作为真实 Plan 提交。DeepAgents/LangGraph、Vue、Java ERP、MongoDB、MCP、OpenSandbox 栈保持不变，私有求解器不得安装为 Actor 工具。
