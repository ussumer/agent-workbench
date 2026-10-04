# T38：持久数据，每步独立计算

用户2026-10-03批准以本机制替代活kernel。真实计算始终在OpenSandbox，Mongo是权威数据；Agent提供Python代码，系统只提供通用数据接口，不提供采购planner。

阅读顺序：`src/agent/tools/planning_computation.py`（模型可见接口）→ `src/agent/planning/computation.py`（可信身份、版本与发布）→ `src/agent/planning/computation_process.py`（上传到沙箱执行的监督程序）。Actor提示与装配在`src/agent/planning/actor.py`。

## 一次预算变更

第一步已核验并保存quotes、constraints和required。后续调用computation_execute声明read_names=["quotes","constraints","required"]，例如：

```python
quotes = load_state("quotes")
constraints = load_state("constraints")
constraints["budget"] = 220000  # 单位由Agent显式组织，这里使用分
required = load_state("required")
candidates = {"optional": (constraints["budget"] - required) // quotes["C"]}
save_state("constraints", constraints)
save_state("candidates", candidates)
```

load_state返回从本步执行副本加载的新Python数据；修改返回对象本身不会保存。save_state写临时JSON，正常结束后监督程序确认所有后代已结束，API再次验证JSON/operation/base_version，并原子发布新版本。其他名字的已提交值保留。未声明read_names的数据不能通过load_state读取；无需全量打印报价。依赖关系、来源版本、结果是否需要重算由Agent组织并交给公开planning_check作业务复验，数据服务不替Agent判断最优性。

## 三个正确性边界

- 失败不发布：代码异常、非法JSON、输出超限、超时、取消均不改变已提交值。候选临时文件或历史execution记录不等于权威版本。Mongo发布应答丢失先原子撤销旧操作资格并核对提交记录；无法确认则uncertain，不声称回滚。
- 真正停止：监督程序使用Linux subreaper接管孤儿后代，重复终止并waitpid回收，包括double-fork和setsid。只有全部子进程结束才允许发布；无法确认时Mongo和稳定proxy都隔离旧容器，重启仍拒绝。只有替换容器解除，替换失败不放行旧环境。
- 拒绝迟到提交：owner/thread/session来自可信runtime，Mongo独占active operation与base version做CAS，取消先撤销资格；容器generation在发布期间锁定，替换不能穿过提交窗口。每个operation ID只能使用一次，不重放有副作用代码。

API正常重启与容器替换均重新加载已提交JSON。执行中API崩溃时调用computation_recover明确撤销遗留operation，确认停止后释放活动位；如果发布已成功则按权威记录恢复完成状态。不使用自动过期租约假定旧计算已结束。函数、模块、DataFrame与对象身份不持久，下次显式重建。

接口：computation_execute/status/cancel/output/recover。默认60秒、最大300秒，排队计入期限；代码64KiB、最多32个数据名、累计JSON4MiB；单stream输出上限4MiB，展示32KiB并保留完整分页。session只共享数据，不共享Python命名空间。同owner容器仍共享workspace；通用计算事务不回滚任意文件/网络副作用。正式订单继续planning_submit、逐单审批与独立MCP。

## 验证范围

新检查为tests/acceptance/test_t38_computation.py与test_t38_process.py。真实Mongo/OpenSandbox机制检查与原沙箱/规划回归同一gate归档；旧kernel/Jupyter失败记录保留。此处无付费模型，组件通过不证明真实Actor规划质量、学习空间、TRACE收益或迁移。
