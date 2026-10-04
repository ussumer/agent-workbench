"""T08 acceptance: real execution inside an OpenSandbox container.

Every assertion here runs against a real container created through the control
service. There is no host-shell fallback: if the service or the image is missing,
the suite fails with the command needed to fix it.

The suite covers the whole DeepAgents backend contract (``ls``, ``read``,
``write``, ``edit``, ``glob``, ``grep``, ``execute``, ``upload_files``,
``download_files`` and their async counterparts), the failure paths, and the
isolation properties that make the sandbox worth having.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fixtures import sandbox_service, site_service  # noqa: E402

from agent.backends.custom_opensandbox import (  # noqa: E402
    ABORTED_EXIT_CODE,
    TIMEOUT_EXIT_CODE,
)
from agent.backends.sandbox_setup import (  # noqa: E402
    RULE_FILE_MODE,
    RULE_FILES,
    SCRATCH_ROOT,
    WORKSPACE_ROOT,
)

pytestmark = pytest.mark.integration

RULE_README = f"{WORKSPACE_ROOT}/rules/README.md"


# --------------------------------------------------------------------------- #
# fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="module")
def config():
    return sandbox_service.control_settings()


@pytest.fixture(scope="module")
def workspace():
    """One sandbox shared by the behavioural tests (creation is not free)."""
    with sandbox_service.running_backend() as (backend, report):
        yield backend, report


@pytest.fixture(scope="module")
def quote_site(tmp_path_factory):
    """The demo quote site, also reachable from inside a container."""
    with site_service.running_site(
        tmp_path_factory.mktemp("t08-site"), test_mode=False, host="0.0.0.0"
    ) as site:
        yield site


@pytest.fixture(scope="module")
def host_sentinel(tmp_path_factory):
    return sandbox_service.write_host_sentinel(tmp_path_factory.mktemp("t08-host"))


# --------------------------------------------------------------------------- #
# control service and image
# --------------------------------------------------------------------------- #


def test_control_service_is_healthy(config):
    health = sandbox_service.require_control_service(config)

    assert health["status"] == "healthy"
    assert config.base_url() == f"http://{sandbox_service.CONTROL_HOST}:{sandbox_service.CONTROL_PORT}"


def test_pinned_execution_image_is_present(config):
    """The image is pinned by tag; a missing image fails instead of silently pulling another."""
    sandbox_service.require_image(config)

    assert config.image == "opensandbox/code-interpreter:v1.1.0"


def test_the_backend_is_a_real_container_not_a_local_shell(workspace):
    backend, _ = workspace

    identity = backend.execute("cat /etc/hostname; echo ---; ls -d /workspace")
    assert identity.exit_code == 0

    container_id = sandbox_service.container_id_for(backend.id)
    assert container_id, f"no Docker container found for sandbox {backend.id}"
    assert sandbox_service.container_exists(container_id)
    assert container_id[:12] in identity.output


# --------------------------------------------------------------------------- #
# workspace preparation
# --------------------------------------------------------------------------- #


def test_workspace_is_prepared_with_directories_and_rules(workspace):
    backend, report = workspace

    assert report is not None
    listing = backend.ls(WORKSPACE_ROOT)
    assert listing.error is None
    names = {Path(entry["path"]).name for entry in listing.entries}
    assert {"rules", "skills", "scratch"} <= names

    for relative, content in RULE_FILES.items():
        read = backend.read(relative)
        assert read.error is None, relative
        assert read.file_data["content"].strip() == content.strip()


def test_rule_files_are_written_read_only(workspace):
    """Scope of the guarantee: the files are mode 444 and re-created per sandbox.

    This deters an accidental overwrite by a non-root tool. It is deliberately not
    claimed as a hard boundary against the container's own root user — the real
    protections are that the sandbox is ephemeral (see
    ``test_a_new_sandbox_starts_from_a_pristine_workspace``) and that the container
    cannot reach the host.
    """
    backend, _ = workspace

    info = backend.stat([RULE_README])[RULE_README]
    # The API carries modes as octal *digits* (444), not as the decimal permission
    # value (292), so compare against the same constant the setup used.
    assert info.mode == RULE_FILE_MODE, f"规则文件应为只读 444，实际 {info.mode}"


def test_the_reported_python_version_comes_from_the_container(workspace):
    backend, report = workspace

    executed = backend.execute("python3 -V 2>&1")
    assert executed.exit_code == 0
    assert report.python_version.strip() in executed.output
    assert report.image == "opensandbox/code-interpreter:v1.1.0"


# --------------------------------------------------------------------------- #
# execute
# --------------------------------------------------------------------------- #


def test_execute_returns_real_output_and_exit_code(workspace):
    backend, _ = workspace

    response = backend.execute("echo sandbox-says-hello; uname -s")

    assert response.exit_code == 0
    assert "sandbox-says-hello" in response.output
    assert "Linux" in response.output


def test_a_non_zero_exit_is_reported_rather_than_hidden(workspace):
    backend, _ = workspace

    response = backend.execute("echo before-failure; exit 7")

    assert response.exit_code == 7
    assert "before-failure" in response.output
    assert "exit" in response.output.lower()


def test_stderr_is_not_discarded(workspace):
    backend, _ = workspace

    response = backend.execute("echo to-stdout; echo to-stderr 1>&2")

    assert response.exit_code == 0
    assert "to-stdout" in response.output
    assert "to-stderr" in response.output


def test_a_timeout_is_reported_explicitly(workspace):
    """A killed command must not look like an empty success."""
    backend, _ = workspace

    response = backend.execute("sleep 60", timeout=5)

    assert response.exit_code == TIMEOUT_EXIT_CODE
    assert response.output.strip(), "超时必须有明确输出，不能是空字符串"
    assert "timed out" in response.output
    assert response.exit_code != ABORTED_EXIT_CODE


# --------------------------------------------------------------------------- #
# protocol coverage (sync)
# --------------------------------------------------------------------------- #


def test_ls_read_write_glob_grep_round_trip(workspace):
    backend, _ = workspace
    target = f"{SCRATCH_ROOT}/protocol.txt"

    written = backend.write(target, "alpha\nbeta\ngamma\n")
    assert written.error is None

    read = backend.read(target)
    assert read.error is None
    # `read` is line-based and paginated, so it reconstructs the content from whole
    # lines: a trailing newline is not part of the returned payload.
    assert read.file_data["content"].splitlines() == ["alpha", "beta", "gamma"]
    assert read.total_lines == 3

    listing = backend.ls(SCRATCH_ROOT)
    assert any(Path(entry["path"]).name == "protocol.txt" for entry in listing.entries)

    globbed = backend.glob("*.txt", SCRATCH_ROOT)
    assert any(Path(match["path"]).name == "protocol.txt" for match in globbed.matches)

    grepped = backend.grep("beta", SCRATCH_ROOT)
    assert [match["line"] for match in grepped.matches] == [2]


def test_edit_replaces_text_inside_the_sandbox(workspace):
    backend, _ = workspace
    target = f"{SCRATCH_ROOT}/editable.txt"
    backend.write(target, "one\ntwo\nthree\n")

    edited = backend.edit(target, "two", "TWO")

    assert edited.error is None
    assert edited.occurrences == 1
    assert "TWO" in backend.read(target).file_data["content"]


def test_upload_and_download_round_trip_binary_content(workspace):
    backend, _ = workspace
    payload = bytes(range(256))
    target = f"{SCRATCH_ROOT}/binary.bin"

    uploaded = backend.upload_files([(target, payload)])
    assert [response.error for response in uploaded] == [None]

    downloaded = backend.download_files([target])
    assert downloaded[0].error is None
    assert downloaded[0].content == payload
    assert hashlib.sha256(downloaded[0].content).hexdigest() == hashlib.sha256(payload).hexdigest()


def test_downloading_a_missing_file_reports_file_not_found(workspace):
    backend, _ = workspace

    responses = backend.download_files([f"{SCRATCH_ROOT}/does-not-exist.txt"])

    assert responses[0].content is None
    assert responses[0].error == "file_not_found"


def test_invalid_paths_are_rejected_structurally(workspace):
    backend, _ = workspace

    uploads = backend.upload_files([("relative/path.txt", b"x"), ("", b"x")])
    assert [response.error for response in uploads] == ["invalid_path", "invalid_path"]

    downloads = backend.download_files(["also/relative.txt"])
    assert downloads[0].error == "invalid_path"


# --------------------------------------------------------------------------- #
# protocol coverage (async)
# --------------------------------------------------------------------------- #


def test_async_entry_points_cover_the_same_operations(workspace):
    backend, _ = workspace
    target = f"{SCRATCH_ROOT}/async.txt"

    async def exercise() -> dict:
        return {
            "execute": await backend.aexecute("echo async-execute"),
            "write": await backend.awrite(target, "first\nsecond\n"),
            "read": await backend.aread(target),
            "edit": await backend.aedit(target, "second", "SECOND"),
            "ls": await backend.als(SCRATCH_ROOT),
            "glob": await backend.aglob("async.txt", SCRATCH_ROOT),
            "grep": await backend.agrep("SECOND", SCRATCH_ROOT),
            "upload": await backend.aupload_files([(f"{SCRATCH_ROOT}/a.bin", b"payload")]),
            "download": await backend.adownload_files([f"{SCRATCH_ROOT}/a.bin"]),
            "delete": await backend.adelete(f"{SCRATCH_ROOT}/a.bin"),
        }

    results = asyncio.run(exercise())

    assert results["execute"].exit_code == 0
    assert "async-execute" in results["execute"].output
    assert results["write"].error is None
    assert "first" in results["read"].file_data["content"]
    assert results["edit"].occurrences == 1
    assert any(Path(entry["path"]).name == "async.txt" for entry in results["ls"].entries)
    assert [Path(m["path"]).name for m in results["glob"].matches] == ["async.txt"]
    assert [m["text"].strip() for m in results["grep"].matches] == ["SECOND"]
    assert results["upload"][0].error is None
    assert results["download"][0].content == b"payload"
    assert results["delete"].error is None


# --------------------------------------------------------------------------- #
# sandbox reaches the demo quote site
# --------------------------------------------------------------------------- #


def test_the_sandbox_reads_the_demo_quote_site_over_http(workspace, quote_site):
    """Network path proven end-to-end: container → host gateway → quote site."""
    backend, _ = workspace
    base = sandbox_service.sandbox_url_for_host_config(quote_site.base_url)

    script = (
        "import urllib.request\n"
        f"print(urllib.request.urlopen('{base}/suppliers/S001/quotes', timeout=20)"
        ".read().decode('utf-8'))\n"
    )
    backend.upload_files([(f"{SCRATCH_ROOT}/fetch_quotes.py", script.encode("utf-8"))])
    response = backend.execute(f"python3 {SCRATCH_ROOT}/fetch_quotes.py")

    assert response.exit_code == 0, response.output
    assert 'data-supplier-id="S001"' in response.output
    assert 'data-part-id="P001"' in response.output
    assert "演示数据" in response.output


def test_the_sandbox_writes_a_file_and_the_content_hashes_match(workspace, quote_site):
    """Fetch, transform, write, then verify the digest after downloading it back."""
    backend, _ = workspace
    base = sandbox_service.sandbox_url_for_host_config(quote_site.base_url)
    script = f"""
