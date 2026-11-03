"""Lightweight socket connection watcher to verify offline operation."""

from __future__ import annotations

import socket

NEEDS_INTERNET: dict[str, str] = {
    "web_search": "opens Google in your browser",
    "open_url": "opens websites in your browser",
    "first run": "downloads Whisper, Kokoro and wake-word models once",
}


def _is_loopback(host: str) -> bool:
    h = host.strip().lower()
    return (
        h in {"localhost", "::1", "0.0.0.0", ""}
        or h.startswith("127.")
        or h.startswith("::ffff:127.")
    )


class ConnectionLog:
    """Context manager that records non-loopback outbound socket connection attempts."""

    def __init__(self) -> None:
        self.external: list[str] = []
        self._names: dict[str, str] = {}
        self._orig_connect = None
        self._orig_getaddrinfo = None

    def _record(self, host: str) -> None:
        name = self._names.get(host, host)
        if _is_loopback(host) or _is_loopback(name):
            return
        if name not in self.external:
            self.external.append(name)

    def __enter__(self) -> ConnectionLog:
        orig_connect = socket.socket.connect
        orig_getaddrinfo = socket.getaddrinfo
        self._orig_connect = orig_connect
        self._orig_getaddrinfo = orig_getaddrinfo
        log = self

        def tracked_getaddrinfo(host, port, *args, **kwargs):
            results = orig_getaddrinfo(host, port, *args, **kwargs)
            host_str = host.decode("utf-8", errors="ignore") if isinstance(host, bytes) else str(host or "")
            if host_str:
                for res in results:
                    sockaddr = res[4]
                    if isinstance(sockaddr, tuple) and sockaddr:
                        ip = str(sockaddr[0])
                        if host_str != ip:
                            log._names[ip] = host_str
            return results

        def tracked_connect(sock, address):
            if isinstance(address, tuple) and address:
                log._record(str(address[0]))
            return orig_connect(sock, address)

        socket.getaddrinfo = tracked_getaddrinfo
        socket.socket.connect = tracked_connect
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._orig_connect is not None:
            socket.socket.connect = self._orig_connect
        if self._orig_getaddrinfo is not None:
            socket.getaddrinfo = self._orig_getaddrinfo

