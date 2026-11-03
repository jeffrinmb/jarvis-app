"""Sending the export to an OTLP/HTTP collector (P2.3b)."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from jarvis.cli import main
from jarvis.core.ledger import Ledger
from jarvis.core.observability import OtlpSendError, normalize_endpoint, send_export
from jarvis.core.tokens import model_call_event


class Collector:
    """A local stand-in for a collector that records what it was sent."""

    def __init__(self):
        self.requests = []
        self.status = 200
        self.answer = b"{}"
        self.redirect_to = None
        self.redirect_code = 307
        self.hits = []  # every request of any method, to see whether a redirect was followed
        collector = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                collector.hits.append(("GET", self.path))
                self.send_response(200)
                self.send_header("Content-Length", "2")
                self.end_headers()
                self.wfile.write(b"{}")

            def do_POST(self):
                collector.hits.append(("POST", self.path))
                length = int(self.headers.get("Content-Length", 0))
                collector.requests.append(
                    (self.path, self.headers.get("Content-Type"), self.rfile.read(length))
                )
                if collector.redirect_to and self.path == "/v1/traces":
                    self.send_response(collector.redirect_code)
                    self.send_header("Location", collector.redirect_to)
                    self.end_headers()
                    return
                self.send_response(collector.status)
                self.send_header("Content-Length", str(len(collector.answer)))
                self.end_headers()
                self.wfile.write(collector.answer)

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def url(self):
        return f"http://127.0.0.1:{self.server.server_address[1]}"

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def collector():
    found = Collector()
    yield found
    found.close()


DOC = {"resourceSpans": [{"scopeSpans": [{"spans": []}]}]}


def ledger_with_a_call(tmp_path):
    ledger = Ledger(tmp_path / "events.jsonl")
    ledger.append(
        model_call_event(
            agent_id="a", on_behalf_of="alice", model="m1", tokens_in=1, tokens_out=1, cost_usd=0
        )
    )
    return ledger


@pytest.mark.parametrize(
    "endpoint, expected",
    [
        ("http://localhost:4318", "http://localhost:4318/v1/traces"),
        ("http://127.0.0.1:4318/", "http://127.0.0.1:4318/v1/traces"),
        ("http://[::1]:4318/custom", "http://[::1]:4318/custom"),
        ("https://otel.example.com", "https://otel.example.com/v1/traces"),
        ("https://otel.example.com/v1/traces", "https://otel.example.com/v1/traces"),
    ],
)
def test_endpoints_that_are_accepted(endpoint, expected):
    assert normalize_endpoint(endpoint) == expected


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://otel.example.com",  # plain http to another host
        "http://localhost.example.com",  # a look-alike of localhost
        "file:///etc/passwd",
        "ftp://localhost/x",
        "otel.example.com",
        "https://",
        "https://user:pass@otel.example.com",
        "https://otel.example.com/v1/traces?token=abc",
    ],
)
def test_endpoints_that_are_refused(endpoint):
    with pytest.raises(ValueError):
        normalize_endpoint(endpoint)


def test_the_document_is_posted_as_json_to_the_traces_path(collector):
    result = send_export(DOC, collector.url)
    ((path, content_type, body),) = collector.requests
    assert path == "/v1/traces"
    assert content_type == "application/json"
    assert json.loads(body) == DOC
    assert (result.status, result.rejected_spans) == (200, 0)


def test_rejected_spans_reported_by_the_collector_are_counted(collector):
    collector.answer = json.dumps({"partialSuccess": {"rejectedSpans": "3"}}).encode()
    assert send_export(DOC, collector.url).rejected_spans == 3


def test_an_unreadable_answer_is_not_an_error(collector):
    collector.answer = b"not json"
    assert send_export(DOC, collector.url).rejected_spans == 0


def test_an_error_status_is_reported_without_the_response_body(collector):
    collector.status = 500
    collector.answer = b"SECRET BODY FROM THE SERVER"
    with pytest.raises(OtlpSendError) as caught:
        send_export(DOC, collector.url)
    assert "HTTP 500" in str(caught.value)
    assert "SECRET" not in str(caught.value)


@pytest.mark.parametrize("code", [301, 302, 303, 307, 308])
def test_a_redirect_is_refused_and_not_followed(collector, code):
    collector.redirect_to = f"{collector.url}/elsewhere"
    collector.redirect_code = code
    with pytest.raises(OtlpSendError, match="redirected"):
        send_export(DOC, collector.url)
    assert collector.hits == [("POST", "/v1/traces")]  # nothing was requested at /elsewhere


def test_an_unreachable_collector_is_an_error_not_a_hang(collector):
    url = collector.url
    collector.close()
    with pytest.raises(OtlpSendError, match="could not reach"):
        send_export(DOC, url, timeout=2)


def test_cli_writes_the_file_and_sends_to_the_collector(tmp_path, collector, capsys):
    ledger = ledger_with_a_call(tmp_path)
    out = tmp_path / "traces.json"
    argv = ["otel", "export", "--ledger", str(ledger.path), "--out", str(out)]
    assert main([*argv, "--endpoint", collector.url]) == 0
    assert "HTTP 200" in capsys.readouterr().out
    assert json.loads(out.read_text(encoding="utf-8")) == json.loads(collector.requests[0][2])


def test_cli_can_send_without_writing_a_file(tmp_path, collector):
    ledger = ledger_with_a_call(tmp_path)
    assert main(["otel", "export", "--ledger", str(ledger.path), "--endpoint", collector.url]) == 0
    assert len(collector.requests) == 1


def test_cli_needs_somewhere_to_put_the_export(tmp_path):
    ledger = ledger_with_a_call(tmp_path)
    assert main(["otel", "export", "--ledger", str(ledger.path)]) == 2


def test_cli_refuses_a_bad_endpoint_before_reading_or_sending_anything(tmp_path, capsys):
    ledger = ledger_with_a_call(tmp_path)
    argv = ["otel", "export", "--ledger", str(ledger.path), "--endpoint", "http://example.com"]
    assert main(argv) == 2
    assert "invalid endpoint" in capsys.readouterr().err


def test_cli_fails_when_the_collector_is_down_but_keeps_the_file(tmp_path, collector, capsys):
    ledger = ledger_with_a_call(tmp_path)
    url = collector.url
    collector.close()
    out = tmp_path / "traces.json"
    argv = ["otel", "export", "--ledger", str(ledger.path), "--out", str(out), "--endpoint", url]
    assert main(argv) == 1
    assert out.exists()
    assert "not sent" in capsys.readouterr().err


def test_cli_fails_when_the_collector_rejects_spans(tmp_path, collector, capsys):
    collector.answer = json.dumps({"partialSuccess": {"rejectedSpans": 2}}).encode()
    ledger = ledger_with_a_call(tmp_path)
    assert main(["otel", "export", "--ledger", str(ledger.path), "--endpoint", collector.url]) == 1
    assert "2 rejected" in capsys.readouterr().out


def test_cli_sends_nothing_from_a_broken_ledger(tmp_path, collector):
    ledger = ledger_with_a_call(tmp_path)
    ledger.path.write_text(ledger.path.read_text(encoding="utf-8").replace("m1", "m2", 1))
    assert main(["otel", "export", "--ledger", str(ledger.path), "--endpoint", collector.url]) == 1
    assert collector.requests == []
