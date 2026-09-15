# Coding Instructions

Implement the procurement Harness demo from `docs/plan/README.md`.
Read `docs/plan/execution.md` and `docs/plan/HANDOFF.md` before each task.
Run `python3 scripts/plan_guard.py check`, then `next`, then `packet TXX`.
Work on one eligible task at a time. The user reviews and learns from the code.

## Constraints

- Preserve the course stack and capabilities. Obtain user agreement before a
  technology substitution or scope reduction. Java ERP, Vue, MongoDB,
  DeepAgents/LangGraph, MCP and OpenSandbox are required.
- Runtime model identity comes from configuration. Do not guess an API model ID
  from the shorthand DSV4.1flash.
- Match contracts under `docs/plan/contracts/`. Document interface changes and
  check dependent tasks. Inspect installed library signatures; lock versions.
- Do not replace ERP state with fixed responses, MongoDB with process memory,
  OpenSandbox with host execution, or live model calls with scripted workflows.
- Preserve unrelated files and user edits. Keep secrets out of logs and evidence.

## Completion

Application checks are specified in `docs/plan/verification.md` and established
by T00. A documented command is not an implemented check. Zero collected tests,
skipped mandatory checks, missing credentials, and unavailable services are not
passes. Keep failed attempts visible. Never weaken acceptance to pass a task.

Mark a task done only with required evidence and passing dependency gates.
Update `docs/plan/state.json` and `HANDOFF.md` when completing or pausing a task.
The plan checker validates structure, not business correctness or honest work.
