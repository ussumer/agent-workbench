"""Check the learning package's local references and expected chapter sections.

This is a documentation check, not an application test or proof of user understanding.
"""

from pathlib import Path
import re


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    directory = root / "docs" / "learning"
    names = ["01-chat-run.md", "02-approval.md", "03-erp-idempotency.md",
             "04-delegation.md", "05-state-sandbox.md", "06-skills-budget-memory.md",
             "07-evidence-interview.md"]
    errors = []
    references = 0
    for name in ["README.md", "PROGRESS.md", *names]:
        path = directory / name
        if not path.is_file():
            errors.append(f"missing file: {name}")
            continue
        body = path.read_text(encoding="utf-8")
        if name in names:
            for heading in ["学习目标", "机制解释", "代码阅读", "具体案例", "自检", "面试表达", "确认"]:
                if f"## {heading}\n" not in body:
                    errors.append(f"{name}: missing section {heading}")
        for target in re.findall(r"\[[^\]]+\]\(([^)]+)\)", body):
            if "://" in target or target.startswith("#"):
                continue
            references += 1
            local = target.split("#", 1)[0]
            if not (path.parent / local).is_file():
                errors.append(f"{name}: missing target {target}")
    if errors:
        print("\n".join(errors))
        return 1
    print(f"Documentation check passed: 7 chapters, entry, progress, {references} local references.")
    print("Application behavior, rendered layout and user confirmation are not checked.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
