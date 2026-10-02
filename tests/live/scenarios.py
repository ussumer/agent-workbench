"""D01-D08 as data: what each demo case does, and what must be true afterwards.

The scenarios live in a module rather than inside the runner because three different things
need them and none of them should own the definition:

* the runner drives them,
* the reconciler checks the numbers they predict,
* and the acceptance suite asserts *about* them — that every case exists, that the numbers
  they promise are the contract's numbers, and that none of them is missing an invariant.

Keeping them here also makes the rule from ``demo.md`` enforceable: "不要用关键词分支硬编码
这些答案". A scenario is a user's words plus the facts that must hold; there is no branch on
the text anywhere, because the runner never inspects the input to decide what to expect.

The numbers are cross-checked against ``fixtures/expected-v1.json`` rather than restated, so a
seed change shows up here as a failure instead of as a demo that quietly proves the wrong
thing.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
EXPECTED_PATH = REPO_ROOT / "fixtures" / "expected-v1.json"

#: How many times each case runs. ``demo.md`` requires three, and the acceptance requires at
#: least two of the three to succeed per case.
TRIALS_PER_SCENARIO = 3
PER_SCENARIO_MINIMUM = 2
TRIAL_TOTAL = TRIALS_PER_SCENARIO * 8
REQUIRED_SUCCESSES = 22

#: Decisions an automated client makes on the user's behalf. Named so the evidence can say
#: who clicked, which ``demo.md`` requires: a report that silently counts a robot click as a
#: human one is misrepresenting the demo.
TEST_ACTOR = "automated-test-client"


@dataclass(frozen=True)
class Supplement:
    """What the user says when the assistant asks for missing order fields.

    ``demo.md`` writes this as a plain sentence — 「P001，50件，单价25.50元」 — and that is what
    the automated client sends, because it is what a person would type into the box. The tool
    does not read fields out of a sentence; it hands the words back to the model, which reads
    them and names the values. So ``says`` is the whole declaration for a prose answer.

    ``fields`` exists for the other channel: a caller that submits a form (a JSON string of
    named fields, which is the shape ``_resume_payload`` forwards) can declare that instead.
    Both are real; a scenario uses whichever its case is about.
    """

    says: str
    fields: Mapping[str, Any] | None = None

    def payload(self) -> str:
        """What goes on the wire as ``resume.supplement``.

        Always a string, because that is the contract's type for this field — a JSON object
        would be stringified by the route into a Python repr that nothing can parse.
        """
        import json

        if self.fields is None:
            return self.says
        return json.dumps(dict(self.fields), ensure_ascii=False)

    def as_dict(self) -> dict[str, Any]:
        return {
            "says": self.says,
            "fields": dict(self.fields) if self.fields is not None else None,
            "sent": self.payload(),
        }


@dataclass(frozen=True)
class Scenario:
    """One demo case."""

    id: str
    title: str
    #: The user's turns, in order. Natural language; nothing branches on the text.
    #:
    #: A turn may contain ``{order_id}``, which the runner fills from the fixture it prepared.
    #: That is not a convenience: the ERP is shared across the round, so by the third D03
    #: trial the owner has several orders and "刚才那笔" identifies nothing. ``demo.md`` says
    #: the fixture's order id is given to the conversation, and this is where that happens.
    turns: tuple[str, ...]
    #: What must hold afterwards. Each one is checked by the reconciler against the ERP, the
    #: database, the sandbox or the artifacts — never against the model's own prose.
    expectations: tuple[str, ...]
    #: Fixtures this case needs prepared before it starts, so the cases are independent.
    setup: str = ""
    #: Decisions taken by the automated client, recorded as an automated actor.
    decisions: tuple[str, ...] = ()
    #: Answers the automated client gives to supplementation interrupts, in order.
    supplements: tuple[Supplement, ...] = ()
    #: Numbers the case predicts, keyed by a name the reconciler understands.
    numbers: Mapping[str, str] = field(default_factory=dict)
    #: Whether this case needs an interactive surface (approval, supplement) driven for it.
    interactive: bool = False
    #: A background task the client submits before the conversation starts, given as the
    #: instruction to run. ``demo.md``'s D08 opens with 「后台生成采购分析报告。」 — and that is a
    #: *client* act, not a chat message: the async-task API is the browser's interface (T20),
    #: the agent holds no tool that starts one, and turning it into a chat turn would have the
    #: main conversation do the work the case says must not block it.
    background_instruction: str = ""
    #: Whether the client then starts a *second* task and cancels it — D08's last step
    #: ("另启动任务并取消"), which is about cancellation rather than about the first task.
    cancels_second_task: bool = False
    #: Whether one approval is answered twice *at the same time*, by two clients (D04).
    #:
    #: Two answers in sequence would only show that the API refuses a second decision arriving
    #: later, which is a different property from the one D04 claims: that two decisions landing
    #: on the same interrupt cannot both proceed. The runner has to actually overlap them.
    concurrent_approvals: bool = False
    #: Rebuild the sandbox before this turn (1-based), as this case's own ``setup`` describes.
    #:
    #: D07 says the skill must still work 「重启 API 并重建沙箱后」, and the runner used not to do
    #: it — so the fourth turn's premise was simply false: the files the model had written
    #: minutes earlier were still in the container, nothing had to be restored and nothing had
    #: to be read. The case's expectation (a trace of reading SKILL.md *and* running the script)
    #: was then being judged against a restart that never happened.
    rebuild_before_turn: int | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "title": self.title,
            "turns": list(self.turns),
            "expectations": list(self.expectations),
            "setup": self.setup,
            "decisions": list(self.decisions),
            "supplements": [item.as_dict() for item in self.supplements],
            "numbers": dict(self.numbers),
            "interactive": self.interactive,
            "background_instruction": self.background_instruction,
            "cancels_second_task": self.cancels_second_task,
            "concurrent_approvals": self.concurrent_approvals,
            "rebuild_before_turn": self.rebuild_before_turn,
            "decision_actor": TEST_ACTOR if (self.decisions or self.supplements) else None,
        }


def _expected() -> dict:
    return json.loads(EXPECTED_PATH.read_text(encoding="utf-8"))


def _quote_total() -> str:
    """The comparison total, taken from the contract's own fixture."""
    return str(_expected()["quote_comparison"]["total_amount"])


