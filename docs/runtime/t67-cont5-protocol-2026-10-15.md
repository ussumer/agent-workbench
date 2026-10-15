# T67 cont5 完整开发校准协议

用户“继续吧”恢复主线。前置a8d7c31已修探索/SDK真实重试/中断，d7c7dfb修网关落账。cont4在doctor READY后端口preflight失败，0新增模型调用。真实TIME_WAIT红绿及两次连续锁定服务创建/清理证明修复，见t67-cont4-port-failure-2026-10-15.md；本次新证据下单次修订，不恢复cont4。若同类故障再现立即停止讨论。

旧cont3三skill均完整但session1/3有4个503未知usage及隐藏重试造成的回调计数错位；当前pre-model源码也已改变，不能满足严格reuse绑定。不放宽原guard，不用旧业务分数。遵循原已授权完整路线3fresh独立train-only技能，再15base+15fewshot，5test×3repeat×2arm。新技能生成和solve均同120tools/60models/600steps/8192output，8无进展回合或12返回停止。模型仍配置deepseek-flash，无SDK隐藏重试，同任务/split/评分器/栈/两臂预算不变。

独立目录artifacts/experiments/t67-gdpevo-group025-ds-20261015-cont5；config runtime.cont5.json。运行前新freeze及真实doctor，旧目录/失败/manifest不改；源码snapshot保存runtime/gateway/modelconfig及Git revision。历史155请求/已知部分估79.173954CNY/4个unknownusage单列，新调用/失败/费用完整保留。用户已撤销金额缩小限制，仍保留原单次/全局调用保护；不增加重试，不恢复T68。

不自动恢复失败进程。同类执行机制若再失败、两个连续基础设施/格式失败或约20分钟没有实质进展，停止并保留证据讨论，不叠补丁重跑。完成后只读verify cont5全30行/33独立scope、调用账本、计算/清理；Stage3六独立clean-context原票至少五通过，整体gate指向cont5。原DS难度条件base.40–.60、fewshot提升.10–.30、fewshot<.80不改。开发证据不称封存泛化/统计训练增益，不自动生产晋升。当前尚未启动新模型。
