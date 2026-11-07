"""Allowlist CONNECT proxy. No internet: DNS is injected and upstream is local."""

from __future__ import annotations

import socket
import threading

import pytest

from challenge_runner.sandbox import proxy as proxy_mod
from challenge_runner.sandbox.proxy import ProxyServer, is_blocked_ip, parse_args


@pytest.fixture
def serve():
    servers: list[ProxyServer] = []

    def start(allow: list[str], resolver):
        server = ProxyServer(("127.0.0.1", 0), allow, resolver=resolver)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        servers.append((server, thread))
        return server

    yield start
    for server, _thread in servers:
        server.shutdown()
        server.server_close()


def _exchange(port: int, payload: bytes) -> bytes:
    with socket.create_connection(("127.0.0.1", port), timeout=2) as sock:
        sock.settimeout(2)
        sock.sendall(payload)
        chunks: list[bytes] = []
        while True:
            try:
                data = sock.recv(4096)
            except socket.timeout:
                break
            if not data:
                break
            chunks.append(data)
        return b"".join(chunks)


def _connect(host: str, port: int = 443) -> bytes:
    return (f"CONNECT {host}:{port} HTTP/1.1\r\nHost: {host}:{port}\r\n\r\n").encode()


def test_blocked_addresses_include_metadata_private_and_loopback():
    for ip in (
        "169.254.169.254",
        "10.1.2.3",
        "127.0.0.1",
        "192.168.0.9",
        "169.254.1.10",
        "100.64.1.1",
        "0.0.0.0",
        "::1",
        "fe80::1",
        "::ffff:169.254.169.254",
    ):
        assert is_blocked_ip(ip)
    assert not is_blocked_ip("8.8.8.8")


def test_allows_exact_allowlisted_host_on_443_via_injected_resolver(serve, monkeypatch):
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    echo_port = listener.getsockname()[1]
    seen: list[tuple] = []

    def echo() -> None:
        conn, _addr = listener.accept()
        conn.settimeout(2)
        data = b""
        while b"hello" not in data:
            chunk = conn.recv(1024)
            if not chunk:
                break
            data += chunk
        conn.sendall(b"echo:" + data)
        conn.close()

    threading.Thread(target=echo, daemon=True).start()

    def resolver(host: str) -> list[str]:
        assert host == "pypi.org"
        return ["8.8.8.8"]

    real_connect = socket.create_connection

    def fake_connect(address, timeout=None):
        if address[0] == "8.8.8.8":
            seen.append(address)
            return real_connect(("127.0.0.1", echo_port), timeout=timeout)
        return real_connect(address, timeout=timeout)

    monkeypatch.setattr(proxy_mod.socket, "create_connection", fake_connect)
    server = serve(["pypi.org", "files.pythonhosted.org"], resolver)
    port = server.server_address[1]
    with socket.create_connection(("127.0.0.1", port), timeout=2) as sock:
        sock.settimeout(2)
        sock.sendall(_connect("pypi.org"))
        header = b""
        while b"\r\n\r\n" not in header:
            header += sock.recv(1024)
        assert header.startswith(b"HTTP/1.1 200")
        sock.sendall(b"hello")
        body = b""
        while b"echo:hello" not in body:
            body += sock.recv(1024)
    assert seen == [("8.8.8.8", 443)]
    assert server.decisions[-1]["decision"] == "allow"
    assert server.decisions[-1]["host"] == "pypi.org"
    assert "echo:hello" in body.decode()
    listener.close()


def test_denies_literals_other_ports_suffixes_and_private_resolutions(serve):
    resolved: list[str] = []

    def resolver(host: str) -> list[str]:
        resolved.append(host)
        return {
            "pypi.org": ["8.8.8.8"],
            "private.test": ["10.1.2.3"],
            "meta.test": ["169.254.169.254"],
            "loop.test": ["127.0.0.1"],
            "link.test": ["169.254.1.10"],
            "mixed.test": ["8.8.8.8", "10.0.0.1"],
        }[host]

    allow = [
        "pypi.org",
        "private.test",
        "meta.test",
        "loop.test",
        "link.test",
        "mixed.test",
        "8.8.8.8",
    ]
    server = serve(allow, resolver)
    port = server.server_address[1]
    cases = [
        (_connect("evil.pypi.org"), "not_allowlisted"),
        (_connect("pypi.org.evil.com"), "not_allowlisted"),
        (_connect("notpypi.org"), "not_allowlisted"),
        (_connect("pypi.org."), "not_allowlisted"),
        (_connect("pypi.org", 80), "port"),
        (_connect("pypi.org", 8443), "port"),
        (_connect("8.8.8.8"), "ip_literal"),
        (b"CONNECT [2001:db8::1]:443 HTTP/1.1\r\nHost: [2001:db8::1]:443\r\n\r\n", "ip_literal"),
        (_connect("private.test"), "blocked_address"),
        (_connect("meta.test"), "blocked_address"),
        (_connect("loop.test"), "blocked_address"),
        (_connect("link.test"), "blocked_address"),
        (_connect("mixed.test"), "blocked_address"),
    ]
    for payload, reason in cases:
        response = _exchange(port, payload)
        assert response.startswith(b"HTTP/1.1 403"), payload
        assert server.decisions[-1]["reason"] == reason

    query = b"GET http://pypi.org/simple?token=supersecret HTTP/1.1\r\nHost: pypi.org\r\n\r\n"
    response = _exchange(port, query)
    assert response.startswith(b"HTTP/1.1 403")
    rendered = repr(server.decisions)
    assert "supersecret" not in rendered
    assert "?" not in rendered
    assert "evil.pypi.org" not in resolved
    assert "pypi.org.evil.com" not in resolved
    assert "pypi.org." not in resolved
    assert "pypi.org" not in resolved


def test_module_cli_parses_allow_and_port():
    args = parse_args(["--allow", "pypi.org,files.pythonhosted.org", "--port", "8080"])
    assert args.allow_hosts == ["pypi.org", "files.pythonhosted.org"]
    assert args.port == 8080
    assert args.listen == "127.0.0.1"
