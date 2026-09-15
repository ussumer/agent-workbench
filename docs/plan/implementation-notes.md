# 易错实现顺序

以下为应用算法约束，不是可直接粘贴的特定版本框架代码。执行者必须使用T06验证过的签名，把每条约束落实到测试。

## 一次创建订单

```text
session -> trusted owner
request_id -> reserve run with unique key
load graph/checkpoint for owner+thread
model delegates procurement-order
missing fields -> interrupt -> persist state -> interrupted done
supplement -> same thread Command(resume)
complete candidate -> order_create interrupt_on
persist pending action + frozen body bytes + stable operation ID
show approval -> user approves exact interrupt
CAS pending -> approved; reserve resume request
Command(resume=approve) continues same checkpoint
tool wrapper matches frozen args and loads trusted grant
MCP validates grant -> Java writes transaction using stable operation ID
Java commits order + response record -> tool result -> final answer
```

API只负责授权和恢复，不直接绕开图调用订单写接口。工具包装器匹配候选参数时先做同一schema规范化（金额两位字符串、省略note统一为空字符串），生成冻结body一次；实际重发使用存储字节，不重新json.dumps改变hash。

批准记录、graph checkpoint和Java事务不是一个分布式事务。必须以状态对账补齐窗口：批准后图未启动可重试同resume；Java提交后图未checkpoint可重发同operation；pending已存在但checkpoint未确认中断时不显示可批准，先读取graph状态。无法确认时明确recoverable/failed，不生成新operation碰碰运气。

## 图缓存和用户绑定

缓存key至少包含user_id、YAML配置revision、技能revision；缓存项持有该用户稳定proxy，绝不能重绑给另一用户。图运行拿到不可变的user/thread/run上下文；每个thread设置独立checkpoint配置。

可以在运行入口取得proxy再创建图，health middleware负责恢复已绑定失效容器；不要要求middleware首次运行前，backend构造阶段就读一个不存在的沙箱。首次创建/初始化可以由manager在入口完成，middleware继续保留每次健康检查。

同一用户跨thread共享文件容器，但工作目录带thread/run；工具身份头每调用独立构建。不同用户并发测试要交错执行，不只顺序切换。

## 事件归一化

维护message_id到文本buffer、tool_call_id到name/args/result/status的映射。流片暂缺call ID时用本次模型消息ID与tool index暂存，获得ID再合并；不能只用工具名当key，因为同工具可调用多次。

工具参数分片到齐后才parse JSON。ToolMessage可先于完整文本终结；按call ID完成工具状态。interrupt和done的状态写入幂等；服务断线后的恢复读取持久run/checkpoint，不重复追加整个聚合buffer。

## 技能发布窗口

Store文件写入不要求跨文件事务。用不可变版本目录+manifest+assignment指针：任何缺文件/校验失败都不发布指针；读者只读取完整manifest指向的版本。指针条件更新失败时保留既有版本，并报告并发发布结果。

不要在每次 before_agent 重跑生成模型；恢复仅搬运已经验证的文件。主动生成/修复才消耗模型调用。新revision让下一次graph调用刷新发现缓存，避免在正在审批的run上重建graph。

## Gate的自举与重入

T00的acceptance测试需调用gate底层函数或在临时仓库运行一个小型测试manifest。不得在test_t00.py里再次执行本仓库gate T00，否则无限递归。正式gate只在外层执行一次。

相同原则适用于T22：全量回归选择unit/contract/integration及Java/Vue套件，不重新收集acceptance/test_t22.py或执行gate --all自身。最终包装验收读取已经产生的报告，再核对V01-V22。

所有gate参数中的 `{evidence_dir}` 替换为相对仓库根的POSIX路径；当前配置JUnit命令cwd为根。日志路径/argv/cwd必须与receipt一致。路径不从shell插值，构建工具配置和浏览器输出均避免覆盖上次attempt。
