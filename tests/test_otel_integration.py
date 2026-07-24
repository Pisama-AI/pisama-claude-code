"""OpenTelemetry export integration tests over a real loopback HTTP connection."""

from __future__ import annotations

import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
    ExportTraceServiceRequest,
)

from pisama_claude_code import __version__
from pisama_claude_code.otel_export import export_traces_to_otel


class _OtlpRecorder(BaseHTTPRequestHandler):
    payloads: list[bytes]

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        length = int(self.headers["Content-Length"])
        self.payloads.append(self.rfile.read(length))
        self.send_response(200)
        self.end_headers()

    def log_message(self, format: str, *args: object) -> None:
        return


@contextmanager
def _running_otlp_recorder():
    payloads: list[bytes] = []
    handler = type("OtlpRecorder", (_OtlpRecorder,), {"payloads": payloads})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        yield f"http://{host}:{port}/v1/traces", payloads
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _captured_session() -> list[dict]:
    return [
        {
            "session_id": "session-real-export",
            "timestamp": "2026-07-23T12:00:00+00:00",
            "hook_type": "PostToolUse",
            "tool_name": "Read",
            "tool_input": {"file_path": "/workspace/README.md"},
            "model": "claude-sonnet-4-6",
            "input_tokens": 14,
            "output_tokens": 8,
        },
        {
            "session_id": "session-real-export",
            "timestamp": "2026-07-23T12:00:01+00:00",
            "hook_type": "PostToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "git status --short"},
            "model": "claude-sonnet-4-6",
            "input_tokens": 18,
            "output_tokens": 5,
        },
    ]


def _decode_single_export(payloads: list[bytes]) -> ExportTraceServiceRequest:
    assert len(payloads) == 1
    request = ExportTraceServiceRequest()
    request.ParseFromString(payloads[0])
    return request


def test_repeated_exports_are_isolated_and_preserve_span_parentage():
    """Each call owns its provider and child spans belong to the session span."""
    with _running_otlp_recorder() as (first_endpoint, first_payloads):
        first = export_traces_to_otel(
            _captured_session(),
            endpoint=first_endpoint,
            service_name="claude-code-integration",
        )
    with _running_otlp_recorder() as (second_endpoint, second_payloads):
        second = export_traces_to_otel(
            _captured_session(),
            endpoint=second_endpoint,
            service_name="claude-code-integration",
        )

    assert first["spans_created"] == second["spans_created"] == 3
    for request in (
        _decode_single_export(first_payloads),
        _decode_single_export(second_payloads),
    ):
        resource_spans = request.resource_spans[0]
        resource = {
            item.key: item.value.string_value for item in resource_spans.resource.attributes
        }
        assert resource["service.version"] == __version__

        spans = list(resource_spans.scope_spans[0].spans)
        session_span = next(span for span in spans if span.name.startswith("claude-code-session:"))
        tool_spans = [span for span in spans if span is not session_span]
        assert session_span.parent_span_id == b""
        assert {span.parent_span_id for span in tool_spans} == {session_span.span_id}
