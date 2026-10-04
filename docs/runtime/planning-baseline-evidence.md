# T46 真实基线证据与零生成复核

训练前原始 Git 基线为 `3c36215`。当前仍没有 Curator、训练或学习消融结果。

## 已有证据

外部会话目录 `/mnt/c/dev/rsi-eval/procurement_eval/runs/planning-session-20261004` 保留全部失败尝试。
`attempt-7` 证明配置模型 `deepseek-flash` 自行计算初始合法最优方案 2493.50 元。
批准必需物料一单后，预算改为 2200.00 元，旧审批收到 STALE_PLAN；同一个 Episode
读取持久 analysis 数据重算，新增 P004 六件，实际 ERP 两单合计 2196.00 元。
它没有执行原 2500 元方案的两笔订单，不能作为该独立场景的完成证据。

`scripts/planning/evidence.py` 只读核验 immutable attempt：文件 manifest、归档源码、
Episode 全部序号与 payload hash、owner/thread/run/bank、ERP 完整订单集合和实际金额、
已执行批准载荷及 hash、计算 operation/version/终止报告与权威 JSON。
复核会重新运行独立裁判，不生成模型调用。报告明确列出仍未覆盖的项目。

```bash
PYTHONPATH=src .venv-linux-t45/bin/python -m scripts.planning.evidence \
  /mnt/c/dev/rsi-eval/procurement_eval/runs/planning-session-20261004/attempt-7 \
  --output /tmp/t46-business-audit-new.json
```

输出文件使用排他创建；不要覆盖先前报告。修改测试或评测器后，先用已有证据复核，
不需要重新启动 ERP、MCP、Agent Protocol 或沙箱，也不需要重跑 Actor。
常驻服务上的 API/Agent 热重载属于开发运行方式；当前 T46 执行入口仍是单次隔离执行，
没有把服务复用或热重载命令伪记为已实现。

## 运行前冻结与 prepare

`live_baseline.py` 在生成网关启用前记录实际 ERP JAR hash、当前 Python 全部已安装依赖、
Agent Protocol 独立环境依赖、执行与 execd 镜像 ID、配置模型及 provider `/models` 身份，
并独立归档外部评测器源码。服务启动后检查真正执行的 JAR 与冻结 hash 一致。
这些文件保存在控制面，不上传到 Actor 工作区。

`--prepare` 的网关硬限制为零模型生成。它启动真实服务、验证沙箱边界和空 ERP，
退出时保留 result、模型调用空日志、manifest 和服务日志。prepare 成功不等于业务成功。
已有一次带冻结的真实零生成成功证据为 `attempt-prepare-frozen-025040`，无需重复执行。

```bash
PLANNING_LIVE_SESSION=/mnt/c/dev/rsi-eval/procurement_eval/runs/planning-session-20261004/attempt-7 \
PLANNING_PREPARE_SESSION=/mnt/c/dev/rsi-eval/procurement_eval/runs/planning-session-20261004/attempt-prepare-frozen-025040 \
UV_PROJECT_ENVIRONMENT=.venv-linux-t45 .venv-linux-t45/bin/python scripts/gate.py T46
```

最近 gate `artifacts/tasks/T46/20261004T025447Z/receipt.json`：33 unit、68 原回归、
4 已有真实 Actor 证据检查、1 真实 prepare 检查，106 passed / 0 failed / 0 skipped。
它只证明已实现的断言，不将 T46 提前标 done。

## 剩余验收

- 当前历史付费 attempt 无事前 JAR/依赖/评测器冻结，不能由后来 prepare 追认。
  新的独立 2500 元双单场景需要在生成前冻结，且完整核对两单和零额外订单。
- attempt 整体 deadline 和取消尚需入口实现与真实停止证据；不能只取消宿主等待。
  需要撤销计算提交资格，确认进程及子进程停止，无法确认时隔离环境。

费用授权继续为累计 50 CNY、每 attempt 2 CNY；保守累计 5.118624 CNY，实际账单字段仍 null。
本次只有零生成 prepare 和只读复核，未新增付费模型调用。TRACE 固定/raw/curated 三组、
成功失败轨迹训练、未见组合与反事实评测仍需后续登记和独立真实结果。
