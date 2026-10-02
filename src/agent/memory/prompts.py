"""Prompt assembly for the main agent and its sub-agents.

Prompts are built from data, not concatenated inline at the call site, for two reasons:
the delegated context must be *small and relevant* (a sub-agent that receives the whole
parent transcript cannot tell which parts were instructions to it), and the user's
preferences must reach the sub-agent in the same shape every time.

The runtime rules live in ``AGENTS.md`` next to this module and are read from disk, so the
text the agent actually receives is the text in the repository rather than a paraphrase.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

MEMORY_DIR = Path(__file__).resolve().parent
AGENTS_MD_PATH = MEMORY_DIR / "AGENTS.md"

#: Preferences with their contract defaults (contracts/skills-memory.md).
PREFERENCE_DEFAULTS: dict[str, str] = {
    "language": "zh-CN",
    "currency": "CNY",
    "output_format": "markdown",
    "chart_type": "bar",
}

#: Preference keys a user may set explicitly.
USER_SETTABLE_PREFERENCES: tuple[str, ...] = tuple(PREFERENCE_DEFAULTS)

#: Automatic history fields. These are derived from successful work and must never
#: overwrite a user's explicit preference.
AUTOMATIC_PREFERENCE_FIELDS: tuple[str, ...] = ("recent_supplier_ids", "recent_queries")

RECENT_SUPPLIER_LIMIT = 5
RECENT_QUERY_LIMIT = 10


def load_runtime_rules(path: Path | None = None) -> str:
    """The procurement assistant's runtime rules."""
    resolved = path or AGENTS_MD_PATH
    return resolved.read_text(encoding="utf-8").strip()


def effective_preferences(preferences: Mapping[str, Any] | None) -> dict[str, Any]:
    """Merge stored preferences over the contract defaults.

    Only user-settable keys and the two automatic history fields are carried through, so a
    stray key in the store cannot become an instruction the model reads.
    """
    resolved = dict(PREFERENCE_DEFAULTS)
    if not preferences:
        return resolved
    for key in USER_SETTABLE_PREFERENCES:
        value = preferences.get(key)
        if value:
            resolved[key] = value
    for key in AUTOMATIC_PREFERENCE_FIELDS:
        value = preferences.get(key)
        if value:
            resolved[key] = list(value) if isinstance(value, Sequence) else value
    return resolved


def render_preferences(preferences: Mapping[str, Any] | None) -> str:
    """Render preferences as a compact, unambiguous block."""
    resolved = effective_preferences(preferences)
    lines = ["## 用户偏好（由用户在对话中明确设定）"]
    for key in USER_SETTABLE_PREFERENCES:
        lines.append(f"- {key}: {resolved[key]}")
    recent_suppliers = resolved.get("recent_supplier_ids")
    if recent_suppliers:
        lines.append(f"- recent_supplier_ids: {', '.join(str(item) for item in recent_suppliers)}")
    recent_queries = resolved.get("recent_queries")
    if recent_queries:
        lines.append(f"- recent_queries: {'; '.join(str(item) for item in recent_queries)}")
    return "\n".join(lines)


def render_skills(metadata: Sequence[Mapping[str, str]]) -> str:
    """The progressive-disclosure block: names, descriptions and paths only.

    Bodies are deliberately absent. A skill's body is read on demand, which is what keeps
    the prompt from growing with the number of installed skills.
    """
    if not metadata:
        return "## 可用技能\n\n（可用目录由本次运行的技能扫描注入；需要时 read_file 读取正文。）"
    lines = ["## 可用技能", "", "需要时用 read_file 读取对应正文，不要凭技能名猜测用法。", ""]
    for entry in metadata:
        lines.append(f"- **{entry['name']}** `{entry['path']}`")
        lines.append(f"  {entry['description']}")
    return "\n".join(lines)


