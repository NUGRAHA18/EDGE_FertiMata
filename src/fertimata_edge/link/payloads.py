"""Model payload MQTT, mengikuti record Java di backend `mqtt/payload/*.java` (Kontrak §4–§5).

Waktu selalu UTC ISO-8601 dengan `Z`. Field opsional yang kosong tidak dikirim, kecuali
`errorMessage` di ACK yang menurut kontrak §4.3 dikirim `null` saat sukses.
"""

from __future__ import annotations

import json
import math
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..hal.clock import parse_iso, to_iso_z

ActuatorState = Literal["ON", "OFF"]
PowerSource = Literal["MAINS", "BATTERY", "UNKNOWN"]
Severity = Literal["INFO", "WARNING", "CRITICAL"]


def _dumps(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


class _Outbound(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class TelemetryPayload(_Outbound):
    timestamp: datetime
    data: dict[str, float | int | str | bool]

    @field_validator("data", mode="before")
    @classmethod
    def _drop_missing(cls, value: dict[str, Any]) -> dict[str, Any]:
        # Kontrak §4.1: metrik yang gagal dibaca dihilangkan, tidak pernah null/0/NaN.
        return {
            key: val
            for key, val in value.items()
            if val is not None and not (isinstance(val, float) and not math.isfinite(val))
        }

    def to_bytes(self) -> bytes:
        return _dumps({"timestamp": to_iso_z(self.timestamp), "data": dict(self.data)})


class StatusPayload(_Outbound):
    timestamp: datetime
    power_source: PowerSource | None = None
    actuators: dict[str, ActuatorState] | None = None
    status: str | None = None
    metadata: dict[str, Any] | None = None

    def to_bytes(self) -> bytes:
        body: dict[str, Any] = {"timestamp": to_iso_z(self.timestamp)}
        if self.power_source is not None:
            body["powerSource"] = self.power_source
        if self.actuators is not None:
            body["actuators"] = dict(self.actuators)
        if self.status is not None:
            body["status"] = self.status
        if self.metadata:
            body["metadata"] = dict(self.metadata)
        return _dumps(body)


class AckPayload(_Outbound):
    command_uid: str = Field(min_length=1)
    success: bool
    actuator_state: ActuatorState | None = None
    executed_at: datetime
    error_message: str | None = None

    @model_validator(mode="after")
    def _failure_needs_reason(self) -> AckPayload:
        if not self.success and not self.error_message:
            raise ValueError("ACK gagal wajib punya errorMessage")
        return self

    def to_bytes(self) -> bytes:
        return _dumps(
            {
                "commandUid": self.command_uid,
                "success": self.success,
                "actuatorState": self.actuator_state,
                "executedAt": to_iso_z(self.executed_at),
                "errorMessage": self.error_message,
            }
        )


class AlertPayload(_Outbound):
    type: str = Field(min_length=1)
    severity: Severity
    title: str = Field(min_length=1)  # untuk petani: bahasa sederhana, tanpa istilah teknis
    message: str = Field(min_length=1)
    timestamp: datetime
    actuator_code: str | None = None
    sensor_code: str | None = None
    metadata: dict[str, Any] | None = None

    def to_bytes(self) -> bytes:
        return _dumps(
            {
                "type": self.type,
                "severity": self.severity,
                "title": self.title,
                "message": self.message,
                "actuatorCode": self.actuator_code,
                "sensorCode": self.sensor_code,
                "timestamp": to_iso_z(self.timestamp),
                "metadata": dict(self.metadata or {}),
            }
        )


class AiPayload(_Outbound):
    detected_at: datetime
    detection_type: str
    label: str | None = None
    confidence: float | None = None
    scores: dict[str, float] | None = None
    station_code: str | None = None
    pan_direction: str | None = None
    capture_id: str | None = None
    image_url: str | None = None
    metadata: dict[str, Any] | None = None

    @model_validator(mode="after")
    def _label_or_scores(self) -> AiPayload:
        if not self.label and not self.scores:
            raise ValueError("hasil AI wajib punya label atau scores (Kontrak §4.5)")
        return self

    def to_bytes(self) -> bytes:
        names = {
            "detection_type": "detectionType",
            "label": "label",
            "confidence": "confidence",
            "scores": "scores",
            "station_code": "stationCode",
            "pan_direction": "panDirection",
            "capture_id": "captureId",
            "image_url": "imageUrl",
            "metadata": "metadata",
        }
        body: dict[str, Any] = {"detectedAt": to_iso_z(self.detected_at)}
        for attr, key in names.items():
            value = getattr(self, attr)
            if value is not None:
                body[key] = value
        return _dumps(body)


# --------------------------------------------------------------------------------------------
# Masuk: perintah dari backend
# --------------------------------------------------------------------------------------------


class CommandPayload(BaseModel):
    """`ActuatorCommandPayload` backend. Field tambahan di masa depan diabaikan."""

    model_config = ConfigDict(extra="ignore", frozen=True, populate_by_name=True)

    command_uid: str = Field(alias="commandUid", min_length=1)
    device_id: str | None = Field(default=None, alias="deviceId")
    actuator_code: str = Field(alias="actuatorCode", min_length=1)
    command: str = Field(min_length=1)
    parameters: dict[str, Any] = Field(default_factory=dict)
    issued_at: datetime | None = Field(default=None, alias="issuedAt")
    expires_at: datetime | None = Field(default=None, alias="expiresAt")

    @field_validator("parameters", mode="before")
    @classmethod
    def _none_is_empty(cls, value: Any) -> Any:
        return {} if value is None else value

    @field_validator("issued_at", "expires_at", mode="before")
    @classmethod
    def _parse_time(cls, value: Any) -> Any:
        if isinstance(value, str):
            return parse_iso(value)
        return value

    @property
    def duration_s(self) -> float | None:
        """`parameters.durationSeconds` sebagai angka, None bila tidak ada/tidak valid."""
        value = self.parameters.get("durationSeconds")
        if isinstance(value, bool) or value is None:
            return None
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return number if math.isfinite(number) else None
