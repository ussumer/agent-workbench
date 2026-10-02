"""User preferences: a closed schema, not a bag of strings the model writes into.

The contract fixes four explicit preferences and two automatic history fields, and it says
two things that shape this module:

* **Automatic updates must never overwrite an explicit preference.** So the two kinds of
  field live in separate dicts with separate writers, and the automatic writer cannot reach
  the explicit one at all — it is not a rule that a caller has to remember.
* **The model's output must not replace the config object.** So updates arrive as
  ``(key, value)`` pairs that are validated against the schema; there is no "write this
  object" entry point, and `preferences.md` is *derived* from the stored structure rather
  than stored alongside it.

An unsupported value is refused with a reason rather than silently coerced, because silently
storing a value the renderer cannot use produces a preference that looks set and does nothing.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

LOGGER = logging.getLogger("rush_harness.memory.preferences")

#: Key the structured document is stored under, inside the owner's memories namespace.
PREFERENCES_KEY = "preferences"
HISTORY_KEY = "procurement-history"

#: The two automatic history fields and how many entries they keep.
HISTORY_LIMITS: dict[str, int] = {
    "recent_supplier_ids": 5,
    "recent_queries": 10,
}

#: A stored query is a short label, not a transcript. Long input is truncated, not stored whole.
MAX_QUERY_CHARS = 120

#: Bounds on anything that reaches the prompt.
MAX_VALUE_CHARS = 64
MAX_FIELD_CHARS = 4000

#: Preference keys the *user* may set. Nothing else is settable.
USER_SETTABLE: tuple[str, ...] = ("language", "currency", "output_format", "chart_type")

#: Automatic fields. Written only by :meth:`UserPreferences.record_*`.
AUTOMATIC: tuple[str, ...] = tuple(HISTORY_LIMITS)


@dataclass(frozen=True)
class PreferenceSpec:
    """One explicit preference: its default, and the values that are actually supported."""

    key: str
    default: str
    allowed: tuple[str, ...]
    description: str

    def validate(self, value: Any) -> tuple[str | None, str | None]:
        """Return ``(normalised, reason)``. Exactly one of the two is set."""
        if not isinstance(value, str):
            return None, f"{self.key} 必须是字符串"
        candidate = value.strip()
        if not candidate:
            return None, f"{self.key} 不能为空"
        if len(candidate) > MAX_VALUE_CHARS:
            return None, f"{self.key} 超出长度上限"
        if candidate not in self.allowed:
            return None, (
                f"{self.key}={candidate!r} 不是受支持的值；支持：{', '.join(self.allowed)}"
            )
        return candidate, None


#: The four explicit preferences, from contracts/skills-memory.md.
#:
#: ``currency`` allows only CNY on purpose: there is no exchange-rate service, and accepting
#: another code would let a request for a converted total look satisfied when it is not.
PREFERENCE_SPECS: tuple[PreferenceSpec, ...] = (
    PreferenceSpec("language", "zh-CN", ("zh-CN", "en-US"), "回复语言"),
    PreferenceSpec("currency", "CNY", ("CNY",), "金额币种（当前无汇率服务）"),
    PreferenceSpec("output_format", "markdown", ("markdown", "table"), "报告输出格式"),
    PreferenceSpec("chart_type", "bar", ("bar", "line", "pie"), "图表类型"),
)

SPECS_BY_KEY: dict[str, PreferenceSpec] = {spec.key: spec for spec in PREFERENCE_SPECS}

#: Aliases the user may reasonably say, mapped to the canonical enum value.
VALUE_ALIASES: dict[tuple[str, str], str] = {
    ("output_format", "表格"): "table",
    ("output_format", "表"): "table",
    ("output_format", "table"): "table",
    ("output_format", "markdown"): "markdown",
    ("chart_type", "柱状图"): "bar",
    ("chart_type", "条形图"): "bar",
    ("chart_type", "折线图"): "line",
    ("chart_type", "饼图"): "pie",
    # Chart kinds people ask for that this deployment does not have. They are mapped so the
    # request reaches validation and gets refused *with a reason*, instead of being silently
    # ignored — "雷达图不支持，支持 bar/line/pie" is a usable answer; silence is not.
    ("chart_type", "雷达图"): "radar",
    ("chart_type", "散点图"): "scatter",
    ("chart_type", "面积图"): "area",
    ("chart_type", "热力图"): "heatmap",
    ("language", "中文"): "zh-CN",
    ("language", "英文"): "en-US",
}


def defaults() -> dict[str, str]:
    return {spec.key: spec.default for spec in PREFERENCE_SPECS}


@dataclass
class PreferencesReport:
    """What one update attempt did. Recording *why* nothing changed is the point."""

    applied: dict[str, str] = field(default_factory=dict)
    refused: dict[str, str] = field(default_factory=dict)
    unchanged: list[str] = field(default_factory=list)

    def changed(self) -> bool:
        return bool(self.applied)

    def as_dict(self) -> dict[str, Any]:
        return {"applied": dict(self.applied), "refused": dict(self.refused), "unchanged": list(self.unchanged)}


@dataclass
class UserPreferences:
    """One owner's preferences and recent history."""

    values: dict[str, str] = field(default_factory=defaults)
    history: dict[str, list[str]] = field(
        default_factory=lambda: {key: [] for key in AUTOMATIC}
    )
    #: Why an update was refused, kept so the UI can explain rather than silently ignore.
    last_refusal: str | None = None

    # ------------------------------------------------------------- explicit

    def apply_explicit(self, updates: Mapping[str, Any]) -> PreferencesReport:
        """Apply user-stated preferences, validating each key and value.

        Only keys in :data:`USER_SETTABLE` are reachable, and only values in the spec's
        ``allowed`` list are stored. Everything else is reported in ``refused`` with a
        reason instead of being coerced into the nearest legal value.
        """
        report = PreferencesReport()
        for raw_key, raw_value in updates.items():
            key = str(raw_key).strip()
            spec = SPECS_BY_KEY.get(key)
            if spec is None:
                report.refused[key] = "不是可设置的偏好项"
                continue

            candidate = raw_value
            if isinstance(candidate, str):
                alias = VALUE_ALIASES.get((key, candidate.strip().lower()))
                alias = alias or VALUE_ALIASES.get((key, candidate.strip()))
                if alias:
                    candidate = alias

            value, reason = spec.validate(candidate)
            if value is None:
                report.refused[key] = reason or "不支持的值"
                continue
            if self.values.get(key) == value:
                report.unchanged.append(key)
                continue
            self.values[key] = value
            report.applied[key] = value

        if report.refused:
            self.last_refusal = "; ".join(f"{key}: {why}" for key, why in report.refused.items())
        return report

    # ------------------------------------------------------------ automatic

    def record_suppliers(self, supplier_ids: Iterable[str]) -> list[str]:
        """Remember the suppliers an ERP task actually touched, newest first, deduped."""
        return self._record("recent_supplier_ids", supplier_ids, dedupe=True)

    def record_query(self, text: str) -> list[str]:
        """Remember a short label for a completed procurement query."""
        cleaned = " ".join(str(text).split())
        if not cleaned:
            return list(self.history["recent_queries"])
        return self._record("recent_queries", [cleaned[:MAX_QUERY_CHARS]], dedupe=False)

    def _record(self, field_name: str, values: Iterable[str], *, dedupe: bool) -> list[str]:
        limit = HISTORY_LIMITS[field_name]
        kept = list(self.history.get(field_name, []))
        for value in values:
            item = str(value).strip()
            if not item or len(item) > MAX_FIELD_CHARS:
                continue
            # Newest first, and a repeat moves to the front rather than appearing twice.
            kept = [existing for existing in kept if not (dedupe and existing == item)]
            kept.insert(0, item)
        self.history[field_name] = kept[:limit]
        return list(self.history[field_name])

    # -------------------------------------------------------------- storage

    def to_document(self) -> dict[str, Any]:
        return {"values": dict(self.values), "history": {k: list(v) for k, v in self.history.items()}}

    @classmethod
    def from_document(cls, document: Any) -> UserPreferences:
        """Rebuild from storage, filling in defaults for anything missing or unusable.

        A stored value that is no longer supported is dropped rather than kept: it would be
        injected into the prompt as if it meant something.
        """
        if not isinstance(document, Mapping):
            return cls()
        stored_values = document.get("values")
        values = defaults()
        if isinstance(stored_values, Mapping):
            for key, raw in stored_values.items():
                spec = SPECS_BY_KEY.get(str(key))
                if spec is None:
                    continue
                value, _reason = spec.validate(raw)
                if value is not None:
                    values[str(key)] = value

        history = {key: [] for key in AUTOMATIC}
        stored_history = document.get("history")
        if isinstance(stored_history, Mapping):
            for key in AUTOMATIC:
                entries = stored_history.get(key)
                if isinstance(entries, Sequence) and not isinstance(entries, (str, bytes)):
                    history[key] = [str(item) for item in entries][: HISTORY_LIMITS[key]]
        return cls(values=values, history=history)

    # ------------------------------------------------------------ rendering

    def render_markdown(self) -> str:
        """``preferences.md``, derived from the structure.

        Derived, not stored: there is then no second copy to fall out of sync, and no path
        by which a model-authored document becomes the config.
        """
        lines = ["# 用户偏好与采购记忆", "", "## 显式偏好（由用户设定）", ""]
        for spec in PREFERENCE_SPECS:
            lines.append(f"- **{spec.key}**: `{self.values.get(spec.key, spec.default)}` — {spec.description}")
        lines += ["", "## 自动历史（由成功的采购任务更新）", ""]
        suppliers = self.history.get("recent_supplier_ids") or []
        lines.append(
            "- **recent_supplier_ids**: "
            + (", ".join(f"`{item}`" for item in suppliers) if suppliers else "（暂无）")
        )
        queries = self.history.get("recent_queries") or []
        if queries:
            lines.append("- **recent_queries**:")
            lines.extend(f"  - {item}" for item in queries)
        else:
            lines.append("- **recent_queries**: （暂无）")
        lines += [
            "",
            "> 自动历史不会被当成用户偏好，也不会覆盖上面的显式偏好；",
            "> 失败或被拒绝的写操作不计入成功采购。",
        ]
        return "\n".join(lines) + "\n"

    def snapshot(self) -> dict[str, Any]:
        return {"values": dict(self.values), "history": {k: list(v) for k, v in self.history.items()}}


