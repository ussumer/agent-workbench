"""Source packages must survive Git transport while generated evidence stays local."""

import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def ignored(paths: list[str]) -> set[str]:
    result = subprocess.run(
        ["git", "check-ignore", "--no-index", "--stdin", "-z"],
        input=("\0".join(paths) + "\0").encode("utf-8"),
        cwd=ROOT, capture_output=True, check=False,
    )
    assert result.returncode in (0, 1), result.stderr
    return set(result.stdout.decode("utf-8").rstrip("\0").split("\0")) if result.stdout else set()


def test_all_python_source_including_artifact_package_can_be_tracked():
    paths = [p.relative_to(ROOT).as_posix() for p in (ROOT / "src").rglob("*.py")]
    for name in ("__init__.py", "models.py", "service.py", "store.py"):
        assert f"src/agent/artifacts/{name}" in paths
    excluded = ignored(paths)
    assert not excluded, "Python source excluded from Git: " + repr(excluded)


def test_generated_root_evidence_and_runtime_remain_ignored():
    paths = ["artifacts/tasks/T26/generated.json", "artifacts/dev/session.json"]
    assert ignored(paths) == set(paths)
