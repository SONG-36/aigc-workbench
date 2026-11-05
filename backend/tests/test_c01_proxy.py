"""Real urllib routing/error handlers over an injected HTTPS connection; no network or paid API."""

import base64
from email.message import Message
from io import BytesIO
import json
import socket
import ssl
import urllib.error

import pytest

from aigc_workbench.c01_cli import main
from aigc_workbench.c01_context import C01Error, canonical
from aigc_workbench.c01_model import MODEL, OpenAIResponsesTransport


@pytest.fixture(autouse=True)
def offline_environment(monkeypatch, tmp_path):
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
                 "http_proxy", "https_proxy", "all_proxy", "no_proxy", "OPENAI_BASE_URL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-openai-key")
    monkeypatch.setattr("aigc_workbench.config.BACKEND_ENV_FILE", tmp_path / "no-user-credential")
    def forbidden(*args, **kwargs): raise AssertionError("network forbidden in proxy tests")
    monkeypatch.setattr(socket, "create_connection", forbidden)


@pytest.fixture
def wire(monkeypatch):
    state = {"connections": [], "requests": [], "status": 200, "body": b'{}', "headers": {}, "failure": None}

    class Response:
        def __init__(self):
            self.status = self.code = state["status"]
            self.reason = "synthetic HTTP response"
            self.headers = Message()
            for key, value in state["headers"].items(): self.headers[key] = value
            self.stream = BytesIO(state["body"])
        def read(self, size): return self.stream.read(size)
        def close(self): self.stream.close()
        def info(self): return self.headers

    class Connection:
        debuglevel = 0
        def __init__(self, host, timeout, context):
            self.sock = None
            self.record = {"host": host, "timeout": timeout, "context": context, "tunnel": None}
            state["connections"].append(self.record)
        def set_debuglevel(self, level): assert level == 0
        def set_tunnel(self, host, port=None, headers=None):
            self.record["tunnel"] = {"host": host, "headers": headers}
        def request(self, method, path, body, headers, encode_chunked=False):
            state["requests"].append({"method": method, "path": path, "body": body, "headers": headers})
            if state["failure"]: raise state["failure"]
        def getresponse(self): return Response()
        def close(self): pass

    monkeypatch.setattr("http.client.HTTPSConnection", Connection)
    return state


def test_https_proxy_connect_preserves_exact_post_bytes_and_verified_tls(monkeypatch, wire):
    monkeypatch.setenv("HTTPS_PROXY", "http://expected-proxy.invalid:8118")
    monkeypatch.setenv("ALL_PROXY", "socks5://not-selected.invalid:1080")
    payload = canonical({"input": "原四层文字", "image": "data:image/jpeg;base64,synthetic"}).encode()
    response = OpenAIResponsesTransport().send(payload)
    assert response.status == 200
    assert len(wire["connections"]) == len(wire["requests"]) == 1
    connection, request = wire["connections"][0], wire["requests"][0]
    assert connection["host"] == "expected-proxy.invalid:8118"
    assert connection["tunnel"]["host"] == "api.openai.com"
    assert connection["context"].verify_mode == ssl.CERT_REQUIRED
    assert connection["context"].check_hostname is True
    assert request["path"] == "/v1/responses" and request["method"] == "POST"
    assert request["body"] == payload
    assert request["headers"]["Authorization"] == "Bearer synthetic-openai-key"


@pytest.mark.parametrize("no_proxy", ["api.openai.com", ".openai.com", "*", "api.openai.com:443"])
def test_no_proxy_is_explicit_direct(monkeypatch, wire, no_proxy):
    monkeypatch.setenv("HTTPS_PROXY", "http://unused-proxy.invalid:8118")
    monkeypatch.setenv("NO_PROXY", no_proxy)
    OpenAIResponsesTransport().send(b"unchanged")
    assert wire["connections"][0]["host"] == "api.openai.com"
    assert wire["connections"][0]["tunnel"] is None


def test_unrelated_no_proxy_does_not_bypass_expected_proxy(monkeypatch, wire):
    monkeypatch.setenv("HTTPS_PROXY", "http://expected-proxy.invalid:8118")
    monkeypatch.setenv("NO_PROXY", "localhost,.example.com")
    OpenAIResponsesTransport().send(b"unchanged")
    assert wire["connections"][0]["host"] == "expected-proxy.invalid:8118"


def test_no_proxy_configuration_is_direct_without_system_discovery(monkeypatch, wire):
    def forbidden(): raise AssertionError("must not discover hidden system proxies")
    monkeypatch.setattr("urllib.request.getproxies", forbidden)
    OpenAIResponsesTransport().send(b"unchanged")
    assert wire["connections"][0]["host"] == "api.openai.com"


def test_all_proxy_http_fallback_and_lowercase_precedence(monkeypatch, wire):
    monkeypatch.setenv("ALL_PROXY", "http://fallback-proxy.invalid:8118")
    OpenAIResponsesTransport().send(b"first")
    assert wire["connections"][-1]["host"] == "fallback-proxy.invalid:8118"
    monkeypatch.setenv("HTTPS_PROXY", "http://upper-proxy.invalid:8118")
    monkeypatch.setenv("https_proxy", "http://lower-proxy.invalid:8118")
    OpenAIResponsesTransport().send(b"second")
    assert wire["connections"][-1]["host"] == "lower-proxy.invalid:8118"


