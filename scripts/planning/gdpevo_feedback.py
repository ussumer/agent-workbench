"""Public, deterministic diagnostics for GDPevo structured decisions.

The diagnostic critic may inspect the public task and the candidate output, but never
returns the private oracle answer.  It reports violated constraints and the field that
must be rechecked, which gives reflection a useful repair target.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any


def _quote_maps(data: dict[str, Any]):
    quotes = {q["quote_id"]: q for q in data["quotes"]}
    relationships = {(r["part_id"], r["supplier_id"]): r for r in data["erp_relationships"]}
    return quotes, relationships


def _selected(candidate: dict[str, Any], quotes: dict[str, Any], issues: list[dict[str, str]]):
    rows = candidate.get("allocation", []) if isinstance(candidate, dict) else []
    selected: list[tuple[dict[str, Any], int]] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"quote_id", "packs"}:
            issues.append({"field": "allocation", "code": "invalid_line", "message": "allocation 每行必须只有 quote_id 和正整数 packs"})
            continue
        quote_id, packs = row["quote_id"], row["packs"]
        if quote_id in seen:
            issues.append({"field": "allocation", "code": "duplicate_quote", "message": "同一报价不能重复出现在 allocation"})
            continue
        seen.add(quote_id)
        quote = quotes.get(quote_id)
        if quote is None:
            issues.append({"field": "allocation", "code": "unknown_quote", "message": "allocation 引用了当前输入不存在的报价"})
            continue
        if type(packs) is not int or packs < 1:
            issues.append({"field": "allocation", "code": "invalid_packs", "message": "packs 必须是正整数"})
            continue
        if quote["status"] in {"draft", "revoked"}:
            issues.append({"field": "allocation", "code": "inactive_quote", "message": "不能采购 draft 或已 revoked 的报价"})
        if quote["pack_price_cents"] is None:
            issues.append({"field": "allocation", "code": "unknown_price", "message": "价格未知的报价不能直接写入确定金额的 allocation"})
            continue
        selected.append((quote, packs))
    return selected


def diagnose_submission(task: dict[str, Any], candidate: dict[str, Any] | None) -> list[dict[str, str]]:
    """Return public constraint diagnostics, without revealing oracle allocations."""
    data = task["input"]
    issues: list[dict[str, str]] = []
    if not isinstance(candidate, dict):
        return [{"field": "format", "code": "not_object", "message": "输出必须是 JSON 对象"}]
    quotes, relationships = _quote_maps(data)
    selected = _selected(candidate, quotes, issues)
    by_part: dict[str, int] = defaultdict(int)
    goods: dict[str, int] = defaultdict(int)
    suppliers: set[str] = set()
    for quote, packs in selected:
        supplier = quote["supplier_id"]
        suppliers.add(supplier)
        by_part[quote["part_id"]] += packs * quote["pack_size"]
        goods[supplier] += packs * quote["pack_price_cents"]
        relation = relationships.get((quote["part_id"], supplier))
        if relation is None or not relation["active"]:
            issues.append({"field": "allocation", "code": "inactive_relationship", "message": "ERP 供应关系必须 active"})
        elif relation["lead_days"] > next((d["max_lead_days"] for d in data["demands"] if d["part_id"] == quote["part_id"]), 10**9):
            issues.append({"field": "allocation", "code": "late_supplier", "message": "采购交期超过该物料 max_lead_days"})
        if relation is not None and packs * quote["pack_size"] > relation["capacity_units"]:
            issues.append({"field": "allocation", "code": "capacity_exceeded", "message": "采购数量超过该 ERP 关系供货容量"})
        if not quote["effective_at"] <= data["as_of"] <= quote["expires_at"]:
            issues.append({"field": "allocation", "code": "expired_quote", "message": "采购报价未生效或已过期"})
    fixed: dict[str, int] = defaultdict(int)
    fixed_cost = 0
    for commitment in data["commitments"]:
        fixed[commitment["part_id"]] += commitment["quantity"]
        fixed_cost += commitment["paid_cents"]
    fulfilled = {part: fixed[part] + by_part[part] for part in {d["part_id"] for d in data["demands"]}}
    demand_by_part = {d["part_id"]: d for d in data["demands"]}
    for part, demand in demand_by_part.items():
        quantity = fulfilled[part]
        if candidate.get("disposition") == "execute" and demand["required"] and quantity < demand["quantity"]:
            issues.append({"field": "allocation", "code": "required_shortage", "message": f"必需物料 {part} 仍有缺口，不能提交 execute"})
        if data["site"] == "pit_stop" and quantity > demand["quantity"] + 1:
            issues.append({"field": "allocation", "code": "site_surplus", "message": f"pit_stop 的 {part} 本轮盈余超过 1 件"})
    groups = {d["kit_id"] for d in data["demands"] if d.get("kit_id")}
    for kit in groups:
        members = [d for d in data["demands"] if d.get("kit_id") == kit]
        complete = [fulfilled[d["part_id"]] >= d["quantity"] for d in members]
        if any(complete) and not all(complete):
            issues.append({"field": "allocation", "code": "kit_partial", "message": f"kit {kit} 必须整组补足或整组不买"})
    freight: dict[str, int] = {}
    for supplier in suppliers:
        cart = data["carts"].get(supplier, {"freight_cents": 0, "free_at_cents": 0})
        freight[supplier] = 0 if goods[supplier] >= cart["free_at_cents"] else cart["freight_cents"]
    if candidate.get("freight_cents") != freight:
        issues.append({"field": "freight_cents", "code": "freight_mismatch", "message": "freight_cents 必须按本轮每个供应商购物车和免邮门槛重算"})
    total = fixed_cost + sum(goods.values()) + sum(freight.values())
    if total > data["budget_cents"]:
        issues.append({"field": "allocation", "code": "budget_exceeded", "message": "当前 allocation 加运费及历史实付超过预算，必须重新选择可行覆盖"})
    approval = candidate.get("approval_request")
    expected_lines = sorted(candidate.get("allocation", []), key=lambda x: x.get("quote_id", "")) if isinstance(candidate.get("allocation"), list) else []
    if not isinstance(approval, dict) or approval.get("revision") != data["revision"] or approval.get("reuse_prior_approval") is not False:
        issues.append({"field": "approval_request", "code": "approval_binding", "message": "审批必须绑定当前 revision 且 reuse_prior_approval=false"})
    elif sorted(approval.get("lines", []), key=lambda x: x.get("quote_id", "")) != expected_lines:
        issues.append({"field": "approval_request", "code": "approval_lines_mismatch", "message": "审批 lines 必须与最终 allocation 完全一致"})
    disposition = candidate.get("disposition")
    if issues and disposition == "execute" and any(i["code"] in {"budget_exceeded", "required_shortage", "kit_partial", "site_surplus", "inactive_quote", "unknown_price"} for i in issues):
        issues.append({"field": "disposition", "code": "disposition_conflict", "message": "当前候选违反约束，应先修改候选；候选非法不等于整个目标 infeasible。可选项可舍弃，必需已满足时空追加也可 execute。"})
    return issues


def feedback_for(task: dict[str, Any], grade: dict[str, Any], candidate: dict[str, Any] | None = None) -> dict[str, Any]:
    if task.get("split") != "train":
        raise ValueError("diagnostic learning feedback is train-only")
    fields = ("disposition", "allocation", "source_selection", "freight_cents", "commitment_ledger", "approval_request")
    failed = [f for f in fields if not grade.get("points", {}).get(task["task_id"] + "-" + f, False)]
    diagnostics = diagnose_submission(task, candidate)
    return {
        "success": bool(grade.get("business_success", False)),
        "failed_fields": failed,
        "diagnostics": diagnostics,
        "arithmetic_audit": arithmetic_audit(task, candidate),
        "repair_order": ["hard_constraints", "allocation", "freight_cents", "approval_request"],
        "scope_note": "诊断只检验当前候选，不证明全局无解或最优；禁止据此把不可采购的可选项泛化为整个目标 infeasible。",
        "feedback_kind": "public-constraint-diagnostic, no gold answer",
    }


def arithmetic_audit(task: dict[str, Any], candidate: dict[str, Any] | None) -> dict[str, Any]:
    """Report input-derived arithmetic, not an optimized answer or selected plan."""
    data = task["input"]
    demand = {d["part_id"]: d for d in data["demands"]}
    fixed = defaultdict(int)
    for c in data["commitments"]:
        fixed[c["part_id"]] += c["quantity"]
    rows = []
    for q in data["quotes"]:
        if q["part_id"] not in demand:
            continue
        remaining = max(0, demand[q["part_id"]]["quantity"] - fixed[q["part_id"]])
        packs = (remaining + q["pack_size"] - 1) // q["pack_size"]
        prices = [q["pack_price_cents"]] if q["pack_price_cents"] is not None else q["price_bounds_cents"]
        rows.append({"quote_id": q["quote_id"], "status": q["status"],
                     "remaining_units": remaining, "covering_packs": packs,
                     "surplus_units": packs * q["pack_size"] - remaining,
                     "goods_cost_bounds_cents": [packs * p for p in prices] if prices else None})
    return {"budget_cents": data["budget_cents"],
            "committed_paid_cents": sum(c["paid_cents"] for c in data["commitments"]),
            "quote_arithmetic": rows,
            "note": "逐报价算术事实，含不可用报价；不是推荐方案，未做全局最优搜索。"}