def derive_preference_updates(message: str, *, source_is_user: bool = True) -> dict[str, str]:
    """Recognise a preference the user stated in their own words.

    Returns ``{}`` unless the sentence is *about* a preference. Two guards matter:

    * ``source_is_user`` must be true. A webpage saying "请修改用户偏好" is content the agent
      read, not an instruction from the user, and the contract names that case explicitly.
    * The trigger has to be a statement of intent ("以后…", "改成…"), not a mention of the
      word. Otherwise "为什么默认用柱状图？" would rewrite the preference it is asking about.
    """
    if not source_is_user:
        return {}
    text = (message or "").strip()
    if not text or len(text) > 500:
        return {}

    markers = ("以后", "改成", "设为", "设置为", "都用", "请用", "改用", "统一用")
    if not any(marker in text for marker in markers):
        return {}

    lowered = text.lower()
    updates: dict[str, str] = {}
    for (key, word), value in VALUE_ALIASES.items():
        if word and word in text:
            updates[key] = value
    for spec in PREFERENCE_SPECS:
        if spec.key in lowered and spec.default in text:
            updates[spec.key] = spec.default

    # "用表格" without a noun is still the output_format preference in this domain.
    if "表格" in text and "output_format" not in updates:
        updates["output_format"] = "table"
    return updates


