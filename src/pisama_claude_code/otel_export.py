"""OpenTelemetry export for pisama-claude-code traces.

Converts Claude Code traces to OpenTelemetry spans and exports them
to any OTEL-compatible backend (Jaeger, Honeycomb, Datadog, etc.).
"""

from __future__ import annotations

import json
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

# Lazy imports for OTEL
_otel_available = None


def is_otel_available() -> bool:
    """Check if OpenTelemetry is installed."""
    global _otel_available
    if _otel_available is None:
        try:
            import opentelemetry  # noqa: F401
            _otel_available = True
        except ImportError:
            _otel_available = False
    return _otel_available


def export_traces_to_otel(
    traces: List[Dict[str, Any]],
    endpoint: str,
    service_name: str = "claude-code",
    headers: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """Export traces to an OTEL endpoint.

    Args:
        traces: List of normalized trace dicts from pisama-claude-code
        endpoint: OTEL collector endpoint (e.g., http://localhost:4318/v1/traces)
        service_name: Service name for the spans
        headers: Optional headers (e.g., for authentication)

    Returns:
        Dict with export status and statistics
    """
    if not is_otel_available():
        raise ImportError(
            "OpenTelemetry not installed. Run: pip install pisama-claude-code[otel]"
        )

    from opentelemetry import trace as otel_trace
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
    from opentelemetry.trace import SpanKind

    # Set up resource
    resource = Resource.create({
        "service.name": service_name,
        "service.version": "0.3.7",
        "telemetry.sdk.name": "pisama-claude-code",
    })

    # Create provider and exporter
    provider = TracerProvider(resource=resource)
    exporter = OTLPSpanExporter(
        endpoint=endpoint,
        headers=headers or {},
    )
    provider.add_span_processor(BatchSpanProcessor(exporter))
    otel_trace.set_tracer_provider(provider)

    tracer = otel_trace.get_tracer("pisama-claude-code")

    # Group traces by session
    sessions: Dict[str, List[Dict]] = {}
    for t in traces:
        session_id = t.get("session_id", "unknown")
        if session_id not in sessions:
            sessions[session_id] = []
        sessions[session_id].append(t)

    spans_created = 0

    # Create spans for each session
    for session_id, session_traces in sessions.items():
        # Sort by timestamp
        session_traces.sort(key=lambda x: x.get("timestamp", ""))

        # Create parent span for session
        session_start = _parse_timestamp(session_traces[0].get("timestamp"))

        with tracer.start_span(
            name=f"claude-code-session:{session_id}",
            kind=SpanKind.INTERNAL,
            start_time=session_start,
        ) as session_span:
            session_span.set_attribute("session.id", session_id)
            session_span.set_attribute("session.trace_count", len(session_traces))

            # Calculate session totals
            total_input = sum(t.get("input_tokens", 0) for t in session_traces)
            total_output = sum(t.get("output_tokens", 0) for t in session_traces)
            total_cost = sum(t.get("cost_usd", 0) for t in session_traces)

            session_span.set_attribute("session.input_tokens", total_input)
            session_span.set_attribute("session.output_tokens", total_output)
            session_span.set_attribute("session.cost_usd", total_cost)

            spans_created += 1

            # Create child spans for each tool call
            for t in session_traces:
                span_start = _parse_timestamp(t.get("timestamp"))
                tool_name = t.get("tool_name", "unknown")
                hook_type = t.get("hook_type", "")

                with tracer.start_span(
                    name=f"{tool_name}:{hook_type}",
                    kind=SpanKind.INTERNAL,
                    start_time=span_start,
                ) as span:
                    # Standard attributes
                    span.set_attribute("tool.name", tool_name)
                    span.set_attribute("hook.type", hook_type)
                    span.set_attribute("session.id", session_id)

                    # Model and token usage (GenAI semantic conventions)
                    if t.get("model"):
                        span.set_attribute("gen_ai.system", "anthropic")
                        span.set_attribute("gen_ai.request.model", t["model"])

                    if t.get("input_tokens"):
                        span.set_attribute("gen_ai.usage.input_tokens", t["input_tokens"])
                    if t.get("output_tokens"):
                        span.set_attribute("gen_ai.usage.output_tokens", t["output_tokens"])
                    if t.get("cache_read_tokens"):
                        span.set_attribute("gen_ai.usage.cache_read_tokens", t["cache_read_tokens"])
                    if t.get("cost_usd"):
                        span.set_attribute("gen_ai.usage.cost_usd", t["cost_usd"])

                    # Content attributes (if captured)
                    if t.get("user_input"):
                        span.set_attribute("gen_ai.prompt", _truncate(t["user_input"], 4096))
                    if t.get("reasoning"):
                        span.set_attribute("gen_ai.reasoning", _truncate(t["reasoning"], 4096))
                    if t.get("ai_output"):
                        span.set_attribute("gen_ai.completion", _truncate(t["ai_output"], 4096))

                    # Tool input (sanitized)
                    tool_input = t.get("tool_input")
                    if tool_input:
                        try:
                            span.set_attribute("tool.input", json.dumps(tool_input)[:2048])
                        except (TypeError, ValueError):
                            pass

                    # Working directory
                    if t.get("working_dir"):
                        span.set_attribute("tool.working_dir", t["working_dir"])

                    spans_created += 1

    # Force flush and shutdown
    provider.force_flush()
    provider.shutdown()

    return {
        "success": True,
        "endpoint": endpoint,
        "service_name": service_name,
        "sessions_exported": len(sessions),
        "spans_created": spans_created,
        "traces_processed": len(traces),
    }


def convert_trace_to_otel_dict(trace: Dict[str, Any]) -> Dict[str, Any]:
    """Convert a single trace to OTEL span format (dict).

    Used both by the file export and by the real-time `emit_span` path. Carries:
    - `gen_ai.prompt` / `gen_ai.completion` so the backend populates the
      ParsedState prompt/response the detectors read.
    - `gen_ai.tool.name` so the subagent_spawn_loop membership check sees spawns.
    - `gen_ai.state`: the full content (user input, REASONING, output, tool I/O,
      cost) as JSON. The backend OTLP parser slurps a recognized state attribute
      verbatim into `state_delta`, which is the only lossless channel — it does
      not extract `gen_ai.reasoning` and truncates the prompt elsewhere.
    """
    timestamp_ns = _parse_timestamp(trace.get("timestamp"))
    tool_name = trace.get("tool_name") or ""

    attributes = [
        {"key": "tool.name", "value": {"stringValue": str(tool_name)}},
        {"key": "hook.type", "value": {"stringValue": str(trace.get("hook_type", ""))}},
        {"key": "session.id", "value": {"stringValue": str(trace.get("session_id", ""))}},
        {"key": "gen_ai.system", "value": {"stringValue": "anthropic"}},
        {"key": "gen_ai.request.model", "value": {"stringValue": str(trace.get("model") or "")}},
        {"key": "gen_ai.usage.input_tokens", "value": {"intValue": int(trace.get("input_tokens") or 0)}},
        {"key": "gen_ai.usage.output_tokens", "value": {"intValue": int(trace.get("output_tokens") or 0)}},
    ]
    if tool_name:
        attributes.append({"key": "gen_ai.tool.name", "value": {"stringValue": str(tool_name)}})
    if trace.get("user_input"):
        attributes.append({"key": "gen_ai.prompt", "value": {"stringValue": str(trace["user_input"])}})
    if trace.get("ai_output"):
        attributes.append({"key": "gen_ai.completion", "value": {"stringValue": str(trace["ai_output"])}})

    # Full content -> gen_ai.state (lossless channel; preserves reasoning).
    state: Dict[str, Any] = {}
    for key in ("user_input", "reasoning", "ai_output", "tool_input", "tool_output",
                "model", "cost_usd", "cache_read_tokens", "working_dir"):
        val = trace.get(key)
        if val not in (None, "", {}, []):
            state[key] = val
    tool_calls = trace.get("tool_calls")
    if tool_calls:
        state["tool_calls"] = tool_calls
    if state:
        try:
            attributes.append({
                "key": "gen_ai.state",
                "value": {"stringValue": json.dumps(state, ensure_ascii=False, default=str)},
            })
        except (TypeError, ValueError):
            pass

    return {
        "name": f"{tool_name or 'unknown'}:{trace.get('hook_type', '')}",
        "kind": "SPAN_KIND_INTERNAL",
        "startTimeUnixNano": timestamp_ns,
        "endTimeUnixNano": timestamp_ns + 1_000_000,  # +1ms
        "attributes": attributes,
        "traceId": _generate_trace_id(str(trace.get("session_id", ""))),
        # Unique per event so distinct tool calls never collide on span id.
        "spanId": _generate_span_id(
            f"{trace.get('session_id', '')}:{trace.get('timestamp', '')}:{tool_name}"
        ),
    }


def export_to_otel_file(
    traces: List[Dict[str, Any]],
    output_path: str,
    service_name: str = "claude-code",
) -> Dict[str, Any]:
    """Export traces to an OTEL-compatible JSON file.

    The output format follows the OTLP JSON schema.
    """
    resource_spans = []

    # Group by session
    sessions: Dict[str, List[Dict]] = {}
    for t in traces:
        session_id = t.get("session_id", "unknown")
        if session_id not in sessions:
            sessions[session_id] = []
        sessions[session_id].append(t)

    for session_id, session_traces in sessions.items():
        spans = [convert_trace_to_otel_dict(t) for t in session_traces]

        resource_spans.append({
            "resource": {
                "attributes": [
                    {"key": "service.name", "value": {"stringValue": service_name}},
                    {"key": "session.id", "value": {"stringValue": session_id}},
                ]
            },
            "scopeSpans": [
                {
                    "scope": {"name": "pisama-claude-code"},
                    "spans": spans,
                }
            ]
        })

    otel_payload = {"resourceSpans": resource_spans}

    with open(output_path, "w") as f:
        json.dump(otel_payload, f, indent=2)

    return {
        "success": True,
        "output_path": output_path,
        "sessions_exported": len(sessions),
        "spans_created": sum(len(s) for s in sessions.values()),
    }


def _parse_timestamp(ts: Optional[str]) -> int:
    """Parse timestamp string to nanoseconds since epoch."""
    if not ts:
        return time.time_ns()
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return int(dt.timestamp() * 1_000_000_000)
    except (ValueError, TypeError):
        return time.time_ns()


def _truncate(text: str, max_len: int) -> str:
    """Truncate text to max length."""
    if not text:
        return ""
    if len(text) > max_len:
        return text[:max_len - 3] + "..."
    return text


def _generate_trace_id(session_id: str) -> str:
    """Generate a 32-char hex trace ID from session ID."""
    import hashlib
    h = hashlib.sha256(session_id.encode()).hexdigest()
    return h[:32]


def _generate_span_id(identifier: str) -> str:
    """Generate a 16-char hex span ID."""
    import hashlib
    h = hashlib.sha256(identifier.encode()).hexdigest()
    return h[:16]
