"""Gluetun control server client.

Gluetun has no port forwarding code for Windscribe, so the port obtained from
Windscribe is pushed to gluetun at runtime through its control server, which
then opens it on the VPN interface. See
https://github.com/qdm12/gluetun-wiki/blob/main/setup/advanced/control-server.md
"""

import logging
import time

import httpx

# gluetun answers 500 "port forwarding service not started" until the tunnel
# is up, which at pod start is a few seconds after us.
_ATTEMPTS = 30
_RETRY_DELAY = 10


class GluetunManager:
    """Set the forwarded port on a running gluetun instance."""

    def __init__(
        self,
        host: str,
        timeout: float | None = 5,
        client: httpx.Client | None = None,
        retry_delay: float = _RETRY_DELAY,
    ) -> None:
        self.logger = logging.getLogger(self.__class__.__name__)
        self.url = host.rstrip("/") + "/v1/portforward"
        self.client = client or httpx.Client(timeout=timeout)
        self.retry_delay = retry_delay

    def set_port(self, port: int) -> bool:
        """Replace gluetun's forwarded ports with the given one and verify it.

        Args:
            port (int): The port to forward.

        Returns:
            bool: True if gluetun reports the port as forwarded.

        Raises:
            httpx.HTTPStatusError: If the control server rejects the request:
                401 when authentication is not configured for
                `PUT /v1/portforward`, 400 on gluetun releases without the
                route (< v3.42), 500 if the VPN never comes up.
        """
        attempt = 0
        while True:
            attempt += 1
            resp = self.client.put(self.url, json={"ports": [port]})
            if resp.status_code != 500 or attempt >= _ATTEMPTS:
                break
            self.logger.warning(
                "gluetun not ready (%s), retry %d/%d",
                resp.text.strip(),
                attempt,
                _ATTEMPTS,
            )
            time.sleep(self.retry_delay)
        resp.raise_for_status()

        resp = self.client.get(self.url)
        resp.raise_for_status()
        current = resp.json().get("port")
        self.logger.info("gluetun forwarded port: %s", current)
        return current == port
