# T67 无效探索与真实重试修复

用户“你解决一下”授权修复上一轮已定位问题。主线先零模型证明，不覆盖旧failed，不重生成技能，不自动恢复付费。

父拥有taskset/test_t67_discovery：基于cont3原messages录制可移植重放fixture；按成功返回事实内容而非request ID/参数识别新信息；错误和重复空列表不重置，真实分页/新记录/状态进展放行；8轮无新信息或12个连续无新信息工具结果停止，原120/60/600总体预算不改。补通用各domain导航、空total语义、documents只可ID详情，不泄漏Gold/SOP或换ERP实现。

SDK代理仅config.py/test_t67_client：向create_chat_model添加可选max_retries关键字并传给构造器，无参数旧行为不改；真实sync/async SDK+httpx.MockTransport503各一次请求，零外部模型。父runtime显式0并核实实际root SDK字段，doctor可验证。

父补原生中断收据和正在执行scope：捕获KeyboardInterrupt/CancelledError，记录stopped状态、当前scope调用与cleanup，并始终保留用量/manifest；原历史stale running不补写。

先red→green、真实轨迹离线重放、真实SDK本地503检查，再独立review及最小真实零模型预检。提交仅有效修复及协议/状态。完整30业务与Stage3仍未完成，不把组件通过当学习或V4完成。

复审补充：计算进展以真实状态哈希判定，不按version递增；中断保持已生成技能/已评分成绩，取消优先普通Actor/清理/退出错误。网关迟到usage边界明确未修，纳入T67下一次付费前检查，不扩大本轮外部工作区改造。
