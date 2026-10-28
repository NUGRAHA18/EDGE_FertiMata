"""Topik MQTT dari template config, sama dengan `MQTT_TOPIC_*` backend (Kontrak §2)."""

from __future__ import annotations

import re

from ..config import TopicsConfig

PLACEHOLDER = "{deviceId}"


class Topics:
    def __init__(self, config: TopicsConfig) -> None:
        self._templates = config.model_dump()
        before, after = config.command.split(PLACEHOLDER, 1)
        self._command_re = re.compile(
            "^" + re.escape(before) + r"(?P<device>[^/+#]+)" + re.escape(after) + "$"
        )

    def build(self, kind: str, device_id: str) -> str:
        if not device_id or any(c in device_id for c in "/+#"):
            raise ValueError(f"deviceId tidak valid untuk topik: {device_id!r}")
        return self._templates[kind].replace(PLACEHOLDER, device_id)

    def telemetry(self, device_id: str) -> str:
        return self.build("telemetry", device_id)

    def status(self, device_id: str) -> str:
        return self.build("status", device_id)

    def command_ack(self, device_id: str) -> str:
        return self.build("command_ack", device_id)

    def alert(self, device_id: str) -> str:
        return self.build("alert", device_id)

    def ai(self, device_id: str) -> str:
        return self.build("ai", device_id)

    @property
    def command_subscription(self) -> str:
        """Satu langganan untuk semua device: `fertimata/+/command`."""
        return self._templates["command"].replace(PLACEHOLDER, "+")

    def device_from_command(self, topic: str) -> str | None:
        """deviceId dari topik command; backend mempercayai topik, bukan field payload."""
        match = self._command_re.match(topic)
        return match.group("device") if match else None
