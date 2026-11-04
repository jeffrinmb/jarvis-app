"""Send an export to an OpenTelemetry collector over OTLP/HTTP with JSON (task P2.3b).

The body is exactly what ``otel.export_records`` produces, which holds no prompt, answer, path
or reason text. Sending still moves session, task and tool names and rule ids off the machine,
so it only happens when a person gives an endpoint, and the rules below fail closed:

* the endpoint must be ``https``, except for the machine itself (``localhost``, ``127.0.0.1``,
  ``[::1]``), so nothing travels in the clear to another host;
* no credentials are read or sent, so an endpoint that needs a token is not supported yet;
* redirects are refused, so a collector cannot bounce the body somewhere else;
* proxies named in the environment (``HTTP_PROXY`` and the like) are ignored, so the body goes to
  the endpoint given and nowhere else;
* the response body is never printed or stored, only the status and the count of spans the
  collector reports as rejected.

The whole ledger is sent each time and there is no cursor, so a backend that does not merge spans
with the same ids will show them twice if you send twice.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
DEFAULT_PATH = "/v1/traces"
DEFAULT_TIMEOUT = 10.0
MAX_RESPONSE = 64 * 1024


class OtlpSendError(Exception):
    """The export was not delivered. The message never contains a response body."""


@dataclass(frozen=True)
class SendResult:
    url: str
    status: int
    rejected_spans: int


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None  # the HTTPError that results is reported as a refusal


def normalize_endpoint(endpoint: str) -> str:
    """The URL to post to. Raises ``ValueError`` for anything this module will not send to."""
    parts = urlsplit(endpoint)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError("the endpoint must be an http(s) URL with a host")
    if parts.username or parts.password:
        raise ValueError("credentials in the endpoint URL are not supported")
    if parts.scheme == "http" and parts.hostname.lower() not in LOCAL_HOSTS:
        raise ValueError("plain http is only allowed for localhost; use https for other hosts")
    if parts.query or parts.fragment:
        raise ValueError("the endpoint must not have a query or fragment")
    path = parts.path if parts.path not in ("", "/") else DEFAULT_PATH
    return parts._replace(path=path).geturl()


def _rejected(body: bytes) -> int:
    try:
        partial = json.loads(body).get("partialSuccess") or {}
        return max(int(partial.get("rejectedSpans") or 0), 0)
    except (ValueError, AttributeError, TypeError):
        return 0


def send_export(
    document: dict[str, Any], endpoint: str, timeout: float = DEFAULT_TIMEOUT
) -> SendResult:
    url = normalize_endpoint(endpoint)
    body = json.dumps(document, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url, data=body, method="POST", headers={"Content-Type": "application/json"}
    )
    # An empty ProxyHandler: urllib otherwise reads HTTP_PROXY from the environment, which would
    # hand the body to that proxy in the clear (even for "localhost") and report the proxy's
    # answer as the collector's. The rules above are about where the bytes go.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect)
    try:
        with opener.open(request, timeout=timeout) as response:
            status = response.status
            answer = response.read(MAX_RESPONSE)
    except urllib.error.HTTPError as exc:
        kind = "redirected (refused)" if 300 <= exc.code < 400 else "rejected it"
        raise OtlpSendError(f"the collector {kind}: HTTP {exc.code}") from None
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        raise OtlpSendError(f"could not reach the collector ({type(reason).__name__})") from None
    return SendResult(url=url, status=status, rejected_spans=_rejected(answer))
