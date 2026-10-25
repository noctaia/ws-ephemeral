"""The hand over of the new port to gluetun, next to qBittorrent."""

from pathlib import Path

import pytest

from ws_ephemeral import job
from ws_ephemeral.config import (
    GluetunSettings,
    QbitSettings,
    Settings,
    WindscribeSettings,
)

WS = WindscribeSettings(
    username="u", password="p", totp=None, cookie_file=Path("cookie.pkl"), timeout=5
)
QBIT = QbitSettings(host="h", port=1, username="u", password="p", private_tracker=False)
GLUETUN = GluetunSettings(host="http://127.0.0.1:8000", timeout=5)


class Fake:
    qbit_fails = False
    gluetun_fails = False
    gluetun_reports = True


@pytest.fixture
def events(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    log: list[str] = []
    Fake.qbit_fails = Fake.gluetun_fails = False
    Fake.gluetun_reports = True

    class Qbit:
        def __init__(self, **_: object) -> None:
            pass

        def set_listen_port(self, port: int) -> bool:
            log.append(f"qbit port {port}")
            if Fake.qbit_fails:
                raise ConnectionError("qbit went away")
            return True

    class Gluetun:
        def __init__(self, host: str, timeout: float | None) -> None:
            log.append(f"gluetun connect {host}")

        def set_port(self, port: int) -> bool:
            log.append(f"gluetun port {port}")
            if Fake.gluetun_fails:
                raise ConnectionError("gluetun went away")
            return Fake.gluetun_reports

    class Windscribe:
        def __init__(self, **_: object) -> None:
            pass

        def __enter__(self) -> Windscribe:
            return self

        def __exit__(self, *_: object) -> None:
            return None

        def setup(self) -> int:
            log.append("ws setup")
            return 4242

    monkeypatch.setattr(job, "QbitManager", Qbit)
    monkeypatch.setattr(job, "GluetunManager", Gluetun)
    monkeypatch.setattr(job, "Windscribe", Windscribe)
    return log


def test_port_goes_to_qbit_then_gluetun(events: list[str]) -> None:
    assert job.rotate_port(Settings(WS, QBIT, GLUETUN)) == 4242

    assert events == [
        "ws setup",
        "qbit port 4242",
        "gluetun connect http://127.0.0.1:8000",
        "gluetun port 4242",
    ]


def test_gluetun_alone_is_enough(events: list[str]) -> None:
    assert job.rotate_port(Settings(WS, None, GLUETUN)) == 4242

    assert "gluetun port 4242" in events
    assert not any(e.startswith("qbit") for e in events)


def test_gluetun_is_untouched_when_not_configured(events: list[str]) -> None:
    job.rotate_port(Settings(WS, QBIT))

    assert not any(e.startswith("gluetun") for e in events)


def test_gluetun_still_gets_the_port_when_qbit_fails(events: list[str]) -> None:
    Fake.qbit_fails = True

    with pytest.raises(ConnectionError, match="qbit"):
        job.rotate_port(Settings(WS, QBIT, GLUETUN))

    assert "gluetun port 4242" in events


def test_qbit_still_gets_the_port_when_gluetun_fails(events: list[str]) -> None:
    Fake.gluetun_fails = True

    with pytest.raises(ConnectionError, match="gluetun"):
        job.rotate_port(Settings(WS, QBIT, GLUETUN))

    assert "qbit port 4242" in events


def test_run_job_reports_a_gluetun_failure(events: list[str]) -> None:
    Fake.gluetun_fails = True

    assert job.run_job(Settings(WS, QBIT, GLUETUN)) is False


def test_a_port_gluetun_does_not_confirm_is_only_a_warning(
    events: list[str], caplog: pytest.LogCaptureFixture
) -> None:
    Fake.gluetun_reports = False

    assert job.run_job(Settings(WS, QBIT, GLUETUN)) is True
    assert "gluetun did not report the new port 4242" in caplog.text
