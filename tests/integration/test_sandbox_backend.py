"""Integration tests for the OpenSandbox backend adapter.

Two halves:

* Fast, deterministic checks that need no container: configuration parsing, the
  guarantee that the client is aimed at our own control service, path validation,
  error-code mapping, and the fact that every protocol entry point is declared on
  the class rather than resolved dynamically.
* One live sandbox that walks the whole protocol matrix, so a method that exists
  but is broken cannot hide behind a name-only check.
"""

from __future__ import annotations

import asyncio
import inspect
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fixtures import sandbox_service  # noqa: E402

from agent.backends.custom_opensandbox import (  # noqa: E402
    OpenSandboxBackend,
    _error_code,
    _validate_path,
)
from agent.backends.sandbox_setup import (  # noqa: E402
    DEFAULT_DOMAIN,
    DEFAULT_IMAGE,
    SCRATCH_ROOT,
    SandboxConfigurationError,
    SandboxRuntimeConfig,
)

pytestmark = pytest.mark.integration

#: Every method the DeepAgents gateway may call, sync and async.
PROTOCOL_METHODS: tuple[str, ...] = (
    "ls",
    "read",
    "write",
    "edit",
    "glob",
    "grep",
    "delete",
    "upload_files",
    "download_files",
    "execute",
    "als",
    "aread",
    "awrite",
    "aedit",
    "aglob",
    "agrep",
    "adelete",
    "aupload_files",
    "adownload_files",
    "aexecute",
)


# --------------------------------------------------------------------------- #
# configuration (no container)
# --------------------------------------------------------------------------- #


def test_defaults_point_at_the_local_control_service():
    config = SandboxRuntimeConfig.from_env({})

    assert config.domain == DEFAULT_DOMAIN
    assert config.protocol == "http"
    assert config.image == DEFAULT_IMAGE
    assert config.domain != "app.opensandbox.ai", "不得默认指向厂商托管端点"


def test_a_base_url_is_reduced_to_domain_and_protocol():
    config = SandboxRuntimeConfig.from_env({"OPENSANDBOX_BASE_URL": "http://127.0.0.1:18080/"})

    assert config.domain == "127.0.0.1:18080"
    assert config.protocol == "http"
    assert config.base_url() == "http://127.0.0.1:18080"


def test_configuration_rejects_an_empty_image():
    with pytest.raises(SandboxConfigurationError):
        SandboxRuntimeConfig.from_env({"OPENSANDBOX_IMAGE": "   "})


def test_the_connection_config_is_always_explicit():
    """Without this the SDK would fall back to its hosted SaaS endpoint."""
    config = SandboxRuntimeConfig(domain="127.0.0.1:18080", protocol="http", api_key=None)
    connection = config.connection_config()

    assert connection.get_domain() == "127.0.0.1:18080"
    assert "app.opensandbox.ai" not in connection.get_base_url()
    assert connection.get_api_key() == ""


def test_resource_requests_carry_the_cpu_and_memory_limits():
    config = SandboxRuntimeConfig(cpu="2", memory="2Gi")

    assert config.resource_requests() == {"cpu": "2", "memory": "2Gi"}


# --------------------------------------------------------------------------- #
# protocol surface (no container)
# --------------------------------------------------------------------------- #


def test_every_protocol_method_is_declared_never_dynamic():
    """``__getattr__`` magic would make a renamed SDK method fail at call time."""
    assert "__getattr__" not in OpenSandboxBackend.__dict__

    missing = [
        name
        for name in PROTOCOL_METHODS
        if not callable(getattr(OpenSandboxBackend, name, None))
    ]
    assert not missing, f"后端缺少协议方法: {missing}"

    abstract = getattr(OpenSandboxBackend, "__abstractmethods__", frozenset())
    assert not abstract, f"仍有未实现的抽象方法: {sorted(abstract)}"


def test_the_backend_declares_the_primitives_it_adapts_itself():
    """The four base-class primitives must be ours, not inherited defaults."""
    for name in ("execute", "aexecute", "upload_files", "aupload_files",
                 "download_files", "adownload_files", "id"):
        assert name in OpenSandboxBackend.__dict__, f"{name} 应由本适配器显式实现"


