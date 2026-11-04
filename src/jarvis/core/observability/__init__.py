"""Observability export (task P2.3, ADR 0011).

The ids are needed on every hook call, but the exporter and the OTLP sender are not, and the
sender pulls in ``urllib``, ``http``, ``socket`` and ``ssl``. Those names are loaded on first use
(PEP 562) so importing this package for an id costs almost nothing.
"""

from __future__ import annotations

import importlib
from typing import Any

from .ids import agent_span_id_for, span_id_for, trace_id_for

_LAZY = {
    "EXPORTED_KINDS": "otel",
    "SEMCONV_COMMIT": "otel",
    "SEMCONV_REPO": "otel",
    "ExportResult": "otel",
    "export_records": "otel",
    "write_export": "otel",
    "OtlpSendError": "otlp_http",
    "SendResult": "otlp_http",
    "normalize_endpoint": "otlp_http",
    "send_export": "otlp_http",
}

__all__ = [
    "EXPORTED_KINDS",
    "SEMCONV_COMMIT",
    "SEMCONV_REPO",
    "ExportResult",
    "OtlpSendError",
    "SendResult",
    "normalize_endpoint",
    "send_export",
    "export_records",
    "agent_span_id_for",
    "span_id_for",
    "trace_id_for",
    "write_export",
]


def __getattr__(name: str) -> Any:
    module = _LAZY.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(importlib.import_module(f"{__name__}.{module}"), name)
    globals()[name] = value
    return value