import hashlib, json, urllib.request
html = urllib.request.urlopen('{base}/suppliers/S002/quotes', timeout=20).read()
digest = hashlib.sha256(html).hexdigest()
json.dump({{'sha256': digest, 'size': len(html)}}, open('/workspace/scratch/quotes.json', 'w'))
print(digest)
"""
    backend.upload_files([(f"{SCRATCH_ROOT}/digest_quotes.py", script.encode("utf-8"))])
    executed = backend.execute(f"python3 {SCRATCH_ROOT}/digest_quotes.py")

    assert executed.exit_code == 0, executed.output
    sandbox_digest = executed.output.strip().splitlines()[-1]

    downloaded = backend.download_files([f"{SCRATCH_ROOT}/quotes.json"])
    assert downloaded[0].error is None
    record = json.loads(downloaded[0].content.decode("utf-8"))

    assert record["sha256"] == sandbox_digest
    assert record["size"] > 0


# --------------------------------------------------------------------------- #
# isolation
# --------------------------------------------------------------------------- #


def test_host_filesystem_stays_out_of_reach(workspace, host_sentinel):
    """The host sentinel is unchanged no matter what the sandbox tries.

    The container has no bind mount to the host, so a host path is just an
    odd-looking string inside the container. The assertion that matters is that the
    real host file keeps its original digest.
    """
    backend, _ = workspace
    sentinel_path, digest_before = host_sentinel
    host_path = str(sentinel_path)

    probe = backend.execute(f"cat '{host_path}' 2>&1; echo marker-done")
    assert digest_before not in probe.output, "宿主文件内容不应出现在沙箱里"

    backend.execute(f"echo tampered > '{host_path}' 2>&1; echo rc=$?")
    backend.upload_files([(f"{SCRATCH_ROOT}/sentinel-copy", sentinel_path.read_bytes())])

    assert sandbox_service.host_sentinel_digest(sentinel_path) == digest_before, (
        "宿主哨兵文件被沙箱改动了"
    )


def test_the_docker_socket_is_not_visible(workspace):
    backend, _ = workspace

    probe = backend.execute("ls -l /var/run/docker.sock 2>&1; echo rc=$?")

    assert "/var/run/docker.sock" in probe.output
    assert "No such file" in probe.output, probe.output


def test_path_traversal_cannot_escape_the_container(workspace):
    """`..` resolves inside the container's own filesystem, never on the host."""
    backend, _ = workspace
    backend.write(f"{SCRATCH_ROOT}/escape.txt", "inside\n")

    container_hostname = backend.execute("cat /etc/hostname").output.strip()
    traversal = backend.execute(f"cat {SCRATCH_ROOT}/../../../etc/hostname").output.strip()

    assert container_hostname
    assert traversal == container_hostname, "越界路径读到的应该是容器自己的 /etc/hostname"


