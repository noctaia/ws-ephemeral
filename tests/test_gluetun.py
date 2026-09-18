"""Gluetun control server client."""

import sys
from pathlib import Path

import json

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from lib.gluetun import GluetunManager  # noqa: E402


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_set_port_puts_then_verifies():
    calls: list[tuple[str, str, bytes]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path, request.content))
        if request.method == "PUT":
            return httpx.Response(200)
        return httpx.Response(200, json={"port": 12345, "ports": [12345]})

    gluetun = GluetunManager("http://127.0.0.1:8000", client=_client(handler))
    assert gluetun.set_port(12345) is True
    assert calls[0][:2] == ("PUT", "/v1/portforward")
    assert json.loads(calls[0][2]) == {"ports": [12345]}
    assert calls[1][:2] == ("GET", "/v1/portforward")


def test_set_port_reports_mismatch():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "PUT":
            return httpx.Response(200)
        return httpx.Response(200, json={"port": 0, "ports": []})

    gluetun = GluetunManager("http://127.0.0.1:8000", client=_client(handler))
    assert gluetun.set_port(12345) is False


def test_set_port_raises_on_http_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401)

    gluetun = GluetunManager("http://127.0.0.1:8000", client=_client(handler))
    with pytest.raises(httpx.HTTPStatusError):
        gluetun.set_port(12345)