def test_path_validation_rejects_structurally_bad_paths():
    assert _validate_path("") == "invalid_path"
    assert _validate_path("   ") == "invalid_path"
    assert _validate_path("relative/path") == "invalid_path"
    assert _validate_path("/abs\x00olute") == "invalid_path"
    assert _validate_path("/workspace/ok.txt") is None


def test_error_mapping_uses_the_protocol_vocabulary():
    class FakeHttpError(Exception):
        def __init__(self, status_code: int) -> None:
            super().__init__(f"http {status_code}")
            self.status_code = status_code

    assert _error_code(FakeHttpError(404)) == "file_not_found"
    assert _error_code(FakeHttpError(403)) == "permission_denied"
    assert _error_code(FakeHttpError(409)) == "is_directory"

    # An unrecognised failure keeps its own message instead of being flattened.
    plain = _error_code(ValueError("something odd"))
    assert plain.startswith("ValueError")
    assert plain != "file_not_found"


def test_backend_method_signatures_match_the_protocol():
    execute = inspect.signature(OpenSandboxBackend.execute)
    assert list(execute.parameters)[1:] == ["command", "timeout"]
    assert execute.parameters["timeout"].kind is inspect.Parameter.KEYWORD_ONLY

    aexecute = inspect.signature(OpenSandboxBackend.aexecute)
    assert inspect.iscoroutinefunction(OpenSandboxBackend.aexecute)
    assert aexecute.parameters["timeout"].kind is inspect.Parameter.KEYWORD_ONLY


# --------------------------------------------------------------------------- #
# live protocol matrix
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="module")
def backend():
    with sandbox_service.running_backend(metadata={"purpose": "t08-integration"}) as (
        running,
        _,
    ):
        yield running


def test_the_live_protocol_matrix_is_callable(backend):
    """Call every protocol method once and require a structured result from each."""
    target = f"{SCRATCH_ROOT}/matrix.txt"
    binary = f"{SCRATCH_ROOT}/matrix.bin"

    calls = {
        "write": lambda: backend.write(target, "l1\nl2\nl3\n"),
        "read": lambda: backend.read(target),
        "ls": lambda: backend.ls(SCRATCH_ROOT),
        "glob": lambda: backend.glob("matrix.txt", SCRATCH_ROOT),
        "grep": lambda: backend.grep("l2", SCRATCH_ROOT),
        "edit": lambda: backend.edit(target, "l2", "L2"),
        "upload_files": lambda: backend.upload_files([(binary, b"\x01\x02")]),
        "download_files": lambda: backend.download_files([binary]),
        "execute": lambda: backend.execute("echo matrix-ok"),
        "delete": lambda: backend.delete(binary),
    }
    for name, call in calls.items():
        result = call()
        assert result is not None, name
        if name in {"execute"}:
            assert result.exit_code == 0, (name, result)
        else:
            assert getattr(result, "error", None) in (None, []), (name, result)

    async def matrix() -> dict:
        return {
            "aexecute": await backend.aexecute("echo async-matrix"),
            "aread": await backend.aread(target),
            "als": await backend.als(SCRATCH_ROOT),
            "aglob": await backend.aglob("matrix.txt", SCRATCH_ROOT),
            "agrep": await backend.agrep("L2", SCRATCH_ROOT),
            "awrite": await backend.awrite(f"{SCRATCH_ROOT}/async-matrix.txt", "x\n"),
            "aedit": await backend.aedit(f"{SCRATCH_ROOT}/async-matrix.txt", "x", "y"),
            "aupload_files": await backend.aupload_files([(binary, b"\x03")]),
            "adownload_files": await backend.adownload_files([binary]),
            "adelete": await backend.adelete(binary),
        }

    results = asyncio.run(matrix())
    assert results["aexecute"].exit_code == 0
    assert "async-matrix" in results["aexecute"].output
    assert results["aread"].error is None
    assert results["als"].error is None
    assert results["aglob"].error is None
    assert results["agrep"].error is None
    assert results["awrite"].error is None
    assert results["aedit"].error is None
    assert results["aupload_files"][0].error is None
    assert results["adownload_files"][0].content == b"\x03"
    assert results["adelete"].error is None


def test_the_sandbox_id_is_the_identifier_the_control_plane_reports(backend):
    assert backend.id
    assert sandbox_service.container_id_for(backend.id) is not None
