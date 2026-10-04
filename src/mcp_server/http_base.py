"""HTTP access to the Java ERP, with the unified tool-result envelope.

Two rules shape this module:

* A failure is never turned into a success. A 4xx, a 5xx, a timeout or a broken
  connection all produce ``ok=false`` with a structured error; the gateway never
  fabricates an empty list to keep a caller happy.
* Retries are bounded and honest. Reads may be retried a couple of times with
  exponential backoff; writes are only retried as an idempotent resend of the
  same operation id and the same frozen bytes.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import httpx

from .config import McpSettings

LOGGER = logging.getLogger("rush_harness.mcp.erp")

RETRY_BACKOFF_SECONDS = 0.2


def ok_result(data: Any, request_id: str | None) -> dict[str, Any]:
    return {"ok": True, "data": data, "error": None, "request_id": request_id or ""}


def error_result(
    code: str,
    message: str,
    *,
    retryable: bool,
    details: Any = None,
    request_id: str | None = None,
) -> dict[str, Any]:
    return {
        "ok": False,
        "data": None,
        "error": {
            "code": code,
            "message": message,
            "retryable": retryable,
            "details": details if details is not None else {},
        },
        "request_id": request_id or "",
    }


class ErpClient:
    """Pooled ERP client. One instance lives for the lifetime of the gateway."""

    def __init__(self, settings: McpSettings, transport: httpx.AsyncBaseTransport | None = None):
        self._settings = settings
        self._transport = transport
        self._client: httpx.AsyncClient | None = None

    @property
    def is_running(self) -> bool:
        return self._client is not None

    async def start(self) -> None:
        if self._client is not None:
            return
        self._client = httpx.AsyncClient(
            base_url=self._settings.erp_base_url,
            timeout=httpx.Timeout(
                self._settings.read_timeout, connect=self._settings.connect_timeout
            ),
            transport=self._transport,
            headers={"Accept": "application/json"},
            # This is an internal service-to-service call. Honouring an ambient HTTP(S)_PROXY
            # would silently route ERP traffic through an unrelated proxy and turn a refused
            # connection into a bogus upstream error.
            trust_env=False,
        )

    async def aclose(self) -> None:
        client, self._client = self._client, None
        if client is not None:
            await client.aclose()

    def _attempts(self, *, method: str, idempotent_resend: bool) -> int:
        if method == "GET":
            return self._settings.read_retries + 1
        # A write may only be repeated when the caller is resending the same frozen bytes under
        # the same operation id, which the ERP recognises as a replay.
        return 2 if idempotent_resend else 1

    async def call(
        self,
        method: str,
        path: str,
        *,
        actor: str,
        params: dict[str, Any] | None = None,
        frozen_body: bytes | None = None,
        operation_id: str | None = None,
        idempotent_resend: bool = False,
    ) -> dict[str, Any]:
        """Perform one ERP call and return the tool-result envelope.

        Logs the interaction for evidence: actor, verb, path and outcome. Request
        bodies are never logged, and no credential is ever included.
        """
        result = await self._dispatch(
            method,
            path,
            actor=actor,
            params=params,
            frozen_body=frozen_body,
            operation_id=operation_id,
            idempotent_resend=idempotent_resend,
        )
        if result["ok"]:
            LOGGER.info(
                "erp actor=%s %s %s -> ok request_id=%s", actor, method, path, result["request_id"]
            )
        else:
            LOGGER.warning(
                "erp actor=%s %s %s -> %s (%s)",
                actor,
                method,
                path,
                result["error"]["code"],
                result["error"]["message"],
            )
        return result

    async def _dispatch(
        self,
        method: str,
        path: str,
        *,
        actor: str,
        params: dict[str, Any] | None = None,
        frozen_body: bytes | None = None,
        operation_id: str | None = None,
        idempotent_resend: bool = False,
    ) -> dict[str, Any]:
        if self._client is None:
            return error_result(
                "GATEWAY_NOT_READY",
                "the ERP client was used before the gateway finished starting",
                retryable=True,
            )

        # Headers are built per call. Mutating client defaults would let one user's identity leak
        # into another user's request.
        headers: dict[str, str] = {
            "X-Service-Token": self._settings.service_token,
            "X-Actor-Id": actor,
        }
        if operation_id is not None:
            headers["X-Operation-Id"] = operation_id
        if frozen_body is not None:
            headers["Content-Type"] = "application/json"

        attempts = self._attempts(method=method, idempotent_resend=idempotent_resend)
        deadline = time.monotonic() + self._settings.tool_budget
        last_failure: tuple[str, str, bool] = (
            "UPSTREAM_ERROR",
            "no attempt was made",
            True,
        )

        for attempt in range(attempts):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return error_result(
                    "TIMEOUT",
                    f"the tool budget of {self._settings.tool_budget:g}s was exhausted",
                    retryable=True,
                )
            try:
                response = await self._client.request(
                    method,
                    path,
                    params=params,
                    content=frozen_body,
                    headers=headers,
                    timeout=httpx.Timeout(min(self._settings.read_timeout, remaining)),
                )
            except httpx.TimeoutException as exc:
                last_failure = (
                    "TIMEOUT",
                    f"{method} {path} timed out: {type(exc).__name__}",
                    True,
                )
            except httpx.HTTPError as exc:
                last_failure = (
                    "NETWORK_ERROR",
                    f"{method} {path} could not be completed: {type(exc).__name__}: {exc}",
                    True,
                )
            else:
                if response.status_code < 400:
                    return self._success(response)
                if response.status_code < 500:
                    # Client errors are the ERP's verdict; retrying identical input cannot help.
                    return self._client_error(response)
                last_failure = (
                    "UPSTREAM_ERROR",
                    f"ERP returned {response.status_code}",
                    True,
                )

            if attempt + 1 < attempts:
                await asyncio.sleep(RETRY_BACKOFF_SECONDS * (2**attempt))

        return error_result(*last_failure[:2], retryable=last_failure[2])

    def _request_id(self, response: httpx.Response, body: Any) -> str:
        if isinstance(body, dict) and isinstance(body.get("request_id"), str):
            return body["request_id"]
        return response.headers.get("X-Request-Id", "")

    def _success(self, response: httpx.Response) -> dict[str, Any]:
        try:
            body = response.json()
        except ValueError:
            return error_result(
                "MALFORMED_RESPONSE",
                "ERP returned a non-JSON success body",
                retryable=False,
                request_id=response.headers.get("X-Request-Id", ""),
            )
        if not isinstance(body, dict) or "data" not in body:
            return error_result(
                "MALFORMED_RESPONSE",
                "ERP success body has no 'data' field",
                retryable=False,
                request_id=response.headers.get("X-Request-Id", ""),
            )
        return ok_result(body["data"], self._request_id(response, body))

    def _client_error(self, response: httpx.Response) -> dict[str, Any]:
        try:
            body = response.json()
        except ValueError:
            return error_result(
                "UPSTREAM_ERROR",
                f"ERP returned {response.status_code} without a structured error",
                retryable=False,
                request_id=response.headers.get("X-Request-Id", ""),
            )
        error = body.get("error") if isinstance(body, dict) else None
        if not isinstance(error, dict):
            return error_result(
                "UPSTREAM_ERROR",
                f"ERP returned {response.status_code} without a structured error",
                retryable=False,
                request_id=response.headers.get("X-Request-Id", ""),
            )
        retryable = response.status_code in {408, 429}
        return error_result(
            str(error.get("code", "UPSTREAM_ERROR")),
            str(error.get("message", "")),
            retryable=retryable,
            details=error.get("details"),
            request_id=self._request_id(response, body),
        )