@pytest.mark.parametrize("proxy,code", [
    ("socks5://proxy.invalid:1080", "MODEL_PROXY_SCHEME_UNSUPPORTED"),
    ("https://proxy.invalid:8443", "MODEL_PROXY_SCHEME_UNSUPPORTED"),
    ("http://proxy.invalid:bad", "MODEL_PROXY_INVALID"),
    ("http://user:synthetic-password@", "MODEL_PROXY_INVALID"),
    ("http://user@proxy.invalid:8118", "MODEL_PROXY_INVALID"),
])
def test_invalid_or_unsupported_proxy_never_falls_back(monkeypatch, wire, proxy, code):
    monkeypatch.setenv("ALL_PROXY", proxy)
    with pytest.raises(C01Error) as exc: OpenAIResponsesTransport().send(b"not-sent")
    assert exc.value.code == code
    assert "synthetic-password" not in str(exc.value)
    assert wire["connections"] == wire["requests"] == []


@pytest.mark.parametrize("failure,code", [
    (ConnectionRefusedError("http://user:synthetic-password@proxy.invalid"), "MODEL_PROXY_CONNECTION_FAILED"),
    (TimeoutError("synthetic-openai-key"), "MODEL_NETWORK_TIMEOUT"),
    (ssl.SSLCertVerificationError("untrusted certificate synthetic-password"), "MODEL_TLS_FAILED"),
])
def test_proxy_timeout_tls_failures_are_safe_and_never_retry(monkeypatch, wire, failure, code):
    monkeypatch.setenv("HTTPS_PROXY", "http://expected-proxy.invalid:8118")
    wire["failure"] = failure
    with pytest.raises(C01Error) as exc: OpenAIResponsesTransport().send(b"one-paid-request-intent")
    assert exc.value.code == code
    assert "synthetic" not in json.dumps(exc.value.record())
    assert len(wire["connections"]) == len(wire["requests"]) == 1
    assert wire["connections"][0]["host"] == "expected-proxy.invalid:8118"


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
def test_no_redirect_or_second_post_even_for_same_api_host(monkeypatch, wire, status):
    monkeypatch.setenv("HTTPS_PROXY", "http://expected-proxy.invalid:8118")
    wire["status"] = status
    wire["headers"]["Location"] = "https://different.invalid/leak"
    response = OpenAIResponsesTransport().send(b"fixed-body")
    assert response.status == status
    assert len(wire["requests"]) == 1
    wire["headers"]["Location"] = "https://api.openai.com/v1/responses"
    response = OpenAIResponsesTransport().send(b"another-explicit-test")
    assert response.status == status and len(wire["requests"]) == 2


@pytest.mark.parametrize("status", [401, 403, 407, 429, 500])
def test_http_error_status_and_raw_body_return_without_retry(wire, status):
    wire["status"] = status
    wire["body"] = b'{"error":{"message":"synthetic HTTP error"}}'
    result = OpenAIResponsesTransport().send(b"one-request")
    assert result.status == status and result.body == wire["body"]
    assert len(wire["requests"]) == 1


@pytest.mark.parametrize("password", ["synthetic-proxy-password", 'synthetic-proxy-"password'])
def test_proxy_auth_only_in_connect_and_response_credentials_redacted(monkeypatch, wire, capsys, password):
    from urllib.parse import quote
    username = "synthetic-proxy-user"
    proxy = f"http://{username}:{quote(password, safe='')}@proxy.invalid:8118"
    monkeypatch.setenv("HTTPS_PROXY", proxy)
    encoded = base64.b64encode(f"{username}:{password}".encode()).decode()
    wire["status"] = 401
    wire["body"] = json.dumps({"error": ["synthetic-openai-key", proxy, username, password, encoded]}).encode()
    wire["headers"]["x-request-id"] = "synthetic-openai-key"
    result = OpenAIResponsesTransport().send(b"fixed-body")
    assert wire["connections"][0]["tunnel"]["headers"]["Proxy-Authorization"] == "Basic " + encoded
    assert "Proxy-Authorization" not in wire["requests"][0]["headers"]
    for value in ["synthetic-openai-key", proxy, username, password, encoded]:
        assert value.encode() not in result.body
        assert json.dumps(value)[1:-1].encode() not in result.body
        assert value not in result.request_id
    assert capsys.readouterr().out == ""


def test_model_get_uses_same_client_proxy_tls_timeout_and_fixed_target(monkeypatch, wire):
    monkeypatch.setenv("HTTPS_PROXY", "http://expected-proxy.invalid:8118")
    wire["body"] = json.dumps({"id": MODEL}).encode()
    result = OpenAIResponsesTransport().check_model_access(MODEL)
    assert result["classification"] == "MODEL_METADATA_ACCESSIBLE" and result["http_status"] == 200
    assert result["network_route"] == "HTTPS_PROXY" and result["inference_calls"] == 0
    request, connection = wire["requests"][0], wire["connections"][0]
    assert request["method"] == "GET" and request["path"] == "/v1/models/" + MODEL
    assert request["body"] is None and connection["host"] == "expected-proxy.invalid:8118"
    assert connection["timeout"] == 60 and connection["context"].check_hostname
    with pytest.raises(C01Error): OpenAIResponsesTransport()._request("GET", "//different.invalid", None)
    assert len(wire["requests"]) == 1


def test_metadata_cli_needs_no_business_database_and_prints_no_key(monkeypatch, wire, tmp_path, capsys):
    monkeypatch.setenv("AIGC_ENV", "development")
    monkeypatch.setenv("AIGC_ENABLE_C01_TESTING", "1")
    monkeypatch.setenv("AIGC_DATA_DIR", str(tmp_path / "nonexistent"))
    wire["body"] = json.dumps({"id": MODEL}).encode()
    assert main(["check-model-access", "--model", MODEL]) == 0
    output = capsys.readouterr()
    assert "synthetic-openai-key" not in output.out and output.err == ""
    assert json.loads(output.out)["classification"] == "MODEL_METADATA_ACCESSIBLE"
    assert not (tmp_path / "nonexistent").exists()
