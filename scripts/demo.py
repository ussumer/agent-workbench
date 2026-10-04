"""Run the fixed demo scenarios against the real stack, three times each, and keep everything.

What this is for: ``demo.md`` fixes eight cases and requires each to run three times with at
least two succeeding, and the acceptance requires 22 of the 24 trials to succeed. That is a
*measurement*, so it has to be taken the same way every time and the raw attempts have to
survive — "pick the runs that worked and write those up" is the failure mode this file exists
to make impossible.

Three rules the runner keeps:

**Every attempt is recorded, including the failures.** A trial writes its own directory before
anything is asserted, so a crash leaves evidence rather than a gap. The round summary counts
what happened; it does not decide what to keep.

**Decisions are attributed.** The model is real, but nobody is sitting at the browser, so
supplement answers and approvals come from an automated client. Every such action is stamped
with ``scenarios.TEST_ACTOR`` — demo.md requires that a robot click is not reported as a
human one.

**Nothing is asserted about the model's prose.** Each trial reconciles *business facts* —
orders in the ERP, rows in MongoDB, files in the sandbox, artifacts over HTTP — and the
expectations in ``tests/live/scenarios.py`` say which. A fluent answer with no tool call does
not pass, and neither does a correct answer produced without the tools having run.

Usage::

    python scripts/demo.py                 # one round of all eight cases
    python scripts/demo.py --only D01 --trials 1
    python scripts/demo.py --round 2       # a second round, kept separately
"""

from __future__ import annotations

