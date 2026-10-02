"""The tool that takes a skill from a source to an assignment.

Two things about its shape are worth stating.

**The core is a plain function.** ``publish_skill`` takes a ``repair_hook`` instead of calling
``interrupt`` directly. Interrupting is one way to ask a human to fix a broken script, and it
is the way the agent uses — but it is not the only one, and a core that can only be driven
through a checkpointered graph is a core that can only be tested through one. The hook is the
seam: the tool passes an interrupting hook, the tests pass a fixing one.

**Asking for the scope is a supplement, not a default.** ``target_scope`` missing is answered
by asking which agent the skill is for, never by assuming all of them. A skill published to
every agent is a skill nobody approved for most of them.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import StructuredTool
from langgraph.types import interrupt

from agent.artifacts.service import SCOPE_OWNER_KEY, SCOPE_THREAD_KEY, resolve_scope
from agent.skills.pipeline import (
    PreparedSkill,
    PublishResult,
    SkillPublisher,
    SkillValidationError,
    SmokeFailed,
)
from agent.skills.store import SkillStore

LOGGER = logging.getLogger("rush_harness.tools.assign_skill")

#: The interrupt tag for the scope question. It stays an interrupt because it *is* a question
#: for the user, and the user is the one who can answer it.
SCOPE_INTERRUPT = "skill_scope_supplement"

#: The tag on the payload handed to a caller-supplied repair hook. **Not an interrupt any
#: more**: ``assign_skill`` used to raise a real ``interrupt()`` here and then discard the
#: answer, so the retry re-read the same broken files and every smoke failure was permanent.
#: The name is kept because it is the contract's word for "the smoke test failed and the skill
#: needs fixing", and the acceptance drives the in-call loop with a hook that receives it.
REPAIR_INTERRUPT = "skill_smoke_repair"

#: From contracts/skills-memory.md item 3: the model gets two chances to fix a failing skill.
#: Used by the in-call loop in :func:`publish_skill`, which is what the acceptance drives with
#: an injected hook.
MAX_REPAIRS = 2

#: The same budget seen from the tool's side, where the repair is the model's turn: one initial
#: attempt plus two repairs, counted across calls for one conversation and one slug. Kept
#: derived so the two numbers cannot drift apart.
MAX_ATTEMPTS = MAX_REPAIRS + 1

SOURCE_TYPES: tuple[str, ...] = ("generated", "package")


def _ok(data: Mapping[str, Any]) -> str:
    return json.dumps(
        {"ok": True, "data": dict(data), "error": None, "request_id": None}, ensure_ascii=False
    )


def _fail(code: str, message: str, *, details: Any = None) -> str:
    return json.dumps(
        {
            "ok": False,
            "data": None,
            "error": {"code": code, "message": message, "retryable": False, "details": details},
            "request_id": None,
        },
        ensure_ascii=False,
    )


def _normalise_answer(answer: Any) -> dict[str, Any]:
    """Accept the shapes a supplement arrives in, and refuse to invent the rest."""
    if isinstance(answer, Mapping):
        inner: Any = answer.get("supplement", answer)
    else:
        inner = answer
    if isinstance(inner, Mapping):
        return dict(inner)
    if isinstance(inner, (str, bytes)):
        text = inner.decode("utf-8") if isinstance(inner, bytes) else inner
        stripped = text.strip()
        if stripped.startswith("{"):
            try:
                parsed = json.loads(stripped)
            except json.JSONDecodeError:
                return {}
            if isinstance(parsed, Mapping):
                return dict(parsed)
        # A plain word is a plausible answer to "which scope"; anything else is prose and is
        # not parsed into fields.
        if stripped and " " not in stripped:
            return {"target_scope": stripped}
    return {}


def publish_skill(
    *,
    publisher: SkillPublisher,
    owner_user_id: str,
    source_type: str,
    source: str,
    slug: str,
    target_scope: str | None,
    repair_hook: Callable[[dict[str, Any]], None] | None = None,
    artifacts: Any | None = None,
    thread_id: str = "",
) -> dict[str, Any]:
    """The whole flow, returning an envelope body. Raises nothing a caller has to catch.

    Order matters and is not negotiable: scope, source, validation, smoke, persist, assign.
    A failure at any step returns before the next one starts, so there is no path that
    assigns a version that did not pass the step before it.
    """
    try:
        scope = publisher.check_scope(target_scope)
    except SkillValidationError as failure:
        return {"ok": False, "code": failure.code, "message": str(failure)}

    if source_type not in SOURCE_TYPES:
        return {
            "ok": False,
            "code": "SOURCE_TYPE_UNKNOWN",
            "message": f"source_type 只能是 {'、'.join(SOURCE_TYPES)}，收到 {source_type!r}",
        }

    repairs: list[dict[str, Any]] = []
    prepared: PreparedSkill | None = None

    for repair in range(MAX_REPAIRS + 1):
        try:
            prepared = publisher.prepare(source_type=source_type, source=source, slug=slug)
            break
        except SmokeFailed as failure:
            repairs.extend(failure.records)
            if repair >= MAX_REPAIRS:
                return _refuse_after_repairs(
                    slug=slug, scope=scope, repairs=repairs, artifacts=artifacts,
                    owner_user_id=owner_user_id, thread_id=thread_id,
                )
            if repair_hook is None:
                # No way to ask anybody; refusing is the only honest outcome.
                return _refuse_after_repairs(
                    slug=slug, scope=scope, repairs=repairs, artifacts=artifacts,
                    owner_user_id=owner_user_id, thread_id=thread_id,
                )
            LOGGER.info("skill %s failed smoke (attempt %d); asking for a repair", slug, repair + 1)
            repair_hook(
                {
                    "interrupt_type": REPAIR_INTERRUPT,
                    "slug": slug,
                    "attempt": repair + 1,
                    "attempts_allowed": MAX_REPAIRS,
                    "stage": publisher._staging_root + f"/{slug}",  # noqa: SLF001 - reported to a human
                    "failure": failure.records,
                    "instructions": (
                        "上面的冒烟测试没有通过。请修改 staging 目录里的技能文件后重试；"
                        f"还有 {MAX_REPAIRS - repair} 次修复机会，仍失败则不会发布。"
                    ),
                }
            )
        except SkillValidationError as failure:
            return {"ok": False, "code": failure.code, "message": str(failure)}

    assert prepared is not None  # the loop either breaks with one or returns

    try:
        result = publisher.complete(prepared, owner_user_id=owner_user_id, scope=scope)
    except SkillValidationError as failure:
        return {
            "ok": False,
            "code": failure.code,
            "message": str(failure),
            "details": {"smoke": prepared.smoke},
        }

    prepared.repairs = len(repairs)
    from agent.middlewares.user_skills_restore import USER_SKILLS_ROOT

    artifact_id = _record_validation(
        artifacts=artifacts,
        owner_user_id=owner_user_id,
        thread_id=thread_id,
        slug=slug,
        scope=scope,
        version=result.version,
        smoke=prepared.smoke,
        repairs=repairs,
        refused=False,
    )

    return {
        "ok": True,
        "skill": {
            "skill_id": f"{owner_user_id}:{scope}:{slug}",
            "slug": slug,
            "scope": scope,
            "version": result.version,
            "status": result.status,
            "reused": result.reused,
            "content_sha256": result.content_sha256,
            "validation_artifact_id": artifact_id,
            "sandbox_directory": f"{USER_SKILLS_ROOT}/{scope}/{slug}",
            "smoke": prepared.smoke,
            "repairs": len(repairs),
            "reason": result.reason,
        },
    }


def _refuse_after_repairs(
    *,
    slug: str,
    scope: str,
    repairs: list[dict[str, Any]],
    artifacts: Any | None,
    owner_user_id: str,
    thread_id: str,
) -> dict[str, Any]:
    """Smoke kept failing. Nothing is published, and the attempts are kept as evidence."""
    artifact_id = _record_validation(
        artifacts=artifacts,
        owner_user_id=owner_user_id,
        thread_id=thread_id,
        slug=slug,
        scope=scope,
        version="",
        smoke=repairs,
        repairs=repairs,
        refused=True,
    )
    return {
        "ok": False,
        "code": "SMOKE_FAILED",
        "message": (
            f"{slug} 的冒烟测试在 {len(repairs)} 次尝试后仍未通过，**没有发布任何版本**。"
            "下面是每一次尝试的退出码与输出。"
        ),
        "details": {"attempts": repairs, "validation_artifact_id": artifact_id},
    }


def _record_validation(
    *,
    artifacts: Any | None,
    owner_user_id: str,
    thread_id: str,
    slug: str,
    scope: str,
    version: str,
    smoke: Sequence[Mapping[str, Any]],
    repairs: Sequence[Mapping[str, Any]],
    refused: bool,
) -> str | None:
    """Store the validation record as a downloadable artifact, when one is available.

    This is the ``validation_artifact_id`` the contract asks the tool to return, and the
    reason it is a file rather than a log line: "the smoke test passed" is a claim, and a
    claim has to be checkable after the fact.
    """
    if artifacts is None or not owner_user_id:
        return None
    payload = json.dumps(
        {
            "slug": slug,
            "scope": scope,
            "version": version,
            "outcome": "refused" if refused else "passed",
            "smoke": list(smoke),
            "repairs": len(repairs),
        },
        ensure_ascii=False,
        indent=2,
    ).encode("utf-8")
    try:
        record = artifacts.register(
            owner_user_id=owner_user_id,
            thread_id=thread_id or "skill-publish",
            name=f"{slug}-validation.json",
            content=payload,
            mime="application/json",
            source=f"skill:{scope}/{slug}",
        )
    except ValueError as failure:  # pragma: no cover - oversized/empty record
        LOGGER.warning("could not store validation record for %s: %s", slug, failure)
        return None
    return record.artifact_id


ASSIGN_SKILL_DESCRIPTION = (
    "把一个技能包发布并分配给某个 Agent。参数：source_type（generated=沙箱内生成的目录，"
    "package=资源站上已批准的 ZIP 地址）、source、slug、target_scope"
    "（main / procurement-analyst / procurement-order）。"
    "流程是校验 → 在沙箱内真实冒烟（示例输入必须成功、坏输入必须失败）→ 写入 Store 并读回校验 → "
    "条件更新发布指针。任何一步失败都不会发布。"
    "未指定 target_scope 时会向用户询问，**不会默认对所有 Agent 发布**。"
    "**在沙箱里写技能文件不算创建技能。** 那些文件不会被校验、不会被持久化、不会分配给任何 "
    "Agent，会话结束后也不存在——用户要的技能是「可用」，不是「有一堆文件」。"
    "要让技能真的存在，**必须调用本工具**，generated 的 source 就是那份目录的路径；"
    "工具会在沙箱里跑它的示例输入与坏输入，所以目录里要有 SKILL.md、脚本和示例。"
    "用户说「创建一个技能」时的完整动作是：先在沙箱里把技能写好，再调用本工具发布并分配。"
    "**冒烟失败不会中断对话**：退出码与输出会写在本次返回的 error.details 里，你按它改完"
    "文件**再调一次本工具**即可；同一个 slug 在一个会话里最多试 3 次（初次 + 2 次修复），"
    "之后不再自动重试。没改文件就重试不会得到不同结果——冒烟跑的是同一份文件。"
)


def build_assign_skill_tool(
    *,
    publisher: SkillPublisher,
    artifacts: Any | None = None,
    store: SkillStore | None = None,
) -> StructuredTool:
    """The tool, shaped for the framework. Credentials and stores are captured, not arguments."""

    def assign_skill(
        source_type: str,
        source: str,
        slug: str,
        target_scope: str | None = None,
        config: RunnableConfig = None,  # type: ignore[assignment]
    ) -> str:
        scope = resolve_scope(config)
        if scope is None:
            return _fail(
                "SCOPE_MISSING",
                f"这次运行没有携带 {SCOPE_OWNER_KEY!r}/{SCOPE_THREAD_KEY!r}，无法确定技能归属",
            )
        owner_user_id, thread_id = scope

        # Ask before assuming. The allowed values travel with the question so the answer can
        # be checked rather than merely accepted.
        if not target_scope:
            answer = interrupt(
                {
                    "interrupt_type": SCOPE_INTERRUPT,
                    "missing_fields": ["target_scope"],
                    "known_values": {
                        "source_type": source_type,
                        "source": source,
                        "slug": slug,
                    },
                    "allowed_values": list(publisher.scopes),
                    "question": f"技能 {slug} 要分配给哪个 Agent 使用？",
                    "instructions": (
                        "请选择一个 scope；未指定时不会默认对所有 Agent 发布。"
                        f"可选：{'、'.join(publisher.scopes)}"
                    ),
                }
            )
            supplied = _normalise_answer(answer)
            target_scope = str(supplied.get("target_scope") or "").strip() or None

        # The smoke budget is spent *across tool calls*, so it is read before this attempt and
        # written after a failure. The repair is the model's turn now — it edits the files and
        # calls again — and a counter local to one call would reset every time and bound
        # nothing, which is why it lives in the store rather than in this function.
        if store is not None:
            spent = store.smoke_failures(owner_user_id, thread_id, slug)
            if spent >= MAX_ATTEMPTS:
                return _fail(
                    "TOO_MANY_ATTEMPTS",
                    f"{slug} 的冒烟测试本会话已经失败 {spent} 次，不再自动重试。"
                    "请先在沙箱里把技能跑通（示例输入退出码 0、坏输入非 0）再开始发布。",
                    details={"attempts": spent, "allowed": MAX_ATTEMPTS, "source": source},
                )

        result = publish_skill(
            publisher=publisher,
            owner_user_id=owner_user_id,
            source_type=source_type,
            source=source,
            slug=slug,
            target_scope=target_scope,
            # No repair hook, on purpose. This used to pass ``interrupt``, which asked **the
            # user** to "modify the files in the staging directory" and then discarded the
            # answer: the retry re-read the same broken files, so every smoke failure was
            # permanent and no skill that failed once could ever be published. The contract
            # says the error goes back to the *model* for repair, and this is what that looks
            # like — the failure is in this tool's answer, and the model is the one that can
            # edit the files and call again.
            repair_hook=None,
            artifacts=artifacts,
            thread_id=thread_id,
        )

        if result["ok"]:
            if store is not None:
                store.clear_smoke_failures(owner_user_id, thread_id, slug)
            return _ok(result["skill"])

        if result["code"] == "SMOKE_FAILED" and store is not None:
            spent = store.note_smoke_failure(owner_user_id, thread_id, slug)
            remaining = max(0, MAX_ATTEMPTS - spent)
            return _fail(
                result["code"],
                f"{result.get('message', '')}\n\n"
                f"请修改沙箱里 {source} 这份技能目录后**再次调用本工具**；"
                f"本会话还剩 {remaining} 次机会。修改之前重试不会得到不同结果——"
                "冒烟会把同一份文件重跑一遍。",
                details={
                    "attempts": spent,
                    "remaining": remaining,
                    "source": source,
                    "smoke": (result.get("details") or {}).get("attempts"),
                },
            )
        return _fail(
            result["code"],
            result["message"],
            details=result.get("details") or {"allowed_scopes": list(publisher.scopes)},
        )

    return StructuredTool.from_function(
        func=assign_skill,
        name="assign_skill",
        description=ASSIGN_SKILL_DESCRIPTION,
    )


__all__ = [
    "ASSIGN_SKILL_DESCRIPTION",
    "MAX_ATTEMPTS",
    "MAX_REPAIRS",
    "REPAIR_INTERRUPT",
    "SCOPE_INTERRUPT",
    "SOURCE_TYPES",
    "build_assign_skill_tool",
    "publish_skill",
]
