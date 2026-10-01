"""Run the sandbox payments API in a background thread, or reuse one that is already up."""

from __future__ import annotations

import socket
import threading
import time
from dataclasses import dataclass

import httpx
import uvicorn

from cerebellum.errors import CerebellumError
from cerebellum.sandbox.payments import SERVICE_NAME, FailMode, PaymentsState, create_payments_app


@dataclass
class SandboxHandle:
    url: str
    owned: bool
    _server: uvicorn.Server | None = None
    _thread: threading.Thread | None = None

    def set_fail_mode(self, mode: str) -> None:
        FailMode.parse(mode)
        response = httpx.put(f"{self.url}/_sandbox/fail-mode", json={"mode": mode}, timeout=5.0)
        response.raise_for_status()

    def stop(self) -> None:
        if self.owned and self._server is not None:
            self._server.should_exit = True
            if self._thread is not None:
                self._thread.join(timeout=5.0)

    def __enter__(self) -> SandboxHandle:
        return self

    def __exit__(self, *exc: object) -> None:
        self.stop()


def sandbox_running(url: str) -> bool:
    try:
        response = httpx.get(f"{url}/health", timeout=0.5)
        return response.status_code == 200 and response.json().get("service") == SERVICE_NAME
    except (httpx.HTTPError, ValueError):
        return False


def _port_in_use(host: str, port: int) -> bool:
    with socket.socket() as sock:
        sock.settimeout(0.3)
        return sock.connect_ex((host, port)) == 0


def sandbox_url(host: str, port: int) -> str:
    """The base URL of the sandbox payments API on `host`:`port` (what start_sandbox serves);
    an IPv6 address goes in brackets."""
    return f"http://[{host}]:{port}" if ":" in host else f"http://{host}:{port}"


def start_sandbox(host: str, port: int, fail: str = "never") -> SandboxHandle:
    url = sandbox_url(host, port)
    if sandbox_running(url):
        handle = SandboxHandle(url, owned=False)
        handle.set_fail_mode(fail)
        return handle
    if _port_in_use(host, port):
        raise CerebellumError(
            f"port {port} is in use by another service; set CEREBELLUM_SANDBOX_PORT"
        )
    app = create_payments_app(PaymentsState(FailMode.parse(fail)))
    config = uvicorn.Config(
        app, host=host, port=port, log_level="warning", lifespan="off", access_log=False
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, name="cerebellum-sandbox", daemon=True)
    thread.start()
    deadline = time.monotonic() + 5.0
    while not server.started:
        if not thread.is_alive() or time.monotonic() > deadline:
            server.should_exit = True
            raise CerebellumError(f"sandbox payments API failed to start on {url}")
        time.sleep(0.02)
    return SandboxHandle(url, owned=True, _server=server, _thread=thread)