import argparse
import asyncio
import atexit
import base64
import json
import logging
import os
import sys
import time
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
for _path in (str(REPO_ROOT / "src"), str(REPO_ROOT / "tests")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from langchain_core.callbacks import BaseCallbackHandler  # noqa: E402

from live import preflight as live_preflight  # noqa: E402
from live import scenarios  # noqa: E402
from live.stack import DEMO_USERS, LiveStack, running_stack  # noqa: E402

LIVE_ROOT = REPO_ROOT / "artifacts" / "live"

LOGGER = logging.getLogger("rush_harness.demo")

#: How many times a single trial may be interrupted before the runner gives up. Generous: a
#: create plus an approval is two, and D07's skill flow asks for a scope on top.
MAX_INTERRUPTS = 6


class TraceRecorder(BaseCallbackHandler):
    """Every tool call, including those inside a delegated sub-agent.

    The SSE stream cannot carry these: a sub-agent is invoked with ``await
    subagent.ainvoke(...)``, so nothing of what it does is streamed to the parent. Callbacks do
    cross that boundary, and without them a ``task`` call would be all the evidence there is
    that the ERP was ever read.
    """

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self._open: dict[str, dict[str, Any]] = {}

    def on_tool_start(self, serialized: dict, input_str: str, **kwargs: Any) -> None:  # noqa: ANN401
        name = (serialized or {}).get("name") or kwargs.get("name") or "?"
        call: dict[str, Any] = {"tool": name, "arguments": _parse(input_str)}
        self.calls.append(call)
        run_id = str(kwargs.get("run_id") or "")
        if run_id:
            self._open[run_id] = call

    def on_tool_end(self, output: Any, **kwargs: Any) -> None:  # noqa: ANN401
        """What the tool answered.

        Kept because a *refused write* and a *successful write* look identical from the
        outside: both start the tool. When the ERP is unchanged after an approval, the reason
        is in this text and nowhere else — a refusal is a ToolMessage, not an exception, so it
        never reaches the stream, and for a sub-agent's tool it never reaches the SSE stream
        at all.
        """
        call = self._open.pop(str(kwargs.get("run_id") or ""), None)
        if call is None:
            return
        content = getattr(output, "content", output)
        call["result"] = _clip_text(str(content))

    def names(self) -> list[str]:
        return [call["tool"] for call in self.calls]


def read_frames(text: str) -> list[dict[str, Any]]:
    """Re-frame an SSE body by hand, the way ``frontend/src/api/sse.ts`` does.

    Deliberately not an SSE client library: the bytes on the wire are what the browser gets,
    and re-framing them here is the same check ``tests/acceptance/test_t14.py`` makes. A frame
    keeps its ``id`` because that is what a reconnecting client would resume from.
    """
    frames: list[dict[str, Any]] = []
    event = ""
    frame_id = ""
    data: list[str] = []

    def flush() -> None:
        if data:
            frames.append(
                {"event": event or "message", "id": frame_id, "envelope": json.loads("\n".join(data))}
            )
            data.clear()

    for raw in text.splitlines():
        if raw == "":
            flush()
            event, frame_id = "", ""
            continue
        if raw.startswith(":"):
            continue
        field, _, value = raw.partition(":")
        value = value[1:] if value.startswith(" ") else value
        if field == "event":
            event = value
        elif field == "id":
            frame_id = value
        elif field == "data":
            data.append(value)
    flush()
    return frames


def trace_of(frames: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Every tool call with its arguments, rebuilt from the stream itself.

    The arguments have to come from here rather than from the graph's messages: a delegated
    sub-agent's calls happen *inside* the ``task`` tool and never appear in the parent's state,
    and when a case fails the question is always why the model chose something — for a
    delegation that answer is only in the text the main agent wrote for the sub-agent.

    ``tool_args`` arrives as one-character fragments (the contract's own notes record
    ``["{", '"', "a", ...]`` for ``{"a": 17}``), so they are concatenated per call id and
    parsed only at the end — the same reassembly the browser's client performs.
    """
    calls: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    fragments: dict[str, list[str]] = {}

    for frame in frames:
        envelope = frame.get("envelope") or {}
        payload = envelope.get("payload") or {}
        call_id = str(envelope.get("tool_call_id") or "")
        if not call_id:
            continue
        if frame.get("event") == "tool_start":
            call = calls.setdefault(
                call_id,
                {"tool": payload.get("name") or "?", "source": envelope.get("source"), "arguments": None},
            )
            if call["tool"] == "?" and payload.get("name"):
                call["tool"] = payload["name"]
            if call_id not in order:
                order.append(call_id)
        elif frame.get("event") == "tool_args":
            fragments.setdefault(call_id, []).append(str(payload.get("delta") or ""))
        elif frame.get("event") == "tool_end":
            calls.setdefault(call_id, {"tool": "?", "source": envelope.get("source")})
            calls[call_id]["status"] = payload.get("status")

    for call_id, parts in fragments.items():
        if call_id in calls:
            calls[call_id]["arguments"] = _parse("".join(parts))
    return [calls[call_id] for call_id in order]


#: Longest string kept verbatim inside a recorded argument. The instruction the main agent
#: writes for a delegate — which is what explains *why* a sub-agent behaved as it did — is a
#: few hundred characters; a `write_file` body is not, and keeping it whole would bury the
#: reasoning in file content. Truncation is marked with the original length so a reader can
#: tell a short argument from a clipped one.
ARGUMENT_TEXT_LIMIT = 2000


def _parse(raw: Any) -> Any:  # noqa: ANN401
    if isinstance(raw, (dict, list)):
        return _clip(raw)
    if isinstance(raw, str):
        try:
            return _clip(json.loads(raw))
        except json.JSONDecodeError:
            return {"raw": _clip_text(raw)}
    return {"raw": _clip_text(str(raw))}


def _clip(value: Any) -> Any:  # noqa: ANN401
    if isinstance(value, str):
        return _clip_text(value)
    if isinstance(value, dict):
        return {str(key): _clip(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_clip(item) for item in value]
    return value


def _clip_text(text: str) -> str:
    if len(text) <= ARGUMENT_TEXT_LIMIT:
        return text
    return f"{text[:ARGUMENT_TEXT_LIMIT]}<…truncated {len(text)} chars>"


@dataclass
class TrialResult:
    scenario: str
    trial: int
    owner: str
    thread_id: str
    started_at: str
    finished_at: str = ""
    ok: bool = False
    failures: list[str] = field(default_factory=list)
    tool_calls: list[str] = field(default_factory=list)
    #: Every tool call with its arguments, main agent and sub-agent alike. The names alone say
    #: *what* ran; when a case fails the question is always *why the model chose that*, and for
    #: a delegation that answer is only in the text the main agent wrote for the sub-agent.
    trace: list[dict[str, Any]] = field(default_factory=list)
    #: Tool names as they appeared on the SSE wire. A strict subset of ``tool_calls`` by
    #: construction — sub-agent calls are invoked with ``ainvoke`` and never streamed — and
    #: kept so that difference is evidence rather than folklore.
    stream_tools: list[str] = field(default_factory=list)
    #: How many interrupts ``/state`` reported at each check. One is the expected shape; more
    #: than one means the run is parked on several at once, and a bare ``Command(resume=...)``
    #: answers only the first — leaving the graph parked and looking like "approving changed
    #: nothing". Recorded because that reasoning is invisible without the counts.
    pending_counts: list[int] = field(default_factory=list)
    todos: int = 0
    answer: str = ""
    #: The status of the last run in this trial. Recorded because "the answer looked right" and
    #: "the run said it completed" are different claims, and only one of them is about the
    #: system's own state.
    status: str = ""
    #: Every run's status, in order. Kept as a list because the last one being fine says
    #: nothing about the earlier ones.
    statuses: list[str] = field(default_factory=list)
    interrupts: list[dict[str, Any]] = field(default_factory=list)
    decisions: list[dict[str, Any]] = field(default_factory=list)
    facts: dict[str, Any] = field(default_factory=dict)
    #: The raw SSE frames. Written beside the trial rather than inside it — a run emits one per
    #: token, so inlining them would bury the summary the summary is for.
    events: list[dict[str, Any]] = field(default_factory=list)
    #: Contested approvals: every attempt on one interrupt, with the status it got back.
    races: list[dict[str, Any]] = field(default_factory=list)
    #: Background-task records: what was launched, what it reports, what cancelling did.
    tasks: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "scenario": self.scenario,
            "trial": self.trial,
            "owner": self.owner,
            "thread_id": self.thread_id,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "ok": self.ok,
            "failures": self.failures,
            "tool_calls": self.tool_calls,
            "trace": self.trace,
            "stream_tools": self.stream_tools,
            "pending_counts": self.pending_counts,
            "races": self.races,
            "tasks": self.tasks,
            "todos": self.todos,
            "answer": self.answer,
            "status": self.status,
            "statuses": self.statuses,
            "event_count": len(self.events),
            "interrupts": self.interrupts,
            "decisions": self.decisions,
            "facts": self.facts,
        }


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _reset_owner(stack: LiveStack, owner: str) -> None:
    """Clear one owner's state so a trial starts where the last one did.

    ``demo.md`` requires the 24 trials to be independent, and two of the cases depend on that
    literally: D06 says the user starts with no preference and D07 says there is no skill of
    that name yet. Without a reset, trial 2 would begin with trial 1's leftovers and both
    cases would pass for the wrong reason.

    Only this owner's rows are touched. The ERP is deliberately *not* reset — restarting Java
    twenty-four times would dominate the run — so reconciliation compares the facts a trial
    produced against a snapshot taken before it, rather than against absolute state.
    """
    from agent.persistence.indexes import APPLICATION_COLLECTIONS

    for collection in APPLICATION_COLLECTIONS:
        document = {"owner_user_id": owner}
        if collection in {"threads"}:
            document = {"owner_user_id": owner}
        stack.database[collection].delete_many(document)

    # The Store's user prefix, listed and removed key by key: the Store has no bulk delete.
    prefix = f"users/{owner}"
    offset = 0
    while True:
        page = stack.store.search(("skills",), limit=200, offset=offset)
        if not page:
            break
        removed = 0
        for item in page:
            key = str(item.key)
            if key.lstrip("/").startswith(prefix):
                stack.store.delete(("skills",), key)
                removed += 1
        if len(page) < 200:
            break
        offset += 200 - removed

    # The execution copies go too, but the *container* does not: the graphs for both owners
    # were built before the round and hold a proxy to it. Recycling here would hand the first
    # tool call a container that had just been destroyed — which is exactly how this failed
    # the first time, as a connection reset in the middle of a sandbox command.
    backend = stack.manager.get(owner)
    if backend is not None:
        backend.execute("rm -rf /skills/users")
        # What a *trial* leaves behind, which is not the same as what the container must keep.
        # ``/workspace/rules`` is written by the middleware and read by the prompt, so it stays;
        # every top-level entry a case produces — reports, scratch, staged skill packages,
        # charts — is removed.
        #
        # This is not tidiness. Without it the trials are not the independent runs ``demo.md``
        # requires: D07 #1 and #2 started on a sandbox still holding D05's report, whose own
        # text reads 「报价抓取全部失败，无金额」, and both runs spent themselves diagnosing
        # that connectivity failure instead of creating the skill they were asked for. The
        # leftovers made a *later* case look like a model that cannot follow instructions.
        backend.execute(
            "cd /workspace 2>/dev/null && for entry in *; do "
            '[ "$entry" = rules ] || rm -rf "$entry"; done; true'
        )


def _prepare(stack: LiveStack, scenario: scenarios.Scenario) -> dict[str, Any]:
    """The fixtures a case needs before it starts, so it does not depend on another case.

    ``D03`` needs an order to exist: it says "把刚才订单数量改为60件" and, in an automated
    round, there is no "刚才". The order is created here and the agent is expected to find it —
    which is the same thing it would have to do in a conversation where the order came from
    earlier.

    ``D07`` needs the fixed sample its second turn refers to. A *fixed* sample is not something
    the agent can invent: the number the case promises — 1533.00 — is determined by the
    fixture's input (42×24.00 + 30×17.50), so if nobody puts it in the sandbox the second turn
    has nothing to verify against. Three trials before this was noticed produced
    "创建并分配，从未验证" and were graded as passes, because the reconciliation only asked
    whether an assignment existed.
    """
    prepared: dict[str, Any] = {}
    if scenario.id == "D03":
        with _erp_http(stack, owner=DEMO_USERS[0]) as http:
            response = http.post(
                "/api/erp/v1/orders",
                json={
                    "supplier_id": "S001",
                    "currency": "CNY",
                    "lines": [{"part_id": "P001", "quantity": 50, "unit_price": "25.50"}],
                },
                # The ERP's idempotency key is `X-Operation-Id`, and the ledger is what makes
                # a retried create return the same order rather than a second one.
                headers={"X-Operation-Id": f"demo-prep-{uuid.uuid4().hex[:10]}"},
            )
        if response.status_code < 300:
            prepared["order_id"] = (response.json().get("data") or {}).get("order_id")
        else:
            prepared["setup_error"] = f"HTTP {response.status_code}: {response.text[:200]}"
    elif scenario.id == "D07":
        sample = REPO_ROOT / "fixtures" / "skills" / "reorder-cost-summary-v1" / "examples" / "input.json"
        backend = stack.manager.get(DEMO_USERS[0])
        if backend is None:
            prepared["setup_error"] = "D07 需要沙箱，但 owner 还没有容器"
        else:
            # Base64 through the shell rather than an upload call: the content is 480 bytes,
            # every sandbox image has `base64`, and this way the staging does not depend on
            # which file API this backend version exposes.
            encoded = base64.b64encode(sample.read_bytes()).decode("ascii")
            written = backend.execute(
                "mkdir -p /workspace/samples && "
                f"printf '%s' '{encoded}' | base64 -d > /workspace/samples/reorder-input.json && "
                "wc -c < /workspace/samples/reorder-input.json"
            )
            if written.exit_code != 0:
                prepared["setup_error"] = f"固定样例写入失败：{written.output[:200]}"
            else:
                prepared["sample"] = "/workspace/samples/reorder-input.json"
    return prepared


def _rebuild_sandbox(stack: LiveStack, owner: str) -> dict[str, Any]:
    """Make the container look like a rebuilt one, the way a case's setup describes.

    D07 says the skill must still work 「重启 API 并重建沙箱后」. Nothing used to do that, so the
    premise was false: the files the model had written minutes earlier were still there, nothing
    had to be restored, and nothing had to be read — and the case's expectation (a trace of
    reading SKILL.md *and* running the script) was judged against a restart that never happened.

    The container is **not** recycled: the graphs were built before the round and hold a proxy
    to it (see :func:`_reset_owner`). What is removed is exactly what a rebuilt container would
    not have — the restored copies and the revision marker they are keyed by. That marker is the
    signal ``UserSkillsRestoreMiddleware`` documents for its own skip check ("a rebuilt container
    has no marker at all, which is exactly the signal that everything must be restored again"),
    so clearing it is the same event from the middleware's point of view, and it also covers the
    "restart the API" half: the in-process generation cache is not enough to skip a restore that
    the container no longer agrees with.
    """
    backend = stack.manager.get(owner)
    if backend is None:
        return {"rebuilt": False, "reason": "owner has no container"}
    cleared = backend.execute(
        "rm -rf /skills/users /skills/.user-skills-revision 2>/dev/null; "
        "ls -a /skills 2>/dev/null | tr '\\n' ' '"
    )
    return {
        "rebuilt": cleared.exit_code == 0,
        "remaining": (cleared.output or "")[:200].strip(),
    }


def _thread_id(scenario_id: str, trial: int) -> str:
    return f"{scenario_id.lower()}-{trial}-{uuid.uuid4().hex[:8]}"


def _post(client: Any, path: str, body: Mapping[str, Any]) -> tuple[int, list[dict[str, Any]]]:
    """One HTTP turn, and the frames it produced.

    A non-2xx is returned rather than raised: an HTTP refusal is a result the trial records,
    and turning it into an exception would file it as a crash instead of as what happened.
    """
    response = client.post(path, json=dict(body))
    if response.status_code >= 400:
        return response.status_code, []
    return response.status_code, read_frames(response.text)


def _race_approvals(
    client: Any, thread_id: str, interrupt_id: str, sequence: int, count: int
) -> dict[str, Any]:
    """Approve one interrupt from several clients at the same moment.

    A thread pool behind a barrier, rather than a loop, because the overlap is the case under
    test. The guarantee itself does not come from the overlap — it comes from the store's
    conditional transition (``expect=PENDING``), which is what makes exactly one attempt win
    even if the server happens to serve them one after the other. Overlapping them is what
    makes the trial a test of that guarantee instead of a test of request ordering.

    Every attempt is returned, winner and loser alike: a race reported as "it worked" without
    the losing status is not evidence that anything was refused.
    """
    import concurrent.futures
    import threading

    barrier = threading.Barrier(count)

    def attempt(index: int) -> dict[str, Any]:
        request_id = f"{thread_id}-race{sequence}-{index}"
        body = {
            "request_id": request_id,
            "interrupt_id": interrupt_id,
            "resume": {"decisions": [{"type": "approve"}]},
        }
        barrier.wait(timeout=30)
        response = client.post(f"/api/chat/{thread_id}/resume", json=body)
        return {
            "request_id": request_id,
            "status": response.status_code,
            "body": response.text[:400],
            "frames": read_frames(response.text) if response.status_code < 400 else [],
        }

    with concurrent.futures.ThreadPoolExecutor(max_workers=count) as pool:
        attempts = list(pool.map(attempt, range(count)))
    return {"interrupt_id": interrupt_id, "attempts": attempts}


def _task_record(response: Any) -> dict[str, Any]:
    """A task API answer, success or refusal, in one shape.

    The launch route answers 503 rather than inventing a task when the Protocol service is
    down, so a refusal is a fact about the system and is kept as one instead of being raised
    as an exception the trial would report as a crash.
    """
    if response.status_code >= 400:
        return {"ok": False, "http_status": response.status_code, "body": response.text[:300]}
    return {"ok": True, "http_status": response.status_code, **(response.json().get("data") or {})}


def _launch_task(client: Any, thread_id: str, instruction: str, request_id: str) -> dict[str, Any]:
    return _task_record(
        client.post(
            "/api/async-tasks",
            json={
                "parent_thread_id": thread_id,
                "instruction": instruction,
                "request_id": request_id,
            },
        )
    )


def _task_status(client: Any, task_id: str) -> dict[str, Any]:
    return _task_record(client.get(f"/api/async-tasks/{task_id}"))


def _cancel_task(client: Any, task_id: str, request_id: str) -> dict[str, Any]:
    return _task_record(
        client.post(f"/api/async-tasks/{task_id}/cancel", json={"request_id": request_id})
    )


def _state(client: Any, thread_id: str) -> dict[str, Any] | None:
    """The thread's own report of where it is parked, or ``None`` if the read failed.

    Read from the API rather than from the graph: the interrupt id here is the one the resume
    route insists on, and a checkpoint read would give a different object with the same
    contents — close enough to look right and wrong in exactly the cases that matter.
    """
    response = client.get(f"/api/chat/{thread_id}/state")
    if response.status_code >= 400:
        return None
    return (response.json().get("data") or {})


@dataclass
class ClientState:
    """What the automated client has spent so far.

    Three independent counters, one per channel, and that is the point: user turns are spent
    only when the script's words are said, supplements only when a form is submitted, and
    decisions only when an approval is clicked. D03 is why they cannot share one — it rejects
    an update, then the user says something new, then approves — and D02 is why a supplement
    is not a turn: the words and the submitted fields are different things.
    """

    next_turn: int = 0
    next_supplement: int = 0
    decision_index: int = 0


def _interrupt_kinds(interrupt: Mapping[str, Any]) -> set[str]:
    """The candidate types the API classified this interrupt into."""
    return {str(item.get("type")) for item in (interrupt.get("candidates") or [])}


def _answer_for(
    interrupt: Mapping[str, Any],
    scenario: scenarios.Scenario,
    state: ClientState,
) -> tuple[dict[str, Any] | None, str | None]:
    """How the automated client answers one interrupt. Returns the resume body and a problem.

    Which layer this is comes from the payload's structure through the API's own classifier
    (:func:`api_view.stream_adapter.describe_interrupt`), which is why this reads
    ``candidates[].type`` rather than the raw interrupt value: the classification is already
    made, in one place, by the code the browser's payload comes from. Matching words here
    would be a second, divergent definition.
    """
    kinds = _interrupt_kinds(interrupt)
    missing = next(
        (
            item.get("missing_fields") or []
            for item in (interrupt.get("candidates") or [])
            if item.get("type") == "supplement"
        ),
        [],
    )

    if "supplement" in kinds:
        if state.next_supplement >= len(scenario.supplements):
            # Answering here would invent the very fields the tool exists to obtain. Declining
            # makes the trial fail with a reason instead of passing on made-up data.
            return (
                {"supplement": "脚本没有准备这项补充，请不要猜测。"},
                f"场景只声明了 {len(scenario.supplements)} 项补充，但出现了第 "
                f"{state.next_supplement + 1} 次补充中断"
                f"（缺 {', '.join(str(f) for f in missing)}）",
            )
        supplement = scenario.supplements[state.next_supplement]
        state.next_supplement += 1
        # A string, per the contract's type for this field. The route forwards it verbatim,
        # so a mapping here would arrive as a Python repr that no parser accepts.
        return {"supplement": supplement.payload()}, None

    if "approval" not in kinds:
        return None, f"无法识别的中断，既不是补充也不是审批：{sorted(kinds)}"

    if state.decision_index >= len(scenario.decisions):
        # The script has run out of decisions but the system still wants one. Approving here
        # would sanitise a defect into a pass, so the trial refuses the write and says why.
        return (
            {"decisions": [{"type": "reject"}]},
            f"场景只声明了 {len(scenario.decisions)} 个决策，但出现了第 "
            f"{state.decision_index + 1} 次审批中断",
        )

    decision = scenario.decisions[state.decision_index]
    state.decision_index += 1
    return {"decisions": [{"type": decision}]}, None


def _say(turn: str, prepared: dict[str, Any]) -> str:
    """Fill a turn's fixture placeholders.

    Only ``{order_id}`` for now, and a missing value is left as the literal placeholder rather
    than silently becoming "None": a turn that reads "订单（{order_id}）" in the evidence is
    visibly unprepared, while one that reads "订单（None）" looks like the model failed.
    """
    if "{order_id}" not in turn:
        return turn
    order_id = prepared.get("order_id")
    return turn.replace("{order_id}", str(order_id)) if order_id else turn


def _drive(
    stack: LiveStack,
    scenario: scenarios.Scenario,
    trial: int,
    thread_id: str,
    *,
    owner: str = DEMO_USERS[0],
    prepared: dict[str, Any] | None = None,
) -> TrialResult:
    """One attempt: say each turn over HTTP, answer whatever interrupts come back, record all.

    This goes through the real routes rather than calling the graph directly, and that is not
    a stylistic choice: **the approval authorization lives in the resume route.** The run that
    the browser drives records a pending action when an approval interrupt appears, writes the
    user's decision against it, and only then injects ``approved_interrupt_id`` into the run
    config that the write gate reads. Driving the graph in-process skipped all three, so the
    write was refused with ``APPROVAL_REQUIRED`` — the gate working correctly on a run that
    genuinely carried no authorization. A demo that bypasses the mechanism it is demonstrating
    proves nothing about it.
    """
    prepared = prepared or {}
    result = TrialResult(
        scenario=scenario.id,
        trial=trial,
        owner=owner,
        thread_id=thread_id,
        started_at=_now(),
    )
    client = stack.client
    client.post("/api/demo/session", json={"user_id": owner})

    recorder = TraceRecorder()
    events: list[dict[str, Any]] = []
    client_state = ClientState()
    pending: Mapping[str, Any] | None = None
    interrupts = 0
    sequence = 0

    # Started before anything is said, so the conversation below happens *while* it runs.
    # That overlap is the case, so the order here is the assertion rather than bookkeeping.
    if scenario.background_instruction:
        # The task API needs a parent conversation, and D08 happens in one that already exists:
        # ``demo.md`` walks D01 to D08 in a single session. The runner says so explicitly
        # rather than letting the first chat turn create it, because the task has to be
        # *already running* when that turn happens — that overlap is the claim under test.
        from agent.persistence.repository import ApplicationRepository

        ApplicationRepository(stack.database).ensure_thread(
            owner_user_id=owner, thread_id=thread_id, title=scenario.title
        )
        result.tasks["launched"] = _launch_task(
            client, thread_id, scenario.background_instruction, f"{thread_id}-bg1"
        )

    while True:
        # Bound before the branches rather than inside them: the race branch sends its own
        # requests and never touches these, so leaving them unset would read as "the request
        # below might use a stale path" to anyone — including the type checker.
        sent = False
        path = ""
        body: dict[str, Any] = {}
        if pending is None:
            if client_state.next_turn >= len(scenario.turns):
                break
            # A case whose premise is "after the API restarts and the sandbox is rebuilt" has to
            # have that happen, or its later turns are judged against a state that never existed.
            if scenario.rebuild_before_turn == client_state.next_turn + 1:
                result.facts["sandbox_rebuild"] = _rebuild_sandbox(stack, owner)
            said = _say(scenario.turns[client_state.next_turn], prepared)
            client_state.next_turn += 1
            sequence += 1
            path = "/api/chat/stream"
            body: dict[str, Any] = {
                "thread_id": thread_id,
                "request_id": f"{thread_id}-t{sequence}",
                "message": said,
            }
        elif scenario.concurrent_approvals and "approval" in _interrupt_kinds(pending):
            # Every declared decision is spent at once, on one interrupt, from separate
            # clients. The trial keeps all the answers, so "one order" is joined in the
            # evidence by "and here is the one that was refused".
            race = _race_approvals(
                client,
                thread_id,
                str(pending.get("interrupt_id")),
                sequence,
                len(scenario.decisions),
            )
            sequence += len(scenario.decisions)
            for item in race["attempts"]:
                events.extend(item.pop("frames", []))
            result.races.append(race)
            result.decisions.append(
                {
                    "actor": scenarios.TEST_ACTOR,
                    "interrupt_type": pending.get("interrupt_type"),
                    "answer": [{"type": "approve"}] * len(race["attempts"]),
                    "concurrent": [item["status"] for item in race["attempts"]],
                }
            )
            client_state.decision_index += len(race["attempts"])
            sent = True
        else:
            interrupts += 1
            if interrupts > MAX_INTERRUPTS:
                result.failures.append(f"超过 {MAX_INTERRUPTS} 次中断仍未结束")
                break
            answer, problem = _answer_for(pending, scenario, client_state)
            if problem:
                result.failures.append(problem)
            result.decisions.append(
                {
                    "actor": scenarios.TEST_ACTOR,
                    "interrupt_type": pending.get("interrupt_type"),
                    "answer": answer,
                }
            )
            if answer is None:
                break
            sequence += 1
            path = f"/api/chat/{thread_id}/resume"
            body = {
                "request_id": f"{thread_id}-r{sequence}",
                "interrupt_id": pending.get("interrupt_id"),
                "resume": answer,
            }

        if not sent:
            # The observer is attached exactly while a request is being served, and cleared
            # after: it is a property of this trial, not of the stack.
            stack.recorder = recorder
            try:
                status_code, frames = _post(client, path, body)
            finally:
                stack.recorder = None
            events.extend(frames)
            if status_code >= 400:
                result.failures.append(f"{path} 返回 HTTP {status_code}")
                break

        # What the thread is parked on now, asked of the same route the browser asks. A turn
        # that produced no interrupt simply ends, and any turn the script still has left is
        # something new the user says — the first version of this loop only advanced on an
        # interrupt, so every multi-turn case silently ran its first turn and stopped.
        snapshot = _state(client, thread_id)
        if snapshot is None:
            result.failures.append("读不到 /state，无法确定这一轮停在哪里")
            break
        result.todos = len(snapshot.get("todos") or [])
        found = snapshot.get("pending_interrupts") or []
        result.pending_counts.append(len(found))
        pending = found[0] if found else None
        if pending is not None:
            result.interrupts.append(pending)

    # Asked of the API after the conversation, not remembered from the launch reply: the point
    # is that the task's state is *queryable*, and a cached copy would answer a different
    # question.
    if scenario.background_instruction:
        task_id = str((result.tasks.get("launched") or {}).get("task_id") or "")
        if task_id:
            result.tasks["status"] = _task_status(client, task_id)

    if scenario.cancels_second_task:
        second = _launch_task(
            client, thread_id, scenario.background_instruction, f"{thread_id}-bg2"
        )
        result.tasks["second"] = second
        second_id = str(second.get("task_id") or "")
        if second_id:
            # Counted around the cancel call: "cancelling starts no new work" is the claim,
            # and a count taken anywhere else would not be about cancelling.
            before = len(recorder.calls)
            result.tasks["cancelled"] = _cancel_task(client, second_id, f"{thread_id}-cancel")
            result.tasks["calls_during_cancel"] = len(recorder.calls) - before

    # Two views of the same run, and the difference between them is a fact worth keeping. The
    # observer sees everything; the stream sees only what the main agent did, because a
    # sub-agent runs under ``ainvoke``. Recording both means "the sub-agent's calls are missing
    # from the wire" is visible in the evidence rather than being an assumption.
    result.trace = list(recorder.calls)
    result.tool_calls = [call["tool"] for call in result.trace]
    result.stream_tools = [call["tool"] for call in trace_of(events)]
    result.statuses = _done_statuses(events)
    result.status = result.statuses[-1] if result.statuses else ""
    # A run that reported ``failed`` is a failure of the trial, whatever the case's own
    # expectations say afterwards. ``interrupted`` is not: a run parked on an approval or a
    # supplement is the normal way a turn ends, and whether that was *correct* is what the
    # decisions and the expectations are for.
    for index, status in enumerate(result.statuses, 1):
        if status == "failed":
            result.failures.append(
                f"第 {index} 次运行以 failed 结束（原因见 events.jsonl 的 error 事件）"
            )
    result.answer = _answer_text(events)
    result.events = events
    result.finished_at = _now()
    return result


def _done_statuses(events: Sequence[Mapping[str, Any]]) -> list[str]:
    """Every run's ``done`` status, in order — one per run, not one per trial.

    Per run, because a multi-turn case runs the graph more than once and a turn that *died*
    followed by turns that went fine would otherwise read as a clean trial. That is not
    hypothetical: D06 #1 and #2 died on ``write_file`` (``NAMESPACEVIOLATION``) in seven
    seconds, and still looked like passes, because the verdict was "were any failures
    recorded?" and a dead run records none — the case's own expectations were satisfied by the
    middleware's separate automatic write. A trial is not a pass if a run inside it failed.
    """
    found: list[str] = []
    for frame in events:
        if frame.get("event") != "done":
            continue
        envelope = frame.get("envelope") or {}
        found.append(str((envelope.get("payload") or {}).get("status") or ""))
    return found


def _answer_text(events: Sequence[Mapping[str, Any]]) -> str:
    """The reply, reassembled from the token stream — what the browser shows the user.

    Read from the wire rather than from the final checkpoint message, because the wire is what
    was actually delivered; the two agreeing is a property worth being able to notice.

    Only the main agent's tokens count. A delegated sub-agent's own text streams with its name
    on ``source``, and folding it in would produce an "answer" the user never saw as one.
    """
    for frame in reversed(list(events)):
        envelope = frame.get("envelope") or {}
        if frame.get("event") == "done":
            content = (envelope.get("payload") or {}).get("content")
            if content:
                return str(content)

    return "".join(
        str((frame.get("envelope") or {}).get("payload", {}).get("text") or "")
        for frame in events
        if frame.get("event") == "token" and (frame.get("envelope") or {}).get("source") == "main"
    )


def _snapshot(stack: LiveStack, scenario: scenarios.Scenario, thread_id: str) -> dict[str, Any]:
    """What the world looked like before the trial, so reconciliation can use the delta.

    Needed because the ERP is shared across the round: D03 asks for an order to have become
    version 2, and by trial three there are several such orders. Comparing counts and the
    specific order this trial created is what keeps the trials independent without restarting
    Java between them.
    """
    snapshot: dict[str, Any] = {}
    if scenario.id in {"D02", "D03", "D04"}:
        snapshot["order_ids"] = {order["order_id"] for order in _orders(stack)}
    if scenario.id in {"D05", "D06", "D07"}:
        snapshot["artifact_ids"] = {
            item["artifact_id"] for item in _artifacts(stack, DEMO_USERS[0], thread_id)
        }
    if scenario.id == "D08":
        from agent.async_tasks.store import MongoAsyncTaskStore

        snapshot["task_ids"] = {
            item.task_id
            for item in MongoAsyncTaskStore(stack.database).list_for_parent(
                DEMO_USERS[0], thread_id
            )
        }
    return snapshot


async def _reconcile(  # noqa: PLR0912 - one branch per case, which is the point
    stack: LiveStack,
    scenario: scenarios.Scenario,
    result: TrialResult,
    *,
    before: dict[str, Any] | None = None,
    prepared: dict[str, Any] | None = None,
) -> None:
    """Check the business facts the scenario promises.

    Nothing here reads the model's answer: the point of a demo is that the *system* ended up
    in the promised state, and a report that grades itself on its own prose is not evidence.
    """
    from agent.persistence.repository import ApplicationRepository

    repository = ApplicationRepository(stack.database)

    if scenario.id == "D01":
        warnings = await _erp_warnings(stack)
        if warnings is None:
            result.failures.append("ERP 没有返回库存预警")
        else:
            for part_id, expected in scenario.numbers.items():
                actual = warnings.get(part_id)
                if actual != expected:
                    result.failures.append(f"{part_id} 建议补货 {actual} != {expected}")
            result.facts["suggested_quantities"] = warnings
        reads = [name for name in result.tool_calls if "inventory" in name or "part" in name]
        if not reads:
            result.failures.append("trace 里没有 ERP 读调用")

    elif scenario.id == "D02":
        added = [
            order
            for order in _orders(stack)
            if order["order_id"] not in (before or {}).get("order_ids", set())
        ]
        if len(added) != 1:
            result.failures.append(f"这一轮应当恰好新增 1 张订单，实际 {len(added)}")
        elif added[0]["total_amount"] != "1275.00":
            result.failures.append(f"新增订单总额 {added[0]['total_amount']} != 1275.00")
        result.facts["orders_added"] = [order["total_amount"] for order in added]
        if not result.interrupts:
            result.failures.append("没有出现补充或审批中断")

    elif scenario.id == "D03":
        wanted = (prepared or {}).get("order_id")
        if (prepared or {}).get("setup_error"):
            result.failures.append(f"夹具准备失败：{prepared['setup_error']}")
        orders = {order["order_id"]: order for order in _orders(stack)}
        target = orders.get(str(wanted)) if wanted else None
        if target is None:
            result.failures.append(f"找不到夹具订单 {wanted!r}；实际 {sorted(orders)[:5]}")
        else:
            if target.get("version", 1) < 2:
                result.failures.append(f"夹具订单仍是 version={target.get('version')}")
            if target["total_amount"] != "1530.00":
                result.failures.append(f"改单后总额 {target['total_amount']} != 1530.00")
            result.facts["order"] = {
                "order_id": target["order_id"],
                "version": target.get("version"),
                "total_amount": target["total_amount"],
                "lines": target.get("lines"),
            }

    elif scenario.id == "D04":
        added = [
            order
            for order in _orders(stack)
            if order["order_id"] not in (before or {}).get("order_ids", set())
        ]
        if len(added) != 1:
            result.failures.append(f"两个同时的批准应当只产生 1 张订单，实际 {len(added)}")
        elif added[0]["total_amount"] != "1275.00":
            result.failures.append(f"新增订单总额 {added[0]['total_amount']} != 1275.00")
        result.facts["orders_added"] = [order["order_id"] for order in added]

        races = result.races or []
        statuses = [item["status"] for race in races for item in race["attempts"]]
        result.facts["race_statuses"] = statuses
        if len(statuses) < 2:
            result.failures.append(f"同一审批只被尝试了 {len(statuses)} 次，没有形成竞争")
        elif sum(1 for status in statuses if status < 300) != 1:
            # Not "at least one": two successes would mean two writes were authorised off one
            # approval, which is the failure this case exists to catch.
            result.failures.append(f"并发批准应当只有一次成功，实际状态码 {statuses}")
        elif not any(status == 409 for status in statuses):
            result.failures.append(f"落后的那次批准应当被拒为 409，实际 {statuses}")

        if races:
            from agent.approval.store import MongoPendingActionStore
            from agent.persistence.indexes import COLLECTION_PENDING_ACTIONS

            interrupt_id = str(races[0]["interrupt_id"])
            action = MongoPendingActionStore(stack.database).find(
                result.owner, result.thread_id, interrupt_id
            )
            if action is None:
                result.failures.append("找不到这次审批的账本记录")
            else:
                result.facts["action"] = {
                    "status": str(action.status),
                    "operation_id": action.operation_id,
                }
            # Counted from the collection rather than from the index: "one row per interrupt"
            # is the claim, so the claim is checked where rows live.
            rows = list(
                stack.database[COLLECTION_PENDING_ACTIONS].find({"interrupt_id": interrupt_id})
            )
            if len(rows) != 1:
                result.failures.append(f"同一审批在账本里应当只有 1 条记录，实际 {len(rows)}")

    elif scenario.id == "D05":
        added = [
            item
            for item in _artifacts(stack, result.owner, result.thread_id)
            if item["artifact_id"] not in (before or {}).get("artifact_ids", set())
        ]
        if not added:
            result.failures.append("没有产出任何新产件")
        result.facts["artifacts"] = [
            {"name": item["name"], "size": item["size"]} for item in added
        ]
        if not any(
            "inventory" in name or "price" in name or "supplier" in name or "part" in name
            for name in result.tool_calls
        ):
            result.failures.append("trace 里没有比价相关调用")

        # What the case actually promises: three recommendations and a total. Until this was
        # added, a trial passed on "some artifact exists" alone — and three did, while the
        # report they produced said 「报价抓取全部失败，无金额」 and the chart was titled
        # 「无报价，无法计算补货成本」. The session had not fetched a single quote.
        report = _artifact_text(stack, result.owner, added)
        result.facts["report_chars"] = len(report)
        if not report:
            result.failures.append("产件里没有可读文本（报告或 CSV），无法核对推荐与合计")
        else:
            lines = report.splitlines()
            for part_id, supplier_id in scenario.numbers.items():
                if part_id == "total_amount":
                    continue
                if not any(part_id in line and supplier_id in line for line in lines):
                    result.failures.append(
                        f"报告里没有 {part_id}→{supplier_id} 这一行，推荐无法核对"
                    )
            total = scenario.numbers.get("total_amount")
            if total and not _amount_seen(report, total):
                result.failures.append(f"报告里没有合计 {total}")

    elif scenario.id == "D07":
        from agent.skills.store import SkillStore

        store = SkillStore(stack.store, stack.database)
        assigned = store.list_for_owner(result.owner)
        if not assigned:
            result.failures.append("没有已分配的技能")
        result.facts["assignments"] = [
            {"scope": item.scope, "slug": item.slug, "version": item.version} for item in assigned
        ]

        # The case promises far more than an assignment: 「生成、测试、分配、Store 持久化、
        # 恢复、读 SKILL.md、执行脚本都有证据；只重读描述不算使用」. Only the assignment was
        # checked, so a run that created and assigned a skill without ever testing it — the
        # second turn's whole point — was graded a pass. The number is checked against the
        # *tools'* output on purpose: a model that computes nothing and writes the right figure
        # into its prose is what this reconciliation exists to catch.
        trail = _tool_results_text(result)
        for item in assigned:
            scope, slug = str(item.scope), str(item.slug)
            if scope != "procurement-analyst":
                result.failures.append(
                    f"技能被分配给 {scope!r}；用例只要求分配给采购分析专家"
                )
            read_skill = any(
                call["tool"] == "read_file"
                and slug in json.dumps(call.get("arguments"), ensure_ascii=False)
                and "SKILL.md" in json.dumps(call.get("arguments"), ensure_ascii=False)
                for call in result.trace
            )
            ran_script = any(
                call["tool"] == "execute"
                and slug in json.dumps(call.get("arguments"), ensure_ascii=False)
                for call in result.trace
            )
            if not read_skill:
                result.failures.append(f"没有读过 {slug} 的 SKILL.md，不算使用过这个技能")
            if not ran_script:
                result.failures.append(f"没有在沙箱里执行过 {slug} 的脚本，不算使用过这个技能")
            result.facts.setdefault("usage_trail", {})[slug] = {
                "read_skill": read_skill,
                "ran_script": ran_script,
            }

        total = scenario.numbers.get("total_amount")
        if total and not _amount_seen(trail, total):
            result.failures.append(f"工具返回里没有固定样例的合计 {total}")

    elif scenario.id == "D08":
        from agent.async_tasks.store import MongoAsyncTaskStore

        rows = MongoAsyncTaskStore(stack.database).list_for_parent(result.owner, result.thread_id)
        launched = result.tasks.get("launched") or {}
        if not launched.get("ok"):
            result.failures.append(
                f"后台任务未能启动：{launched.get('body') or launched.get('http_status') or launched}"
            )
        else:
            # A task id alone would prove nothing about where the work runs. The Protocol ids
            # are what make "this is a real run on the service" checkable from outside.
            if not launched.get("async_run_id"):
                result.failures.append("后台任务没有对应的 Protocol run id，不是真实运行")

        # The artifacts come from the *queried* record, not from the launch reply: at launch
        # time the run has not started, so it cannot have produced anything, and a check against
        # that reply fails for every task that ever works.
        queried = result.tasks.get("status") or {}
        filed = [str(item) for item in (queried.get("artifact_ids") or [])]
        result.facts["background"] = {
            "task_id": launched.get("task_id"),
            "async_run_id": launched.get("async_run_id"),
            "artifacts": filed,
            "status_at_launch": launched.get("status"),
            "status_when_queried": queried.get("status"),
            "rows_in_store": len(rows),
        }
        if not filed:
            result.failures.append("后台任务没有产物可取回")
        else:
            # "Retrievable" is checked against the owner's own listing rather than taken from
            # the id: an artifact filed under the wrong owner, or one with nothing in it, would
            # satisfy the id and not the promise.
            owned = {
                item.artifact_id: item
                for item in stack.artifacts.list_for_thread(result.owner, result.thread_id)
            }
            for artifact_id in filed:
                item = owned.get(artifact_id)
                if item is None:
                    result.failures.append(f"产物 {artifact_id} 不在该所有者与父会话名下，取不回来")
                elif item.size <= 0:
                    result.failures.append(f"产物 {artifact_id} 是空文件")
        if not result.answer.strip():
            result.failures.append("主对话在后台任务运行期间没有回答")
        if str(launched.get("status") or "") not in {"queued", "running"}:
            result.failures.append(
                f"启动时任务状态是 {launched.get('status')!r}，主对话并非在它运行期间进行的"
            )

        if scenario.cancels_second_task:
            cancelled = result.tasks.get("cancelled") or {}
            state = str(cancelled.get("status") or "")
            result.facts["cancelled_task"] = {
                "task_id": cancelled.get("task_id"),
                "status": state,
            }
            if state != "cancelled":
                result.failures.append(
                    f"被取消的任务状态是 {state or '（没有记录）'}，不是 cancelled"
                )
            if result.tasks.get("calls_during_cancel"):
                result.failures.append(
                    "取消任务期间产生了 "
                    f"{result.tasks['calls_during_cancel']} 次新的工具调用"
                )

    if not result.tool_calls:
        result.failures.append("整轮没有任何工具调用，回答可能是模型编造的")

    del repository  # the repository is only needed by scenarios that are not yet reconciled


async def _erp_warnings(stack: LiveStack) -> dict[str, str] | None:
    """Read the warnings through the gateway, independently of what the agent reported.

    Independent is the point: the reconciliation compares the ERP's answer with the scenario's
    expectation, so it must not reuse anything the model produced.
    """
    from langchain_mcp_adapters.client import MultiServerMCPClient

    client = MultiServerMCPClient(
        {
            "erp": {
                "url": stack.gateway.mcp_url,
                "transport": "streamable_http",
                "headers": {"x-actor-id": DEMO_USERS[0]},
            }
        }
    )
    for tool in await client.get_tools():
        if tool.name == "inventory_warning":
            raw = await tool.ainvoke({"page": 1, "page_size": 20})
            text = raw if isinstance(raw, str) else json.dumps(raw, default=str)
            document = json.loads(_text_of(text))
            items = (document.get("data") or document).get("items") or []
            return {
                str(item["part_id"]): str(item.get("suggested_quantity"))
                for item in items
                if item.get("suggested_quantity") is not None
            }
    return None


def _text_of(raw: str) -> str:
    """MCP answers with content blocks; reassemble them before parsing."""
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return raw
    if isinstance(parsed, list) and parsed and isinstance(parsed[0], dict):
        return str(parsed[0].get("text") or raw)
    if isinstance(parsed, dict) and "text" in parsed:
        return str(parsed["text"])
    return raw


def _erp_http(stack: LiveStack, *, owner: str) -> Any:
    """A client for the ERP, built the way its own fixture builds one.

    The base path (``/api/erp/v1``) and the header set live in ``erp_service``; re-deriving
    them here is how the first version of this file ended up calling ``/orders`` and getting
    an internal error from a route that was never there.
    """
    import httpx

    return httpx.Client(
        base_url=stack.erp.base_url,
        headers=stack.erp.headers(actor=owner),
        timeout=httpx.Timeout(30.0, connect=10.0),
        trust_env=False,
    )


def _orders(stack: LiveStack) -> list[dict[str, Any]]:
    """The orders this project's ERP holds, read from the ERP rather than from the agent."""
    with _erp_http(stack, owner=DEMO_USERS[0]) as http:
        response = http.get("/api/erp/v1/orders", params={"page": 1, "page_size": 50})
    response.raise_for_status()
    return (response.json().get("data") or {}).get("items") or []


def _artifacts(stack: LiveStack, owner: str, thread_id: str) -> list[dict[str, Any]]:
    described = stack.artifacts.list_for_thread(owner, thread_id)
    return [
        {
            "artifact_id": item.artifact_id,
            "name": item.name,
            "size": item.size,
            "sha256": item.sha256,
        }
        for item in described
    ]


def _artifact_text(
    stack: LiveStack, owner: str, records: Sequence[Mapping[str, Any]]
) -> str:
    """The readable text of some artifacts, joined.

    Only the text-ish ones: a PNG carries no numbers a case can be reconciled against, and
    decoding one would produce noise that happens to be short enough to look like an answer.
    """
    chunks: list[str] = []
    for record in records:
        name = str(record.get("name") or "")
        if not name.lower().endswith((".md", ".csv", ".json", ".txt")):
            continue
        opened = stack.artifacts.open(owner, str(record["artifact_id"]))
        if opened is None:
            continue
        _record, content = opened
        chunks.append(content.decode("utf-8", errors="replace"))
    return "\n".join(chunks)


def _tool_results_text(result: TrialResult) -> str:
    """Everything the *system* returned during the run, joined.

    Tool results rather than the answer: the case's numbers have to be produced by the tools,
    and a model that writes the right number into its prose after computing nothing is exactly
    what "a fluent answer with no tool call does not pass" is about.
    """
    chunks: list[str] = []
    for frame in result.events:
        if frame.get("event") != "tool_result":
            continue
        payload = (frame.get("envelope") or {}).get("payload") or {}
        chunks.append(json.dumps(payload, ensure_ascii=False))
    return "\n".join(chunks)


def _amount_seen(text: str, amount: str) -> bool:
    """Whether ``amount`` appears in the text, ignoring thousands separators."""
    return amount in text.replace(",", "")


async def run_round(
    *,
    round_name: str,
    only: tuple[str, ...] = (),
    trials: int = scenarios.TRIALS_PER_SCENARIO,
) -> int:
    selected = [item for item in scenarios.SCENARIOS if not only or item.id in only]
    round_dir = LIVE_ROOT / round_name
    round_dir.mkdir(parents=True, exist_ok=True)
    run_id = _open_round(round_dir, [item.id for item in selected], trials)
    _claim_round(round_dir)

    print(f"round {round_name}: {len(selected)} 个场景 × {trials} 次")
    results: list[TrialResult] = []

    with running_stack() as stack:
        print(f"  stack up: model={stack.model_id} erp={stack.erp.base_url} site={stack.site.base_url}")
        # Before the first turn, not after the last: a round that is not live must not become
        # evidence, and a scripted model would produce trials that read exactly like real ones.
        # Written beside them so a reader can see what "live" was checked against.
        preflight = live_preflight.assert_live(
            model_config=stack.model_config,
            # The ports this stack bound, not the ones the environment names. The fixtures pick
            # free ports, so recording the configured addresses would document a run that did
            # not happen.
            addresses={
                "erp": stack.erp.base_url,
                "mcp": stack.gateway.mcp_url,
                "fixtures": stack.site.base_url,
                "opensandbox": stack.services.opensandbox_base_url,
                "agent_protocol": stack.services.agent_protocol_base_url,
            },
        )
        (round_dir / "preflight.json").write_text(
            json.dumps(preflight.as_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"  preflight: model={preflight.model_id} host={preflight.model_host}")
        stack.connect_owners()
        for scenario in selected:
            for trial in range(1, trials + 1):
                started = time.monotonic()
                owner = DEMO_USERS[0]
                thread_id = _thread_id(scenario.id, trial)
                try:
                    # Independence first: the case starts from the state it documents, not
                    # from whatever the previous trial left behind.
                    _reset_owner(stack, owner)
                    prepared = _prepare(stack, scenario)
                    before = _snapshot(stack, scenario, thread_id)
                    result = _drive(
                        stack, scenario, trial, thread_id, owner=owner, prepared=prepared
                    )
                    await _reconcile(
                        stack, scenario, result, before=before, prepared=prepared
                    )
                except Exception as failure:  # noqa: BLE001 - a crashed trial is a result
                    result = TrialResult(
                        scenario=scenario.id,
                        trial=trial,
                        owner=DEMO_USERS[0],
                        thread_id="<crashed>",
                        started_at=_now(),
                        finished_at=_now(),
                    )
                    result.failures.append(f"{type(failure).__name__}: {failure}")
                result.ok = not result.failures
                results.append(result)
                _write_trial(
                    round_dir,
                    scenario,
                    result,
                    seconds=time.monotonic() - started,
                    run_id=run_id,
                )
                mark = "ok" if result.ok else "FAIL"
                print(f"  {scenario.id} #{trial}: {mark} ({time.monotonic() - started:.0f}s)")
                for failure in result.failures:
                    print(f"      - {failure}")

    summary = _summarise(round_dir, round_name, run_id)
    (round_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["passed_overall"] else 1


def _claim_round(round_dir: Path) -> None:
    """Take this round for this process, or refuse to run beside another one.

    Two processes on one round directory do not merely duplicate work — they **corrupt each
    other's evidence**. Both reset the same owner between trials, both drive the same ERP, and
    both write the same ``<case>-<n>/trial.json``, so a trial can be reconciled against state
    the other process just wiped; and the log, one file with two write offsets, interleaves
    into nonsense. Not hypothetical: it happened twice in this session, and the rounds it
    produced were worthless while *looking* like ordinary rounds.

    ``round.json``'s stamp cannot prevent it, because both processes read the same ``run_id``
    and so agree on which trials belong to the round. The lock has to be exclusive, which is
    what ``O_CREAT|O_EXCL`` is for.
    """
    lock = round_dir / "run.lock"
    try:
        handle = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        holder = ""
        try:
            holder = lock.read_text(encoding="utf-8").strip()
        except OSError:
            pass
        raise SystemExit(
            f"round {round_dir.name} 已在运行中（{holder or '另一个进程'}）："
            "两个进程同时写一个轮次目录会互相污染证据，结果不可信。\n"
            f"等它跑完、或换一个 --round 名字；确认那个进程已经退出后可以删掉 {lock}"
        ) from None

    with os.fdopen(handle, "w", encoding="utf-8") as stream:
        stream.write(f"pid={os.getpid()} started={_now()}\n")
    # Released on exit rather than in a ``finally``, so the body below needs no re-indenting.
    # A hard kill leaves the file behind on purpose: a silent stale lock would be worse than a
    # message that tells the operator which file to look at.
    atexit.register(lambda: lock.unlink(missing_ok=True))


def _open_round(round_dir: Path, case_ids: Sequence[str], trials: int) -> str:
    """Stamp this round and return the id every trial of *this* run carries.

    A round directory is meant to be filled across more than one command — see
    :func:`_summarise` — so the runner cannot simply treat the directory as its own. What it
    can do is say which trials are its own, and that is what this id is for. Without it a
    directory reused from an earlier run has its old trials counted as if they had just been
    run, which is a wrong verdict in *both* directions: a stale failure makes a good round
    look short of its minimum, and a stale success is worse, because nothing about it is true
    any more. Found the hard way — ``probe9`` already existed from the day before, and its
    D03-1 (yesterday's failure) was reported as this run's.

    Kept if already present: a second command resuming the same round must produce trials that
    count together with the first command's.
    """
    stamp_file = round_dir / "round.json"
    if stamp_file.is_file():
        try:
            stamp = json.loads(stamp_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            stamp = {}
        if stamp.get("run_id"):
            return str(stamp["run_id"])

    run_id = uuid.uuid4().hex[:12]
    stamp_file.write_text(
        json.dumps(
            {
                "run_id": run_id,
                "opened_at": _now(),
                "cases": list(case_ids),
                "trials_per_case": trials,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return run_id


def _write_trial(
    round_dir: Path,
    scenario: scenarios.Scenario,
    result: TrialResult,
    *,
    seconds: float,
    run_id: str,
) -> None:
    """Write the attempt before anything is decided about the round.

    The order matters: evidence that only survives when a trial succeeds is not evidence of
    anything, and demo.md says the failed attempts are kept and classified.
    """
    directory = round_dir / f"{scenario.id}-{result.trial}"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "scenario.json").write_text(
        json.dumps(scenario.as_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    payload = {"seconds": round(seconds, 2), "round_run_id": run_id, **result.as_dict()}
    (directory / "trial.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    # The raw stream, one frame per line, as ``demo.md`` asks for. Kept out of trial.json so
    # the summary stays readable, and kept at all because it is the only record of what the
    # browser actually received — the answer text above is reassembled from it.
    if result.events:
        (directory / "events.jsonl").write_text(
            "".join(
                json.dumps(frame, ensure_ascii=False) + "\n" for frame in result.events
            ),
            encoding="utf-8",
        )


def _summarise(round_dir: Path, round_name: str, run_id: str) -> dict[str, Any]:
    """The round as its *directory* shows it, not as this invocation remembers it.

    Read back from the trial files because a round may be run across more than one command:
    D07 and D08 take minutes each and the rest take seconds, so requiring all twenty-four
    trials in one process would mean either a very long command or a summary describing a
    smaller round than the evidence does. It also makes the summary unable to disagree with
    the trials it is a summary of — the directory is the record, and this only counts it.
    """
    per_case: dict[str, dict[str, Any]] = {}
    trials = 0
    successes = 0
    #: Trials in this directory that belong to another run. Named rather than skipped
    #: silently: they are why a round can look different from the one that was just executed,
    #: and a reader who cannot see them cannot tell a reused directory from a broken run.
    ignored: list[str] = []

    for directory in sorted(round_dir.glob("*-*")):
        trial_file = directory / "trial.json"
        if not trial_file.is_file():
            continue
        try:
            payload = json.loads(trial_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            LOGGER.warning("unreadable trial file: %s", trial_file)
            continue
        if str(payload.get("round_run_id") or "") != run_id:
            ignored.append(directory.name)
            continue
        scenario_id = str(payload.get("scenario") or "")
        if not scenario_id:
            continue
        entry = per_case.setdefault(
            scenario_id, {"attempts": 0, "succeeded": 0, "failures": []}
        )
        entry["attempts"] += 1
        trials += 1
        if payload.get("ok"):
            entry["succeeded"] += 1
            successes += 1
        else:
            entry["failures"].extend(str(item) for item in (payload.get("failures") or []))

    met_minimum = {
        scenario_id: entry["succeeded"] >= scenarios.PER_SCENARIO_MINIMUM
        for scenario_id, entry in per_case.items()
    }
    return {
        "round": round_name,
        "trial_total": trials,
        "successes": successes,
        "required_successes": scenarios.REQUIRED_SUCCESSES,
        "per_case": per_case,
        "case_minimum": scenarios.PER_SCENARIO_MINIMUM,
        "cases_short_of_minimum": sorted(
            scenario_id for scenario_id, met in met_minimum.items() if not met
        ),
        "every_case_met_minimum": all(met_minimum.values()) if met_minimum else False,
        "passed_overall": (
            trials == scenarios.TRIAL_TOTAL
            and successes >= scenarios.REQUIRED_SUCCESSES
            and all(met_minimum.values())
        ),
        "ignored_from_other_runs": sorted(ignored),
        "decision_actor": scenarios.TEST_ACTOR,
        "generated_at": _now(),
    }


def run_assets(*, round_name: str) -> int:
    """Verify the acceptance's assets and record the result beside the trials.

    Separated from the trials because it is a different claim. The cases show the system
    carrying out the demo; this shows that the real search and the three chart families the
    acceptance names actually work, driven through the same tools against the same services.
    Recorded in the same directory so one round answers both questions.
    """
    from agent.env_utils import load_env
    from agent.persistence.repository import ApplicationRepository

    from live import assets

    round_dir = LIVE_ROOT / round_name
    round_dir.mkdir(parents=True, exist_ok=True)

    with running_stack() as stack:
        owner = DEMO_USERS[0]
        thread_id = f"{owner}-assets"
        ApplicationRepository(stack.database).ensure_thread(
            owner_user_id=owner, thread_id=thread_id, title="资产验证"
        )
        record = assets.verify(stack=stack, owner=owner, thread_id=thread_id, env=load_env())

    (round_dir / "assets.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(record, ensure_ascii=False, indent=2))
    return 0 if record["ok"] else 1


def run_screenshots(*, round_name: str) -> int:
    """Photograph the real interface on this round's backend, at both viewports.

    The backend is the one the stack just started, and the browser reaches it through the dev
    server's proxy — the same origin the application expects. A screenshot taken against a
    backend someone else started would be a picture of a different system.
    """
    from live import browser

    round_dir = LIVE_ROOT / round_name
    round_dir.mkdir(parents=True, exist_ok=True)
    out_dir = round_dir / "screenshots"

    with running_stack() as stack:
        with browser.running_dev_server(backend=stack.base_url) as frontend:
            record = browser.capture(base_url=frontend, out_dir=out_dir)

    (round_dir / "screenshots.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    for entry in record["viewports"]:
        print(
            f"  {entry['viewport']} {entry['width']}×{entry['height']}: "
            f"{'ok' if entry['ok'] else 'FAIL'} "
            f"({entry['bytes'] // 1024}KB, {len(entry['regions'])} 个可见区域, "
            f"横向溢出 {entry['overflow_x']}px)"
        )
    for problem in record["problems"]:
        print(f"      - {problem}")
    return 0 if record["ok"] else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the fixed demo scenarios.")
    parser.add_argument("--round", default="r1", help="directory under artifacts/live/")
    parser.add_argument("--only", action="append", default=[], help="scenario id, repeatable")
    parser.add_argument("--trials", type=int, default=scenarios.TRIALS_PER_SCENARIO)
    parser.add_argument(
        "--assets",
        action="store_true",
        help="只验证真实搜索与柱/折/饼三类图表资产，不跑场景",
    )
    parser.add_argument(
        "--screenshots",
        action="store_true",
        help="只拍桌面与移动截图并检查布局，不跑场景",
    )
    args = parser.parse_args(argv)

    if args.assets:
        return run_assets(round_name=args.round)

    if args.screenshots:
        return run_screenshots(round_name=args.round)

    return asyncio.run(
        run_round(round_name=args.round, only=tuple(args.only), trials=args.trials)
    )


if __name__ == "__main__":
    raise SystemExit(main())
