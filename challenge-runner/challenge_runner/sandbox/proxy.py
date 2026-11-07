"""Allowlist CONNECT proxy. Stdlib only.

Allows an exact allowlisted hostname on port 443. Denies every other request
with 403, including IP literals, other ports, suffix lookalikes, and any host
whose resolved addresses are not public. Decisions are logged without query
strings. Run as ``python -m challenge_runner.sandbox.proxy --allow h1,h2 --port N``.
"""

from __future__ import annotations

import argparse
import ipaddress
import logging
import re
import socket
import socketserver
import threading
from collections.abc import Callable

log = logging.getLogger("challenge_runner.sandbox.proxy")

_HOSTNAME_RE = re.compile(r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")
_METADATA = ipaddress.ip_address("169.254.169.254")
Resolver = Callable[[str], list[str]]


def default_resolver(host: str) -> list[str]:
    infos = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    found: list[str] = []
    for info in infos:
        ip = info[4][0]
        if ip not in found:
            found.append(ip)
    return found


def is_blocked_ip(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip.split("%", 1)[0])
    except ValueError:
        return True
    mapped = getattr(addr, "ipv4_mapped", None)
    if mapped is not None:
        return is_blocked_ip(str(mapped))
    if addr == _METADATA:
        return True
    return not addr.is_global


def host_allowed(host: str, allow: set[str]) -> bool:
    if host != host.strip() or host.endswith(".") or "/" in host or "\\" in host or "@" in host:
        return False
    lowered = host.lower()
    if _is_ip_literal(lowered):
        return False
    if _HOSTNAME_RE.fullmatch(lowered) is None:
        return False
    return lowered in allow


def _is_ip_literal(host: str) -> bool:
    candidate = host[1:-1] if host.startswith("[") and host.endswith("]") else host
    try:
        ipaddress.ip_address(candidate.split("%", 1)[0])
    except ValueError:
        return False
    return True


class ProxyServer(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        server_address: tuple[str, int],
        allowlist: list[str] | tuple[str, ...],
        resolver: Resolver | None = None,
    ):
        self.allow = {host.lower() for host in allowlist if host}
        self.resolver = resolver or default_resolver
        self.decisions: list[dict[str, str | int]] = []
        super().__init__(server_address, ProxyHandler)

    def record(self, decision: str, host: str, port: int, reason: str) -> None:
        safe_host = host.split("?", 1)[0].split("/", 1)[0]
        entry: dict[str, str | int] = {
            "decision": decision,
            "host": safe_host,
            "port": port,
            "reason": reason,
        }
        self.decisions.append(entry)
        log.info("%s %s:%s %s", decision, safe_host, port, reason)


class ProxyHandler(socketserver.BaseRequestHandler):
    server: ProxyServer

    def handle(self) -> None:
        try:
            raw = self._read_headers()
            self._dispatch(raw)
        except (OSError, UnicodeError, ValueError):
            self._deny("-", 0, "bad_request")

    def _read_headers(self) -> bytes:
        data = b""
        while b"\r\n\r\n" not in data:
            if len(data) > 16384:
                raise ValueError("headers")
            chunk = self.request.recv(4096)
            if not chunk:
                break
            data += chunk
        return data

    def _dispatch(self, raw: bytes) -> None:
        head = raw.split(b"\r\n\r\n", 1)[0]
        line = head.split(b"\r\n", 1)[0].decode("iso-8859-1", errors="replace").strip()
        parts = line.split(" ")
        if len(parts) < 2:
            self._deny("-", 0, "bad_request")
            return
        method, target = parts[0].upper(), parts[1]
        host, port = _parse_target(target)
        if method != "CONNECT":
            self._deny(host, port or 0, "method")
            return
        if port != 443:
            self._deny(host, port or 0, "port")
            return
        if _is_ip_literal(host):
            self._deny(host, 443, "ip_literal")
            return
        if not host_allowed(host, self.server.allow):
            self._deny(host, 443, "not_allowlisted")
            return
        try:
            addresses = self.server.resolver(host)
        except OSError:
            self._deny(host, 443, "resolve_failed")
            return
        if not addresses or any(is_blocked_ip(ip) for ip in addresses):
            self._deny(host, 443, "blocked_address")
            return
        upstream = self._connect(addresses)
        if upstream is None:
            self._deny(host, 443, "connect_failed")
            return
        self.server.record("allow", host, 443, "allowlisted")
        try:
            self.request.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            _tunnel(self.request, upstream)
        finally:
            upstream.close()

    def _connect(self, addresses: list[str]) -> socket.socket | None:
        for ip in addresses:
            if is_blocked_ip(ip):
                continue
            try:
                return socket.create_connection((ip, 443), timeout=10)
            except OSError:
                continue
        return None

    def _deny(self, host: str, port: int, reason: str) -> None:
        self.server.record("deny", host, port, reason)
        body = b"denied"
        message = (
            f"HTTP/1.1 403 Forbidden\r\nContent-Length: {len(body)}\r\nConnection: close\r\n\r\n"
        ).encode("ascii") + body
        try:
            self.request.sendall(message)
        except OSError:
            return


def _parse_target(target: str) -> tuple[str, int | None]:
    bare = target.split("?", 1)[0].split("#", 1)[0]
    if "://" in bare:
        bare = bare.split("://", 1)[1]
    bare = bare.split("/", 1)[0]
    if bare.startswith("["):
        end = bare.find("]")
        if end == -1:
            return bare, None
        host = bare[: end + 1]
        rest = bare[end + 1 :]
        if rest.startswith(":"):
            try:
                return host, int(rest[1:])
            except ValueError:
                return host, None
        return host, None
    if bare.count(":") == 1:
        host, port_text = bare.rsplit(":", 1)
        try:
            return host, int(port_text)
        except ValueError:
            return host, None
    return bare, None


def _tunnel(client: socket.socket, upstream: socket.socket) -> None:
    closed = threading.Event()

    def pump(src: socket.socket, dst: socket.socket) -> None:
        try:
            while not closed.is_set():
                data = src.recv(65536)
                if not data:
                    break
                dst.sendall(data)
        except OSError:
            return
        finally:
            closed.set()
            try:
                dst.shutdown(socket.SHUT_WR)
            except OSError:
                return

    workers = (
        threading.Thread(target=pump, args=(client, upstream), daemon=True),
        threading.Thread(target=pump, args=(upstream, client), daemon=True),
    )
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=120)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="challenge_runner.sandbox.proxy")
    parser.add_argument("--allow", required=True, help="comma-separated hostnames")
    parser.add_argument("--port", required=True, type=int)
    parser.add_argument("--listen", default="127.0.0.1")
    args = parser.parse_args(argv)
    args.allow_hosts = [part.strip() for part in args.allow.split(",") if part.strip()]
    return args


def make_server(args: argparse.Namespace, resolver: Resolver | None = None) -> ProxyServer:
    return ProxyServer((args.listen, args.port), args.allow_hosts, resolver=resolver)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    server = make_server(args)
    server.serve_forever()


if __name__ == "__main__":
    main()
