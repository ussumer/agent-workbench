"""T67 local SDK retry boundary; no provider, model, or service calls."""

from __future__ import annotations

import asyncio
import json

import httpx
import langchain_openai
import openai
import pytest

from agent.config import ModelConfig

pytestmark = pytest.mark.unit


@pytest.fixture
def local_model(monkeypatch):
    config = ModelConfig(
        model_id="configured-test-model",
        base_url="https://sdk-test.invalid/v1",
        api_key="synthetic-test-key",
        temperature=0.25,
        max_tokens=64,
        timeout_seconds=3.0,
    )
    requests = {"sync": [], "async": []}

    def unavailable(mode):
        def handle(request):
            requests[mode].append(request)
            return httpx.Response(
                503,
                json={"error": {"message": "local retry test", "type": "server_error"}},
            )
        return handle

    sync_client = httpx.Client(transport=httpx.MockTransport(unavailable("sync")))
    async_client = httpx.AsyncClient(transport=httpx.MockTransport(unavailable("async")))
    real_chat_openai = langchain_openai.ChatOpenAI

    def construct(**kwargs):
        return real_chat_openai(
            **kwargs, http_client=sync_client, http_async_client=async_client
        )

    # Keep the actual ChatOpenAI and OpenAI clients; replace only external HTTP transport.
    monkeypatch.setattr(langchain_openai, "ChatOpenAI", construct)
    try:
        yield config, requests
    finally:
        sync_client.close()
        asyncio.run(async_client.aclose())


@pytest.mark.parametrize("mode", ["sync", "async"])
def test_zero_retries_makes_one_http_attempt_on_503(local_model, mode):
    config, requests = local_model
    chat = config.create_chat_model(max_retries=0)

    with pytest.raises(openai.InternalServerError) as failure:
        if mode == "sync":
            chat.invoke("local transport test")
        else:
            asyncio.run(chat.ainvoke("local transport test"))

    assert failure.value.status_code == 503
    assert len(requests[mode]) == 1
    assert requests["async" if mode == "sync" else "sync"] == []
    assert chat.root_client.max_retries == 0
    assert chat.root_async_client.max_retries == 0
    payload = json.loads(requests[mode][0].content)
    assert payload["model"] == "configured-test-model"


@pytest.mark.parametrize("invalid", [-1, 1.5, True, "0"])
def test_retry_override_rejects_nonnegative_integer_violations(local_model, invalid):
    config, requests = local_model
    with pytest.raises(ValueError, match="max_retries"):
        config.create_chat_model(max_retries=invalid)
    assert requests == {"sync": [], "async": []}


def test_no_argument_preserves_sdk_defaults_and_configured_identity(local_model):
    config, requests = local_model
    original = config.redacted()
    chat = config.create_chat_model()

    assert chat.root_client.max_retries == 2
    assert chat.root_async_client.max_retries == 2
    assert chat.model_name == "configured-test-model"
    assert str(chat.root_client.base_url) == "https://sdk-test.invalid/v1/"
    assert str(chat.root_async_client.base_url) == "https://sdk-test.invalid/v1/"
    assert chat.temperature == 0.25
    assert chat.max_tokens == 64
    assert config.redacted() == original
    assert requests == {"sync": [], "async": []}