def _order_create_total() -> str:
    return str(_expected()["orders"][0]["total_amount"]) if "orders" in _expected() else "1275.00"


def _order_update_total() -> str:
    return "1530.00"


def _skill_total() -> str:
    return str(_expected()["reorder_cost_summary"]["total_amount"])


SCENARIOS: tuple[Scenario, ...] = (
    Scenario(
        id="D01",
        title="库存不足的配件与按目标库存的补货数量",
        turns=("查一下库存不足的配件，按目标库存给出补货数量。",),
        expectations=(
            "trace 中出现 ERP/MCP 的真实读调用，而不是模型自述",
            "预警集合恰为 P001/P003/P004",
            "建议补货数量为 P001=42、P003=15、P004=30",
            "没有发生任何写操作",
        ),
        numbers={"P001": "42", "P003": "15", "P004": "30"},
    ),
    Scenario(
        id="D02",
        title="信息不足先补充，再审批后创建订单",
        turns=("为华东配件创建采购订单。",),
        # ``demo.md`` writes the supplement as the user's line "P001，50件，单价25.50元". The
        # tool does not read fields out of a sentence, so the client submits them — the same
        # thing a person does when the question is a form.
        supplements=(Supplement(says="P001，50件，单价25.50元"),),
        expectations=(
            "第一次调用因信息不足而中断补充，不是直接下单",
            "批准之前 ERP 里没有新订单",
            "批准之后恰好一张订单，总额 1275.00",
            "订单行是 P001×50×25.50",
        ),
        decisions=("approve",),
        numbers={"total_amount": _order_create_total(), "quantity": "50", "unit_price": "25.50"},
        interactive=True,
    ),
    Scenario(
        id="D03",
        title="拒绝不改原单，再次修改并批准",
        turns=(
            "把刚才那笔订单（{order_id}）的数量改为60件，其他不变。",
            "再改一次，这次我批准。",
        ),
        setup="在测试准备阶段建立 50 件 P001 的订单，记录真实 order_id 传给本案例，不依赖 D02 的执行顺序",
        expectations=(
            "第一次被拒绝后原订单数量与 version 都没有变",
            "再次修改并批准后总额 1530.00 且 version=2",
            "库存仍为 8（订单不扣库存）",
        ),
        decisions=("reject", "approve"),
        numbers={"total_amount": _order_update_total(), "version": "2", "on_hand": "8"},
        interactive=True,
    ),
    Scenario(
        id="D04",
        title="同一审批两处同时批准，写成功后响应中断",
        # The candidate is produced by this turn rather than seeded as a database row. That is
        # the same thing a person sees — a write is offered, nobody has approved it yet — and
        # it keeps the case independent of D02 the way ``demo.md`` requires, without the runner
        # fabricating approval records the agent never asked for.
        turns=("为华东配件创建采购订单：P001，50 件，单价 25.50 元。",),
        setup="本轮对话产生一个未批准的创建候选动作（不依赖 D02 的执行顺序），随后同一中断被两个客户端同时批准",
        expectations=(
            "两个同时的批准只产生一张订单",
            "响应中断后重新查询得到同一个订单编号，没有产生第二次采购",
            "操作账本里该 operation_id 只有一条成功记录",
        ),
        decisions=("approve", "approve"),
        numbers={"total_amount": _order_create_total(), "quantity": "50", "unit_price": "25.50"},
        interactive=True,
        concurrent_approvals=True,
    ),
    Scenario(
        id="D05",
        title="比价、生成补货分析与柱状图、交付报告",
        turns=("比较缺货物料的供应商报价，按目标库存生成补货分析和柱状图，给我报告。",),
        expectations=(
            "沙箱内真实抓取两个供应商页面（来源地址是容器可达地址）",
            "推荐 P001=S002、P003=S001、P004=S002",
            "合计 2553.00",
            "报告与图表都是非空文件且可下载、hash 可核对",
        ),
        numbers={
            "total_amount": _quote_total(),
            "P001": "S002",
            "P003": "S001",
            "P004": "S002",
        },
    ),
    Scenario(
        id="D06",
        title="偏好写入、新会话恢复、切用户不可见",
        turns=("以后报告优先用表格。", "再看看库存不足的配件。"),
        setup="新用户 namespace 无偏好",
        expectations=(
            "偏好写入后在新会话里仍然生效",
            "切到 demo-b 看不到 demo-a 的会话、技能与报告",
            "两个用户的偏好不串",
        ),
    ),
    Scenario(
        id="D07",
        title="创建技能、验证、分配、跨重启与重建恢复",
        turns=(
            "创建一个补货金额汇总技能，分配给采购分析专家。",
            # A real user answers the question the agent asks. D07 #2 stopped to ask whether the
            # new skill should reuse the existing cost rule or invent its own, and the scripted
            # client had nothing to say — so the conversation stalled and the case failed for a
            # reason that says nothing about whether the system works. A person would simply
            # answer, which is what this line is. It costs the case nothing: if the agent does
            # not ask, it reads as an ordinary instruction and the run continues.
            "按纯计算来就好，复用现有技能的口径，不要另立一套。",
            # The sample is named, not merely referred to. ``demo.md`` says the case verifies
            # against *the fixed* sample, and a fixed sample is a place and a set of numbers the
            # user brings — not something the agent invents. Left unnamed, three runs said
            # "验证这个技能" and one of them validated against its own ``examples/input.json``
            # instead, so the total was right for its own data and wrong for the case's. Naming
            # the path is what a person does, and it makes the case about the system rather than
            # about whether the model happened to look in the right directory.
            "用 /workspace/samples/reorder-input.json 这份固定样例验证这个技能。",
            "重启之后再用一次这个技能。",
        ),
        setup="新 namespace 无同名技能；重启 API 并重建沙箱后仍需可用",
        expectations=(
            "生成、测试、分配、Store 持久化、恢复都有证据",
            "使用技能时必须留下读 SKILL.md 与执行脚本的轨迹，只重读描述不算使用",
            "固定样例算出 1533.00",
            "未提供 scope 时要询问，不能自动全员分配",
        ),
        numbers={"total_amount": _skill_total()},
        # The fourth turn is 「重启之后再用一次这个技能。」 — so the rebuild has to happen before
        # it, not be assumed to have happened.
        rebuild_before_turn=4,
    ),
    Scenario(
        id="D08",
        title="后台任务不阻塞主对话，可查询、可取消",
        # The opening line of ``demo.md``'s D08 is the background launch, which is why it is
        # not in ``turns``: the conversation under test is the one that has to keep working
        # *while* that task runs.
        turns=("顺便问一下现在库存怎么样？",),
        expectations=(
            "主对话在后台运行期间仍能继续回答",
            "后台任务可查询到真实 Protocol run 且能取回产物",
            "另起的任务取消后状态为 cancelled，且没有新的工具调用",
        ),
        background_instruction="生成采购分析报告",
        # Cancelling is a user action and the automated client performs it, so it carries the
        # test actor's name — but it is an act on the task API, not a graph decision, and
        # declaring it as one would put a type the graph does not accept into a resume.
        cancels_second_task=True,
        interactive=True,
    ),
)


