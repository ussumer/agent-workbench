# T67 两份技能复用与完整配对校准

用户于 2026-10-14 明确继续：复用 run2 的两份真实技能，只独立生成第三份，然后运行同组 15 base / 15 fewshot。新目录为 `artifacts/experiments/t67-gdpevo-group025-ds-20261014-cont1`。原 run1/run2 保留 failed，不恢复 T68。

运行前比较当前与历史任务、train 正确示例、模型配置、依赖、Java 种子/镜像、OpenSandbox 镜像和影响模型输入的代码。每份技能正文必须与真实终末模型消息一致，未经语义编辑。历史 13 次调用的预留、完成、回调与 token 用量已全部核对；导入本身新增模型调用 0。第二份技能原有 stdio closed=false 保留，另附与 owner/thread/sandbox 绑定的进程和容器不存在观察，仅适用于这次已证实的后处理故障，不放宽新 Actor 清理检查。

证据：`gdpevo-construction/v4-20261012/task_factory/scratch/runtime/reuse-import-probe2-result.json`；旧技能各有 2 次真实完成计算。影响范围测试共 69 passed、0 failed、0 skipped，报告 `continuation-final-tests.xml`。两项缺口的 red 报告为 `reuse-prepaid-red.xml`，修复 green 为 `reuse-prepaid-green.xml`。独立范围复审没有剩余重要发现。

运行从配置读取 deepseek-flash，费用上限遵循此前已接受的完整实验授权：gateway 不执行历史 config.t46 的 10 元/16 调用缩小版限制。每会话 30 次模型调用、36 次工具调用，输入/输出和超时协议保持原版。全部失败与未知 usage 保留；新账本从 1 开始，历史 run2 的 1–13 单独验证，不重新编号或重复计费。当前费用、复用费用、run1 失败费用分别报告，实际账单未知。

新运行先完成零模型真实 doctor（Java/MCP/OpenSandbox/Mongo/隔离/清理）；未 ready 不开始付费。运行失败不自动重试。完整校准后独立重评分并按官方 Stage3 进行 6 个干净上下文评审，至少 5 个通过。DS 难度目标仍是 base .40–.60、lift .10–.30、fewshot <.80，失败不改 rubric 凑分。到时 gate 必须核对真实 cont1 与质量评审；当前代码和导入通过不代表任务集难度已接受或训练有效。
