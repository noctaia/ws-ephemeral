"""Gluetun control server client."""

import json

import httpx
import pytest

from ws_ephemeral.gluetun import GluetunManager


def _manager(handler):
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return GluetunManager("http://127.0.0.1:8000/", client=client, retry_delay=0)


def test_set_port_puts_then_verifies() -> None:
    calls: list[tuple[str, str, bytes]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path, request.content))
        if request.method == "PUT":
            return httpx.Response(200)
        return httpx.Response(200, json={"port": 12345, "ports": [12345]})

    assert _manager(handler).set_port(12345) is True
    assert calls[0][:2] == ("PUT", "/v1/portforward")
    assert json.loads(calls[0][2]) == {"ports": [12345]}
    assert calls[1][:2] == ("GET", "/v1/portforward")


def test_set_port_reports_mismatch() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "PUT":
            return httpx.Response(200)
        return httpx.Response(200, json={"port": 0, "ports": []})

    assert _manager(handler).set_port(12345) is False


def test_set_port_raises_on_http_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401)

    with pytest.raises(httpx.HTTPStatusError):
        _manager(handler).set_port(12345)


def test_set_port_retries_while_service_not_started() -> None:
    puts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal puts
        if request.method == "PUT":
            puts += 1
            if puts < 3:
                return httpx.Response(500, text="port forwarding service not started")
            return httpx.Response(200)
        return httpx.Response(200, json={"port": 12345, "ports": [12345]})

    assert _manager(handler).set_port(12345) is True
    assert puts == 3


def test_set_port_gives_up_when_the_vpn_never_comes_up() -> None:
    puts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal puts
        puts += 1
        return httpx.Response(500, text="port forwarding service not started")

    with pytest.raises(httpx.HTTPStatusError):
        _manager(handler).set_port(12345)
    assert puts == 30