#: The extra path ``demo.md`` requires outside D07: installing a skill from the resource site.
DOWNLOAD_SCENARIO_ID = "DL01"


def by_id(scenario_id: str) -> Scenario:
    for scenario in SCENARIOS:
        if scenario.id == scenario_id:
            return scenario
    raise KeyError(scenario_id)


def all_expectation_count() -> int:
    return sum(len(scenario.expectations) for scenario in SCENARIOS)


def contract_numbers() -> dict[str, str]:
    """The numbers the contract fixes, for cross-checking the scenarios above.

    Read from ``expected-v1.json`` rather than repeated, so this function is the one place
    that decides whether a scenario's promise agrees with the seed.
    """
    expected = _expected()
    return {
        "quote_total": str(expected["quote_comparison"]["total_amount"]),
        "skill_total": str(expected["reorder_cost_summary"]["total_amount"]),
    }


def problems() -> list[str]:
    """Everything wrong with the definitions above, as a list of sentences.

    Called by the acceptance suite, so the scenarios cannot drift into promising numbers the
    contract does not produce, or into a case with no invariant to check.
    """
    found: list[str] = []
    seen: set[str] = set()
    for scenario in SCENARIOS:
        if scenario.id in seen:
            found.append(f"{scenario.id}: duplicate id")
        seen.add(scenario.id)
        if not scenario.turns:
            found.append(f"{scenario.id}: no user turn")
        if not scenario.expectations:
            found.append(f"{scenario.id}: no expectation to check")
        drives_human = bool(
            scenario.decisions
            or scenario.supplements
            or scenario.background_instruction
            or scenario.cancels_second_task
        )
        if drives_human and not scenario.interactive:
            found.append(f"{scenario.id}: drives a human surface but is not marked interactive")
        if scenario.interactive and not drives_human:
            found.append(f"{scenario.id}: marked interactive but drives nothing")
        for index, supplement in enumerate(scenario.supplements):
            if not supplement.says:
                found.append(f"{scenario.id}: supplement {index} has no user line")
            if not supplement.payload():
                found.append(f"{scenario.id}: supplement {index} would send nothing")
        # A setup that prepares an id has to hand it over in the conversation: the ERP is
        # shared across the whole round, so by the third trial "刚才那笔" identifies nothing
        # and the agent asking "which order?" would be right, not wrong.
        if "order_id" in scenario.setup and "{order_id}" not in " ".join(scenario.turns):
            found.append(f"{scenario.id}: setup prepares an order_id that no turn ever mentions")
        if scenario.concurrent_approvals and len(scenario.decisions) < 2:
            found.append(
                f"{scenario.id}: races two approvals but declares fewer than two decisions"
            )

    numbers = contract_numbers()
    for scenario in SCENARIOS:
        for key, value in scenario.numbers.items():
            if key == "total_amount" and scenario.id in {"D05"} and value != numbers["quote_total"]:
                found.append(f"{scenario.id}: total {value} != contract {numbers['quote_total']}")
            if key == "total_amount" and scenario.id == "D07" and value != numbers["skill_total"]:
                found.append(f"{scenario.id}: total {value} != contract {numbers['skill_total']}")

    expected_ids = {f"D{index:02d}" for index in range(1, 9)}
    if seen != expected_ids:
        found.append(f"scenario ids differ from D01-D08: {sorted(seen ^ expected_ids)}")
    return found


__all__ = [
    "DOWNLOAD_SCENARIO_ID",
    "PER_SCENARIO_MINIMUM",
    "REQUIRED_SUCCESSES",
    "SCENARIOS",
    "TEST_ACTOR",
    "TRIALS_PER_SCENARIO",
    "TRIAL_TOTAL",
    "Scenario",
    "all_expectation_count",
    "by_id",
    "contract_numbers",
    "problems",
]