def test_container_security_limits_are_applied(workspace):
    """The limits come from infra/sandbox/sandbox.toml, verified on the real container."""
    backend, _ = workspace
    container_id = sandbox_service.container_id_for(backend.id)
    assert container_id

    import subprocess

    completed = subprocess.run(
        [
            "docker",
            "inspect",
            container_id,
            "--format",
            "{{.HostConfig.PidsLimit}}|{{.HostConfig.Privileged}}|{{len .HostConfig.CapDrop}}",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        check=False,
    )
    pids_limit, privileged, cap_drop = completed.stdout.strip().split("|")

    assert privileged == "false"
    assert int(cap_drop) > 0, "容器必须丢弃一部分 capability"
    assert int(pids_limit) > 0, "必须限制进程数，避免 fork bomb"


# --------------------------------------------------------------------------- #
# lifecycle
# --------------------------------------------------------------------------- #


def test_closing_the_backend_destroys_the_container():
    """A sandbox that leaks its container would eventually exhaust the host."""
    with sandbox_service.running_backend(metadata={"purpose": "t08-lifecycle"}) as (
        backend,
        _,
    ):
        container_id = sandbox_service.container_id_for(backend.id)
        assert container_id and sandbox_service.container_exists(container_id)
        sandbox_id = backend.id

    assert sandbox_service.container_id_for(sandbox_id) is None, (
        "沙箱退出后不应残留容器"
    )


def test_a_new_sandbox_starts_from_a_pristine_workspace():
    """Rule files are ephemeral: a fresh container always has the untouched rules."""
    from agent.backends.sandbox_setup import RULE_FILES as expected_rules

    with sandbox_service.running_backend(metadata={"purpose": "t08-ephemeral"}) as (
        first,
        _,
    ):
        first.write(RULE_README, "TAMPERED")
        assert "TAMPERED" in first.read(RULE_README).file_data["content"]

    with sandbox_service.running_backend(metadata={"purpose": "t08-ephemeral-2"}) as (
        second,
        _,
    ):
        content = second.read(RULE_README).file_data["content"]

    assert content.strip() == expected_rules[RULE_README].strip()
