# Snapshot of the existing procurement_eval gateway; owned here for T67 lifecycle fixes.
# Original SHA-256: 888574da7217d99de035c28f30684e9b0bbe4046dfe1d5831cbee04a86252599
"""Local model gateway: one shared request budget across main/subagents and retries.

Reservations use request UTF-8 bytes plus framing allowance, and capped output.
This is a conservative configured estimate, never an actual provider invoice.
"""
from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from datetime import datetime, timezone
import hashlib


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(data):
    return hashlib.sha256(data).hexdigest()


class Budget:
    def __init__(self, spec: dict, log):
        self.spec, self.log = spec, log
        self.lock = threading.Lock()
        self.calls = 0
        self.reserved = Decimal(0)
        self.records = []
        self.denied_requests = 0
        self.requests_drained = False

    def reserve(self, size: int) -> dict:
        with self.lock:
            b = self.spec
            amount = (Decimal(size + 4096) * Decimal(str(b["input_cny_per_million"]))
                      + Decimal(b["max_output_tokens"]) * Decimal(str(b["output_cny_per_million"]))) / 1_000_000
            if (size > b["max_request_bytes"] or self.calls >= b["max_model_calls"]
                    or (b.get("enforce_cost_limit", True)
                        and self.reserved + amount > Decimal(str(b["per_attempt_cny"])))):
                self.denied_requests += 1
                raise ValueError("configured model budget exhausted")
            self.calls += 1
            self.reserved += amount
            row = {"call": self.calls, "started_at": now(), "reserved_upper_cny": float(amount)}
            self.records.append(row)
            # Preserve reservation before any network call, including crashes/unknown usage.
            self.log.write(json.dumps(row) + "\n")
            self.log.flush()
            return row

    def finish(self, row: dict, body: bytes, status: int) -> None:
        chunks = []
        if body.lstrip().startswith(b"{"):
            try:
                chunks = [json.loads(body)]
            except ValueError:
                pass
        else:
            for line in body.splitlines():
                if line.startswith(b"data: ") and line[6:] != b"[DONE]":
                    try:
                        chunks.append(json.loads(line[6:]))
                    except ValueError:
                        pass
        usage = next((x["usage"] for x in reversed(chunks) if x.get("usage")), None)
        model = next((x.get("model") for x in chunks if x.get("model")), None)
        with self.lock:
            row.update({"finished_at": now(), "http_status": status,
                        "observed_model": model, "usage": usage})
            # Release unused output reservation only after provider usage is known.
            # Unknown/failed requests retain their full reservation.
            if usage and all(type(usage.get(k)) is int and usage[k] >= 0 for k in ("prompt_tokens", "completion_tokens")):
                charged = (Decimal(usage["prompt_tokens"]) * Decimal(str(self.spec["input_cny_per_million"]))
                           + Decimal(usage["completion_tokens"]) * Decimal(str(self.spec["output_cny_per_million"]))) / 1_000_000
                original_reservation = Decimal(str(row["reserved_upper_cny"]))
                if charged <= original_reservation:
                    self.reserved += charged - original_reservation
                    row["settled_upper_estimate_cny"] = float(charged)
                else:
                    row["budget_control_violation"] = True
                    self.reserved += charged - original_reservation
            self.log.write(json.dumps(row) + "\n")
            self.log.flush()

    def summary(self) -> dict:
        with self.lock:
            complete = [r for r in self.records if r.get("usage")]
            inputs = sum(r["usage"].get("prompt_tokens", 0) for r in complete)
            outputs = sum(r["usage"].get("completion_tokens", 0) for r in complete)
            estimate = (inputs * self.spec["input_cny_per_million"] + outputs * self.spec["output_cny_per_million"]) / 1_000_000
            return {"model_calls": self.calls, "denied_model_requests": self.denied_requests,
                    "gateway_requests_drained": self.requests_drained,
                    "pending_model_requests": sum("finished_at" not in r for r in self.records),
                    "input_tokens": inputs if complete or self.calls == 0 else None,
                    "output_tokens": outputs if complete or self.calls == 0 else None,
                    "usage_complete": len(complete) == self.calls,
                    "cost_estimate_cny": estimate if complete or self.calls == 0 else None,
                    "actual_cost_cny": None, "reserved_upper_cny": float(self.reserved),
                    "pricing_kind": self.spec["pricing_kind"], "calls": list(self.records)}


@contextmanager
def gateway(model, spec: dict, log, extra_body=None):
    budget = Budget(spec, log)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            if self.path != "/v1/chat/completions":
                self.reply(403, b'{"error":{"message":"endpoint denied"}}')
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length <= 0 or length > spec["max_request_bytes"]:
                    raise ValueError("request too large/empty")
                payload = json.loads(self.rfile.read(length))
                if payload.get("model") != model.model_id:
                    raise ValueError("model identity mismatch")
                payload.update(extra_body or {})
                # Apply to every call, including calls from delegated graphs.
                payload["max_tokens"] = spec["max_output_tokens"]
                payload.pop("max_completion_tokens", None)
                if payload.get("stream"):
                    payload["stream_options"] = {"include_usage": True}
                data = json.dumps(payload, ensure_ascii=False).encode()
                row = budget.reserve(len(data))
                row["request_sha256"] = digest(data)
            except (ValueError, TypeError) as exc:
                self.reply(429, json.dumps({"error": {"message": str(exc), "type": "evaluation_budget"}}).encode())
                return
            upstream = Request(model.base_url.rstrip("/") + "/chat/completions", data=data,
                               headers={"Content-Type": "application/json", "Authorization": "Bearer " + model.api_key})
            try:
                with urlopen(upstream, timeout=min(spec["timeout_seconds"], 120)) as response:
                    body, status = response.read(), response.status
                    media = response.headers.get("Content-Type", "application/json")
            except HTTPError as exc:
                # Provider error text can contain credentials; do not forward/log it.
                body, status, media = b'{"error":{"message":"upstream rejected model request"}}', exc.code, "application/json"
            except Exception:
                body, status, media = b'{"error":{"message":"upstream unavailable"}}', 503, "application/json"
            budget.finish(row, body, status)
            self.reply(status, body, media)

        def reply(self, status, body, media="application/json"):
            self.send_response(status)
            self.send_header("Content-Type", media)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

    class DrainingServer(ThreadingHTTPServer):
        # server_close must join request threads while their usage ledger is open.
        daemon_threads = False

    server = DrainingServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1", budget
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        budget.requests_drained = True