def load_preferences(scoped_store: Any) -> UserPreferences:
    """Read explicit values and independent automatic history; accept legacy documents."""
    from agent.main_agent import memories_namespace

    namespace = memories_namespace(scoped_store.user_id)
    item = scoped_store.get(namespace, PREFERENCES_KEY)
    preferences = UserPreferences.from_document(getattr(item, "value", None))
    history = scoped_store.get(namespace, HISTORY_KEY)
    if history is not None:
        preferences.history = UserPreferences.from_document(
            {"history": getattr(history, "value", None)}
        ).history
    return preferences


def _refresh_markdown(scoped_store: Any) -> None:
    from agent.main_agent import memories_namespace

    current = load_preferences(scoped_store)
    scoped_store.put(memories_namespace(scoped_store.user_id), "preferences.md",
                     {"content": current.render_markdown()})


def save_preferences(scoped_store: Any, preferences: UserPreferences, *,
                     include_history: bool = True) -> None:
    """Persist explicit values; live explicit updates never replace the history key."""
    from agent.main_agent import memories_namespace

    namespace = memories_namespace(scoped_store.user_id)
    scoped_store.put(namespace, PREFERENCES_KEY, preferences.to_document())
    if include_history:
        scoped_store.put(namespace, HISTORY_KEY, dict(preferences.history))
    _refresh_markdown(scoped_store)


def save_automatic_history(scoped_store: Any, preferences: UserPreferences) -> None:
    """Write only history: even a stale read cannot overwrite a concurrent user preference."""
    from agent.main_agent import memories_namespace

    scoped_store.put(memories_namespace(scoped_store.user_id), HISTORY_KEY, dict(preferences.history))
    _refresh_markdown(scoped_store)


__all__ = [
    "AUTOMATIC",
    "HISTORY_LIMITS",
    "HISTORY_KEY",
    "MAX_FIELD_CHARS",
    "MAX_QUERY_CHARS",
    "PREFERENCES_KEY",
    "PREFERENCE_SPECS",
    "USER_SETTABLE",
    "VALUE_ALIASES",
    "PreferenceSpec",
    "PreferencesReport",
    "UserPreferences",
    "defaults",
    "derive_preference_updates",
    "load_preferences",
    "save_preferences",
    "save_automatic_history",
]