#: The main agent's job description.
#:
#: Written as prohibitions rather than suggestions because a first cut phrased these as
#: preferences and a real-model smoke test showed the model ignoring all of them: it called
#: the ERP tools directly instead of delegating, and never recorded a plan. The tool set now
#: makes that impossible (the main agent holds no ERP tool), and this text explains why so
#: the model does not waste turns trying.
DELEGATION_RULES = """## 你的职责

你是采购主管：**规划、分派、汇总**。你自己不查数据。

1. **先规划。** 收到请求后第一步就用 `write_todos` 写下步骤清单，之后每完成一步更新状态。
   不要一边做一边想；清单是给用户看你打算怎么做的。
2. **必须委派。** 你没有库存、物料、供应商、订单的查询工具。任何这类数据都要通过 `task`
   委派获取：
   - 库存预警、比价、报告、分析 → `procurement-analyst`（只读）
   - 下单、改单、需要向用户补充信息 → `procurement-order`
   不要试图自己回答领域问题，也不要凭常识补全缺失的数据。
3. **汇总要保真。** 把子代理返回的 `facts` 与 `warnings` 如实带进你的回答；有缺项就说明缺什么。
   不要把它压成一句听起来很确定的结论。
4. **不要代替用户批准。** 用户要求下单或改单时，把它交给 `procurement-order` 就算完成了你的
   部分：审批由系统在那个工具**执行前**自动发起，用户会在界面上看到并决定。
   **在回答里问"要不要下单 / 要不要改"是无效的**——那个问句不触发任何审批，只会让这一轮
   结束、而订单一步没动。也不要写成"我再委派下单流程处理"然后停下：委派就是现在做的事。
   - 信息不全（少了物料、数量或单价）**不是**先回来问用户的理由：委派下去，订单子代理会用
     `request_order_info` 中断并向用户逐项提问。
   - 用户只是在问情况、没有要求下单时，不要主动发起下单，给建议即可。"""


def build_main_prompt(
    *,
    preferences: Mapping[str, Any] | None = None,
    skills: Sequence[Mapping[str, str]] = (),
    rules: str | None = None,
) -> str:
    """The main agent's system prompt.

    The main agent plans and delegates; it is told not to answer domain questions itself,
    because a main agent that answers directly bypasses the read-only boundary that makes
    the analyst trustworthy.
    """
    sections = [
        rules if rules is not None else load_runtime_rules(),
        render_preferences(preferences),
        render_skills(skills),
        DELEGATION_RULES,
    ]
    return "\n\n".join(section for section in sections if section and section.strip())


def build_subagent_context(
    *,
    task: str,
    preferences: Mapping[str, Any] | None = None,
    relevant_facts: Sequence[str] = (),
) -> str:
    """The message a sub-agent receives.

    Deliberately *not* the parent transcript. A sub-agent needs the task, the preferences
    that shape its output, and any facts the parent already established — not the whole
    conversation, which would blur which parts were addressed to it.
    """
    blocks = [f"## 任务\n\n{task.strip()}"]
    if relevant_facts:
        facts = "\n".join(f"- {fact}" for fact in relevant_facts)
        blocks.append(f"## 已确认的事实（可直接引用，不必重新查询）\n\n{facts}")
    blocks.append(render_preferences(preferences))
    blocks.append(
        "## 返回格式\n\n"
        "用约定结构返回：summary、facts、artifact_ids、warnings、next_action。\n"
        "缺少的信息写进 warnings 或把 next_action 指向补充信息请求，不要用空结果伪装成功。"
    )
    return "\n\n".join(blocks)


__all__ = [
    "AGENTS_MD_PATH",
    "AUTOMATIC_PREFERENCE_FIELDS",
    "DELEGATION_RULES",
    "PREFERENCE_DEFAULTS",
    "RECENT_QUERY_LIMIT",
    "RECENT_SUPPLIER_LIMIT",
    "USER_SETTABLE_PREFERENCES",
    "build_main_prompt",
    "build_subagent_context",
    "effective_preferences",
    "load_runtime_rules",
    "render_preferences",
    "render_skills",
]
