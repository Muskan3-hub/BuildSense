"""
BuildSense — Prometheus Metrics Instrumentation
Provides custom metrics for HTTP requests, latency, active in-flight requests,
AI agent execution (Gemini & Groq), and token consumption.

Includes graceful fallback if `prometheus-client` package is not installed.
"""

import time

try:
    from prometheus_client import (
        Counter as _Counter,
        Histogram as _Histogram,
        Gauge as _Gauge,
        generate_latest as _generate_latest,
        CONTENT_TYPE_LATEST as _CONTENT_TYPE_LATEST,
    )
    PROMETHEUS_AVAILABLE = True
except ImportError:
    PROMETHEUS_AVAILABLE = False
    _CONTENT_TYPE_LATEST = "text/plain; version=0.0.4; charset=utf-8"

    class _DummyMetric:
        def __init__(self, *args, **kwargs):
            pass
        def labels(self, *args, **kwargs):
            return self
        def inc(self, *args, **kwargs):
            pass
        def dec(self, *args, **kwargs):
            pass
        def observe(self, *args, **kwargs):
            pass

    _Counter = _Histogram = _Gauge = _DummyMetric

    def _generate_latest():
        return b"# prometheus_client package not installed\n"

CONTENT_TYPE_LATEST = _CONTENT_TYPE_LATEST


def generate_latest():
    return _generate_latest()


if PROMETHEUS_AVAILABLE:
    # ── HTTP Request Metrics ──────────────────────────────────────────────────
    HTTP_REQUESTS_TOTAL = _Counter(
        "http_requests_total",
        "Total HTTP requests handled by BuildSense",
        ["endpoint", "method", "status"]
    )

    HTTP_REQUEST_DURATION_SECONDS = _Histogram(
        "http_request_duration_seconds",
        "HTTP request latency in seconds",
        ["endpoint"],
        buckets=[0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0]
    )

    ACTIVE_REQUESTS_IN_FLIGHT = _Gauge(
        "active_requests_in_flight",
        "Number of active concurrent requests currently being processed",
        ["endpoint"]
    )

    # ── AI Agent Metrics ──────────────────────────────────────────────────────
    AI_AGENT_CALLS_TOTAL = _Counter(
        "ai_agent_calls_total",
        "Total LLM and multi-agent completion calls",
        ["provider", "model", "key_alias", "status"]
    )

    AI_AGENT_CALL_DURATION_SECONDS = _Histogram(
        "ai_agent_call_duration_seconds",
        "Duration of LLM / AI Agent executions in seconds",
        ["provider", "model"],
        buckets=[0.1, 0.5, 1.0, 2.5, 5.0, 10.0, 20.0, 45.0, 60.0]
    )

    AI_AGENT_TOKENS_TOTAL = _Counter(
        "ai_agent_tokens_total",
        "Total token consumption across prompt, completion, and total tokens",
        ["provider", "model", "key_alias", "token_type"]
    )
else:
    HTTP_REQUESTS_TOTAL = _DummyMetric()
    HTTP_REQUEST_DURATION_SECONDS = _DummyMetric()
    ACTIVE_REQUESTS_IN_FLIGHT = _DummyMetric()
    AI_AGENT_CALLS_TOTAL = _DummyMetric()
    AI_AGENT_CALL_DURATION_SECONDS = _DummyMetric()
    AI_AGENT_TOKENS_TOTAL = _DummyMetric()


def record_agent_call(provider: str, model: str, duration_sec: float, key_alias: str = "default", status: str = "200"):
    """Helper to record duration and status count for AI agent calls."""
    if not PROMETHEUS_AVAILABLE:
        return
    AI_AGENT_CALLS_TOTAL.labels(provider=provider, model=model, key_alias=key_alias, status=status).inc()
    AI_AGENT_CALL_DURATION_SECONDS.labels(provider=provider, model=model).observe(duration_sec)


def record_agent_tokens(provider: str, model: str, key_alias: str = "default", prompt_tokens: int = 0, completion_tokens: int = 0):
    """Helper to record prompt, completion, and total token usage for AI agent calls."""
    if not PROMETHEUS_AVAILABLE:
        return
    prompt_cnt = max(0, int(prompt_tokens or 0))
    completion_cnt = max(0, int(completion_tokens or 0))
    total_cnt = prompt_cnt + completion_cnt

    if prompt_cnt > 0:
        AI_AGENT_TOKENS_TOTAL.labels(provider=provider, model=model, key_alias=key_alias, token_type="prompt").inc(prompt_cnt)
    if completion_cnt > 0:
        AI_AGENT_TOKENS_TOTAL.labels(provider=provider, model=model, key_alias=key_alias, token_type="completion").inc(completion_cnt)
    if total_cnt > 0:
        AI_AGENT_TOKENS_TOTAL.labels(provider=provider, model=model, key_alias=key_alias, token_type="total").inc(total_cnt)
