"""A separate DeepAgents planning Actor, using durable data and disposable sandbox computation."""

from __future__ import annotations

import json
from typing import Any

from deepagents import create_deep_agent
from langchain.agents.middleware import AgentMiddleware, TodoListMiddleware
from langchain_core.messages import ToolMessage

from agent.approval.middleware import MCPWriteChannel
from agent.config import ModelConfig
from agent.middlewares.conversation_archive import build_archived_summarization
from agent.planning.computation import ComputationService
from agent.planning.orders import PlanningOrders
from agent.tools.planning import build_planning_tools
from agent.tools.planning_computation import build_computation_tools

PLANNING_PROMPT = """你是独立采购规划 Actor，不采用课程最低价报告作为决策流程。
先读取本thread公开问题与必要来源。目标顺序：满足所有必需量/交期硬约束，按优先级
最大化可选件数，最后降低总成本。方案可有多个合法解；planning_check只验可行性。
用computation_execute自己组织报价、约束、候选与比较。每步独立Python进程；用
load_state(name)/save_state(name,value)加载/暂存JSON。read_names只声明本步所需数据，
预算/来源变化时识别受影响数据并重算，未变化报价无需再抓。成功后原子发布新版本，
失败不更新旧数据；函数/模块/DataFrame显式重建，不保持对象身份。不得用固定planner
替代自己的决策。计算事务不回滚任意文件/网络副作用，订单必须走审批工具。
来源缺失或矛盾与业务不可行不同；缺证据则核对或提出明确澄清，已知预算充足且来源
齐全不应无条件提问/拒绝。不要读取私有裁判、未来任务或封存答案。
提交前调用planning_check，解释价格/期限取舍、数量缺口和来源。planning_submit只提交
候选并逐单等待用户批准，模型无权批准。拒绝或过期后不偷改参数重试。版本改变后旧
批准不可用；已经写入的订单不能重新全量购买。Plan.lines始终表示本轮追加量；
commitments是已写订单，按原成交价/计划交期计入累计预算与需求，不重复提交。
revision_change标明局部受影响物料，全局预算耦合仍需重验。输出明确区分候选、待批和实际订单。
"""

# Defaults injected by DeepAgents are filtered too, so shell execution or a generic
# subagent cannot bypass the planning computation/approval boundary.
FORBIDDEN_TOOLS = frozenset({"execute", "task", "order_create", "order_update"})


class PlanningToolBoundary(AgentMiddleware):
    def wrap_model_call(self, request, handler):
        return handler(request.override(tools=[t for t in request.tools
                                               if getattr(t, "name", "") not in FORBIDDEN_TOOLS]))

    async def awrap_model_call(self, request, handler):
        return await handler(request.override(tools=[t for t in request.tools
                                                    if getattr(t, "name", "") not in FORBIDDEN_TOOLS]))

    def wrap_tool_call(self, request, handler):
        refused = self.refuse(request)
        return refused if refused is not None else handler(request)

    async def awrap_tool_call(self, request, handler):
        refused = self.refuse(request)
        return refused if refused is not None else await handler(request)

    @staticmethod
    def refuse(request) -> ToolMessage | None:
        call = request.tool_call
        if call["name"] in FORBIDDEN_TOOLS:
            return ToolMessage(content=json.dumps({"ok": False, "data": None, "error": {
                "code": "PLANNING_TOOL_FORBIDDEN", "message": "use computation and submitted-plan approval tools",
            }}), tool_call_id=call["id"], name=call["name"], status="error")
        return None


def build_planning_actor(
    *, orders: PlanningOrders, kernel: ComputationService, channel: MCPWriteChannel,
    backend: Any, checkpointer: Any, store: Any, model: Any = None,
    model_config: ModelConfig | None = None, middleware: tuple[Any, ...] = (),
    episode_store: Any = None, selector_model: Any = None,
) -> Any:
    configured = model if model is not None else (model_config or ModelConfig.from_env()).create_chat_model()
    trace = []
    if episode_store is not None:
        from agent.evolution.orchestration import PlanningTraceMiddleware
        trace = [PlanningTraceMiddleware(episode_store, selector_model or configured)]
    return create_deep_agent(
        model=configured, tools=[*build_planning_tools(orders, channel), *build_computation_tools(kernel)],
        backend=backend, checkpointer=checkpointer, store=store, system_prompt=PLANNING_PROMPT,
        middleware=[*middleware, TodoListMiddleware(),
                    build_archived_summarization(configured, backend), PlanningToolBoundary(), *trace],
    )
