"""Layer one: asking the user for what is missing, and refusing to guess it.

The separation between the two human-in-the-loop layers is the point of this module:

* **Supplementation** happens when the request is under-specified. The tool interrupts,
  asks for named fields, and re-asks if the answer is still incomplete. It never fills a
  blank with a plausible value — a guessed unit price is indistinguishable from a real one
  once it is in an order.
* **Approval** happens later, on a complete and frozen action (see
  :mod:`agent.approval.middleware`). Nothing here approves anything.

The required-field list is data, so callers can assert on it, and the validator is shared
between the tool and the tests rather than duplicated.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from typing import Any

from langchain_core.tools import StructuredTool
from langgraph.types import interrupt

#: Interrupt type tag carried in the payload, so the API can tell the two layers apart.
SUPPLEMENT_INTERRUPT = "order_info_supplement"
APPROVAL_INTERRUPT = "hitl_approval"

#: Tools this module contributes to the agent catalogue. The aggregate lives in
#: ``agent.tools.__init__``; this is only the part belonging to the HITL layer.
LOCAL_TOOL_NAMES: tuple[str, ...] = ("request_order_info",)

#: Fields a create needs before it can be reviewed.
CREATE_REQUIRED: tuple[str, ...] = ("supplier_id", "lines")

#: Fields an update needs on top of the create ones.
UPDATE_REQUIRED: tuple[str, ...] = ("order_id", "expected_version", "supplier_id", "lines")

#: Fields required on every order line.
LINE_REQUIRED: tuple[str, ...] = ("part_id", "quantity", "unit_price")

PART_ID_PATTERN = re.compile(r"^P\d{3}$")
#: Two decimal places, never three: the ERP rejects implicit rounding.
UNIT_PRICE_PATTERN = re.compile(r"^(?:0\.(?:0[1-9]|[1-9][0-9])|[1-9][0-9]{0,5}\.[0-9]{2})$")

QUANTITY_MIN = 1
QUANTITY_MAX = 10000


def line_problems(index: int, line: Mapping[str, Any]) -> list[str]:
    """What is wrong with one order line. Named per field so the user can fix it."""
    problems: list[str] = []
    part_id = line.get("part_id")
    if not isinstance(part_id, str) or not PART_ID_PATTERN.match(part_id):
        problems.append(f"lines[{index}].part_id")

    quantity = line.get("quantity")
    if not isinstance(quantity, int) or isinstance(quantity, bool):
        problems.append(f"lines[{index}].quantity")
    elif not QUANTITY_MIN <= quantity <= QUANTITY_MAX:
        problems.append(f"lines[{index}].quantity (需在 {QUANTITY_MIN}..{QUANTITY_MAX})")

    unit_price = line.get("unit_price")
    if not isinstance(unit_price, str) or not UNIT_PRICE_PATTERN.match(unit_price):
        problems.append(f"lines[{index}].unit_price (需两位小数字符串)")

    return problems


def missing_fields(draft: Mapping[str, Any], *, update: bool = False) -> list[str]:
    """Everything the draft still lacks, in a stable order.

    A value that is present but malformed counts as missing: reporting it as supplied would
    let the write tool proceed with something the ERP will reject later, after the user has
    already been asked to approve it.
    """
    required = UPDATE_REQUIRED if update else CREATE_REQUIRED
    absent: list[str] = []

    for field in required:
        if field == "lines":
            continue
        value = draft.get(field)
        if value in (None, "", []):
            absent.append(field)

    if update and "expected_version" in draft:
        version = draft.get("expected_version")
        if not isinstance(version, int) or isinstance(version, bool) or version < 1:
            absent.append("expected_version (需从 1 起的整数)")

    lines = draft.get("lines")
    if not isinstance(lines, Sequence) or isinstance(lines, (str, bytes)) or not lines:
        absent.append("lines")
        return absent
    if len(lines) > 20:
        absent.append("lines (一单最多 20 行)")

    for index, line in enumerate(lines):
        if not isinstance(line, Mapping):
            absent.append(f"lines[{index}]")
            continue
        absent.extend(line_problems(index, line))

    return absent


def _normalise(answer: Any) -> dict[str, Any]:
    """Accept the shapes a supplement may arrive in, and refuse anything else.

    The API sends ``{"resume": {"supplement": "@@@JSON@@@"}}``; a direct caller may send the
    mapping itself. A string that is not JSON is kept as prose under ``note`` rather than
    being parsed leniently into fields nobody specified.
    """
    if isinstance(answer, Mapping):
        inner = answer.get("supplement", answer)
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
                return {"note": text}
            if isinstance(parsed, Mapping):
                return dict(parsed)
        return {"note": text}

    return {}


def request_order_info(
    field_names: Sequence[str],
    question: str,
    known_values: Mapping[str, Any] | None = None,
    update: bool = False,
) -> str:
    """Ask the user for the fields an order still needs.

    Interrupts once per attempt. A *structured* answer that still leaves fields outstanding
    interrupts again, so the loop is visible to the caller rather than being resolved by a
    guess.

    An answer in the user's own words does not loop. This tool will not read fields out of a
    sentence — a lenient parser turns "便宜点" into a unit price, which T12 pins as a property
    worth keeping — but re-asking forever is not the alternative: the question reaches the user
    through a text box, so prose is the normal case, not an error case. The words go back to
    the model instead, which is the only party here that can understand them and which must
    then *name* the values it read. Nothing is made more permissive by this: the values it
    supplies still pass through the same validator, and a value it cannot read stays missing.
    """
    draft: dict[str, Any] = dict(known_values or {})

    # What to ask about is decided by the validator, not by the caller's list. Two reasons, and
    # the second was found by running the demo rather than by reading the code:
    #
    # * the validator is the same function that will judge the finished draft, so asking for
    #   something it does not require puts a question to the user that has no bearing on
    #   whether the order can be written;
    # * a model that has just read the user's answer calls again with `known_values` filled in
    #   to say "here are the values, go on" — and seeding the loop from `field_names` made that
    #   call interrupt too, asking the user for what the model had just supplied.
    #
    # `field_names` is still carried in the payload: it is what the caller meant to ask for,
    # and a reader comparing the two can see when the model's idea of the requirement and the
    # validator's disagree.
    outstanding = missing_fields(draft, update=update)
    attempts = 0

    while outstanding:
        attempts += 1
        answer = interrupt(
            {
                "interrupt_type": SUPPLEMENT_INTERRUPT,
                "missing_fields": list(outstanding),
                "requested_fields": list(field_names),
                "known_values": _jsonable(draft),
                "question": question,
                "attempt": attempts,
                "instructions": (
                    "请补充上面列出的字段。不要替你猜测物料、数量或单价；"
                    "如果某一项没有确定值，请明确说明。"
                ),
            }
        )

        prose = _prose_of(answer)
        supplied = _normalise(answer)
        if not supplied:
            outstanding = list(outstanding)
            continue

        for key, value in supplied.items():
            if value not in (None, "", []):
                draft[key] = value

        outstanding = missing_fields(draft, update=update)
        if outstanding and prose is not None:
            return json.dumps(
                {
                    "ok": False,
                    "error": {
                        "code": "SUPPLEMENT_UNSTRUCTURED",
                        "message": (
                            "用户是用自然语言回答的，本工具不把句子拆成字段。"
                            "请把你从这段话里读到的值放进 known_values 再调用一次本工具；"
                            "读不出来的字段保持缺失，不要猜测。用户原话见 data.user_reply。"
                        ),
                    },
                    "data": {
                        "user_reply": prose,
                        "missing_fields": outstanding,
                        "order_draft": _jsonable(draft),
                    },
                },
                ensure_ascii=False,
            )
        if outstanding:
            question = "还有字段没有确定，请补充。" + question

    return json.dumps(
        {
            "ok": True,
            "data": {
                "order_draft": _jsonable(draft),
                # Completeness is stated, not merely implied by the absence of a complaint. A
                # model that asked about a field and never received it keeps treating that field
                # as required, and "the tool did not object" is not evidence it can act on: a
                # live run ended with no order because the draft was complete, the tool answered
                # ok, and the model still refused to continue on a `currency` nobody had asked
                # for. Naming what is missing — an empty list — is the same information the
                # interrupt carries, in the shape the caller needs at the other end of the flow.
                "missing_fields": [],
                "note": (
                    "必需字段已齐全，下一步是调用 order_create / order_update。"
                    "不要因为可选字段没有出现而停下——工具没有把它列为必填。"
                ),
            },
        },
        ensure_ascii=False,
    )


def _prose_of(answer: Any) -> str | None:  # noqa: ANN401
    """The answer's text when it is *not* a structured payload, else ``None``.

    Split out from :func:`_normalise` rather than folded into it: the normaliser's job is to
    produce fields, and this one's job is to say whether there were any. A caller that read
    "did it produce fields" out of the returned mapping would treat ``{"note": ...}`` as an
    answer and lose the distinction this function exists to draw.
    """
    inner = answer.get("supplement", answer) if isinstance(answer, Mapping) else answer

    if isinstance(inner, bytes):
        inner = inner.decode("utf-8", errors="replace")
    if not isinstance(inner, str):
        return None

    stripped = inner.strip()
    if not stripped.startswith("{"):
        return inner
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        return inner
    return None if isinstance(parsed, Mapping) else inner


def _jsonable(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return [_jsonable(item) for item in value]
    return value


REQUEST_ORDER_INFO_DESCRIPTION = (
    "当用户的下单或改单请求缺少必要字段时调用此工具向用户提问。"
    "它会中断当前运行，绝不会替你猜测物料、数量或单价。"
    "字段：supplier_id、lines（每行 part_id/quantity/unit_price）；"
    "改单还需要 order_id 与 expected_version。"
    "**用户会用自然语言回答，例如「P001，50件，单价25.50元」。**"
    "这时工具返回 ok=false、code=SUPPLEMENT_UNSTRUCTURED，并在 data.user_reply 里给出"
    "用户原话：你要把这段话读成字段，填进 known_values 再调用一次本工具。"
    "读不出来的字段不要填，也不要用记忆或推断补全——工具会再次向你确认。"
)


def build_request_order_info_tool() -> StructuredTool:
    """The supplement tool, shaped for the framework."""

    def call(
        field_names: list[str],
        question: str,
        known_values: dict[str, Any] | None = None,
        update: bool = False,
    ) -> str:
        return request_order_info(
            field_names=field_names,
            question=question,
            known_values=known_values,
            update=update,
        )

    return StructuredTool.from_function(
        func=call,
        name="request_order_info",
        description=REQUEST_ORDER_INFO_DESCRIPTION,
    )


__all__ = [
    "APPROVAL_INTERRUPT",
    "CREATE_REQUIRED",
    "LINE_REQUIRED",
    "LOCAL_TOOL_NAMES",
    "PART_ID_PATTERN",
    "REQUEST_ORDER_INFO_DESCRIPTION",
    "SUPPLEMENT_INTERRUPT",
    "UNIT_PRICE_PATTERN",
    "UPDATE_REQUIRED",
    "build_request_order_info_tool",
    "line_problems",
    "missing_fields",
    "request_order_info",
]
