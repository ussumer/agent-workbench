# SE00 主 Agent 审阅

状态：返修后主 Agent 审阅通过，已批准登记 SE00 完成并继续 SE01。用户的实施规格确认不等于代码人工审阅通过，用户 review 保持 not_reviewed。

## 已验证

子 Agent 提交 `procurement_eval/checks/SE00-5839df52e47941e9af3a4be325390fbd/receipt.json`，记录 27 tests、0 failed、0 skipped、0 model calls。主 Agent 在评测工作区独立执行 `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s procurement_eval/tests -v`，得到相同的 27 项通过结果。本轮没有新增模型调用。

已审阅 protocol.py、plan_guard.py 与协议测试；核对组身份隔离、显式旧 split 转换、新组执行拒绝、反馈字段白名单与候选文件范围。schema 语义、证据引用和正文泄露的服务校验已登记后续工作包，不在本包冒称完成。

## 必须修订

`plan_guard.check()` 对 done 收据仅检查 status/task、非空 evidence 及 hash，没有验证测试数、失败/跳过数或实际检查项。主 Agent 在 `/tmp/se00-review-*` 创建独立临时计划，通过原检查器复现：

- task 要求至少 27 测试；state 为 done。
- receipt 的 status 为 passed，但 tests_total=0、tests_failed=99、tests_skipped=99、checks=[]。
- 提供一个有效 tests.log 的 SHA256。
- check() 仍接受该计划。

这不是对现有 27 项真实测试结果造假的指控，而是检查器对矛盾收据缺少拒绝能力。

gate 当前硬编码 unittest runner，却把计划声明的 checks 原样写入 receipt。应限制本阶段可支持的具体命令，或真实执行登记的 argv，并验证记录一致；不能修改计划命令而保留旧 runner 后仍声明执行通过。

同时验证收据必需元数据、review 枚举、证据路径根目录限制与符号链接拒绝，补零测试/失败/跳过、检查项不符、路径逃逸的对抗测试。保留旧收据，修订后新建 gate attempt，再审阅。后续未实现 live gate 保持显式 blocked。

子 Agent 已收到修订任务；评测文件写入继续使用正式权限机制，不在临时目录冒充部署。

## 返修复审

新收据：`procurement_eval/checks/SE00-f916aa269f57457eadfab90a09255801/receipt.json`。

主 Agent 阅读返修的 plan_guard.py、protocol.py、CLI 入口与对抗测试，确认已补齐上述收据校验及混合实验身份的入口拒绝。独立复跑全套 40 项测试：0 failed、0 skipped；独立调用 validate_receipt 核验新收据通过，model_calls=0。

新增回归覆盖伪 passed、计数矛盾、执行声明、证据路径和混合组配置。旧 27-test 收据和本机 UTC 回拨导致自检失败的 attempt 保留；gate 使用 UTC 起始锚点与 monotonic elapsed 生成有序执行时间。

已通知子 Agent 登记 SE00 done 后继续 SE01。后续真实执行验收尚未完成，未实现的 live gate 继续拒绝，新增付费预算仍待实际运行前确认。
