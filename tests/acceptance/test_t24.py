"""T24 acceptance: the docs describe a system that exists, and starting it really works.

The claim this task makes is "a user and the next model can start, understand and maintain the
demo from a clean environment". Almost every way of failing that claim is a *document* that
points somewhere the code does not go: a script that was never written, a subcommand that was
renamed, a path that moved. Those are cheap to check and they are checked here for real — by
reading the documents and looking for the files they name.

The rest of the claim cannot be checked by reading: "start it" either works or it does not. So
one test starts the stack through ``scripts/dev.py``, waits for it to answer, asks a read-only
question through the API, stops it, and starts it again to confirm the data survived — the whole
loop, against real services, because a start command that was never executed is exactly the kind
of thing that passes a review and fails a user.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
DEV_PY = REPO_ROOT / "scripts" / "dev.py"
DOCS = {
    "README.md": REPO_ROOT / "README.md",
    "docs/runtime/runbook.md": REPO_ROOT / "docs" / "runtime" / "runbook.md",
    "walkthrough.md": REPO_ROOT / "walkthrough.md",
}

#: `python scripts/<script>.py <subcommand>` as the documents write it.
COMMAND_PATTERN = re.compile(r"python\s+(scripts/[\w/]+\.py)\s+([a-z][\w-]*)")

#: Repo-relative paths the documents put in backticks. Only ones with a slash and a known
#: suffix are followed up: prose mentions plenty of bare names that are not paths.
PATH_PATTERN = re.compile(r"`((?:src|tests|scripts|docs|fixtures|frontend|infra|erp)/[\w./-]+)`")
PATH_SUFFIXES = (".py", ".md", ".java", ".json", ".yml", ".yaml", ".toml", ".vue", ".ts", ".css")


def _text(name: str) -> str:
    return DOCS[name].read_text(encoding="utf-8")


def _run(args: list[str], *, timeout: float = 600.0) -> subprocess.CompletedProcess[str]:
    environment = dict(os.environ)
    environment["OPENSANDBOX_INSECURE_SERVER"] = "YES"
    environment["PYTHONIOENCODING"] = "utf-8"
    return subprocess.run(
        [sys.executable, str(DEV_PY), *args],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        env=environment,
        check=False,
    )


# ------------------------------------------------------------------ the documents


def test_every_document_the_task_names_exists() -> None:
    for name, path in DOCS.items():
        assert path.is_file(), f"{name} 不存在"
        assert path.stat().st_size > 500, f"{name} 太短，像占位文件"


def test_the_documents_only_name_commands_that_exist() -> None:
    """Every `python scripts/x.py sub` in the docs: the script exists and declares that sub."""
    problems: list[str] = []
    seen = 0
    for name in DOCS:
        for script, sub in COMMAND_PATTERN.findall(_text(name)):
            path = REPO_ROOT / script
            if not path.is_file():
                problems.append(f"{name}: 引用了不存在的脚本 {script}")
                continue
            seen += 1
            # The name as a quoted token rather than a particular construction: what this
            # catches is a rename (the thing that rots a runbook), not which way a parser was
            # built. ``plan_guard.py`` declares its subcommands in a list, and a check that
            # demanded ``add_parser("x")`` called a working command missing.
            source = path.read_text(encoding="utf-8")
            if f'"{sub}"' not in source and f"'{sub}'" not in source:
                problems.append(f"{name}: {script} 没有 {sub!r} 这个子命令")
    assert not problems, "\n".join(problems)
    assert seen >= 8, f"只找到 {seen} 条命令，文档不像在讲怎么启动"


def test_the_documents_only_name_files_that_exist() -> None:
    problems: list[str] = []
    seen = 0
    for name in DOCS:
        for raw in PATH_PATTERN.findall(_text(name)):
            if not raw.endswith(PATH_SUFFIXES):
                continue
            seen += 1
            if not (REPO_ROOT / raw).exists():
                problems.append(f"{name}: 指向不存在的路径 {raw}")
    assert not problems, "\n".join(problems)
    assert seen >= 10, f"只找到 {seen} 个路径，文档没有真正指向代码"


def test_the_runbook_states_what_each_command_cannot_prove() -> None:
    """A runbook that only lists commands teaches the reader to over-trust them."""
    text = _text("docs/runtime/runbook.md")
    assert "不能证明什么" in text
    assert "残留" in text, "没有说明停止之后应当没有残留进程"
    assert "8088" in text, "没有写明夹具站必须固定端口，而那正是 D05 抓不到报价的根因"


def test_the_readme_does_not_claim_the_user_reviewed_anything() -> None:
    text = _text("README.md")
    assert "用户尚未审阅" in text or "尚未审阅" in text
    assert "review" in text, "没有说明人工审阅是独立字段"


def test_the_walkthrough_points_at_real_functions_and_files() -> None:
    text = _text("walkthrough.md")
    for needle in ("build_main_agent", "request_order_info", "user_skills_restore"):
        assert needle in text, f"walkthrough 没有讲到 {needle}"
    assert "task" in text and "委派" in text


# ------------------------------------------------------------------ the launcher


def test_the_launcher_declares_the_lifecycle_subcommands() -> None:
    source = DEV_PY.read_text(encoding="utf-8")
    for sub in ("doctor", "up", "status", "logs", "smoke", "seed", "down", "reset"):
        assert f'add_parser("{sub}"' in source, f"dev.py 没有 {sub} 子命令"


def test_the_launcher_parses_the_wire_the_way_the_runner_does() -> None:
    """No second SSE parser.

    The first version of ``smoke`` re-implemented the framing, got it wrong, and reported "no
    tool calls" for a run that had made them. The reader lives in ``scripts/demo.py`` and is
    imported, not copied.
    """
    source = DEV_PY.read_text(encoding="utf-8")
    assert "from demo import read_frames, trace_of" in source
    assert "read_frames(" in source


def test_reset_refuses_without_confirmation_before_touching_anything() -> None:
    result = _run(["reset"], timeout=60.0)
    assert result.returncode != 0
    assert "--yes" in (result.stdout + result.stderr)
    assert "artifacts" in (result.stdout + result.stderr)


def test_reset_scope_is_named_and_bounded() -> None:
    """The database a reset can reach is one this project created, and nothing else is touched."""
    source = DEV_PY.read_text(encoding="utf-8")
    assert "DEFAULT_TEST_DATABASE" in source, "reset 没有复用测试库前缀作为护栏"
    assert "沙箱容器未动" in source or "沙箱容器不" in source
    assert "client.drop_database" in source
    assert "drop_database" in source and source.count("drop_database") == 1, (
        "reset 只应有一处 drop_database"
    )


# ------------------------------------------------------------------ the real loop


def test_the_stack_starts_answers_and_keeps_its_data_across_a_restart() -> None:
    """The claim the README makes, executed: up → smoke → down → up.

    Slow on purpose. Everything above this line checks that the documents describe a system
    that exists; this checks that the system starts, answers a question, stops without leaving
    services behind, and comes back to the same database — which is what "数据默认保留" means.
    """
    up = _run(["up", "--wait", "300"], timeout=420.0)
    assert up.returncode == 0, f"up 失败：\n{up.stdout[-2000:]}\n{up.stderr[-2000:]}"
    assert "就绪" in up.stdout

    try:
        status = _run(["status"], timeout=120.0)
        assert status.returncode == 0, status.stdout + status.stderr
        assert "ok" in status.stdout
        assert status.stdout.count("ok") >= 3, "至少三个服务应当是健康的"

        smoke = _run(["smoke"], timeout=420.0)
        assert smoke.returncode == 0, f"烟测失败：\n{smoke.stdout[-2000:]}"
        assert "done=completed" in smoke.stdout
        assert "task" in smoke.stdout, "这一轮没有走委派路径"

        database = ""
        session = json.loads((REPO_ROOT / "artifacts" / "dev" / "session.json").read_text("utf-8"))
        database = str(session["database"])
        assert database, "会话没有记录数据库名"
        from pymongo import MongoClient
        from api_view.web_config import PersistenceSettings

        settings = PersistenceSettings.from_env()
        with MongoClient(settings.mongo_uri, serverSelectionTimeoutMS=5000) as client:
            # Smoke wrote this run; retain its entire original record, not just the DB name.
            saved_run = client[database]["runs"].find_one()
            assert saved_run is not None, "烟测没有保存真实运行记录"
    finally:
        down = _run(["down", "--timeout", "180"], timeout=240.0)
    assert down.returncode == 0, f"down 失败：\n{down.stdout[-2000:]}"
    with MongoClient(settings.mongo_uri, serverSelectionTimeoutMS=5000) as client:
        assert client[database]["runs"].find_one({"_id": saved_run["_id"]}) == saved_run

    # Restarting must find the same database, or "data is kept by default" is not true.
    again = _run(["up", "--wait", "300"], timeout=420.0)
    assert again.returncode == 0, f"第二次 up 失败：\n{again.stdout[-2000:]}"
    try:
        session = json.loads((REPO_ROOT / "artifacts" / "dev" / "session.json").read_text("utf-8"))
        assert str(session["database"]) == database, "重启换了数据库，数据没有保留"
        with MongoClient(settings.mongo_uri, serverSelectionTimeoutMS=5000) as client:
            assert client[database]["runs"].find_one({"_id": saved_run["_id"]}) == saved_run, (
                "数据库名没有改变，但重启丢失或改写了运行记录"
            )
    finally:
        _run(["down", "--timeout", "180"], timeout=240.0)


@pytest.mark.parametrize("document", sorted(DOCS))
def test_the_documents_do_not_leak_credentials(document: str) -> None:
    """A runbook is the most likely thing in a repo to be pasted into a chat window."""
    text = _text(document)
    for pattern in (r"sk-[A-Za-z0-9]{16,}", r"[0-9a-f]{32}\.[A-Za-z0-9]{10,}", r"Bearer\s+[A-Za-z0-9._-]{20,}"):
        assert not re.search(pattern, text), f"{document} 里像是有密钥"
