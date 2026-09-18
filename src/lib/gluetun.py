"""Gluetun control server client.

Gluetun has no port forwarding code for Windscribe, so the port obtained from
Windscribe is pushed to gluetun at runtime through its control server, which
then opens it on the VPN interface. See
https://github.com/qdm12/gluetun-wiki/blob/main/setup/advanced/control-server.md
"""

import logging

import httpx


class GluetunManager:
    """Set the forwarded port on a running gluetun instance."""

    def __init__(
        self, host: str, timeout: int | None = 5, client: httpx.Client | None = None
    ) -> None:
        self.logger = logging.getLogger(self.__class__.__name__)
        self.url = host.rstrip("/") + "/v1/portforward"
        self.client = client or httpx.Client(timeout=timeout)

    def set_port(self, port: int) -> bool:
        """Replace gluetun's forwarded ports with the given one and verify it.

        Raises:
            httpx.HTTPStatusError: If the control server rejects the request,
                typically 401 when authentication is not configured for
                `PUT /v1/portforward`.
        """
        self.client.put(self.url, json={"ports": [port]}).raise_for_status()

        resp = self.client.get(self.url)
        resp.raise_for_status()
        current = resp.json().get("port")
        self.logger.info("gluetun forwarded port: %s", current)
        return current == port
