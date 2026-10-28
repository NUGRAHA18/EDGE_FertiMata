from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from fertimata_edge.link.payloads import (
    AckPayload,
    AiPayload,
    AlertPayload,
    CommandPayload,
    StatusPayload,
    TelemetryPayload,
)
from fertimata_edge.link.topics import Topics

NOW = datetime(2026, 10, 22, 8, 0, 0, 450000, tzinfo=timezone.utc)


def _json(payload) -> dict:
    return json.loads(payload.to_bytes())


def test_telemetry_matches_contract_and_drops_missing():
    payload = TelemetryPayload(
        timestamp=NOW,
        data={
            "air.temperature": 29.8,
            "air.humidity": None,
            "light.illuminance": float("nan"),
            "tank.float_low_ok": 1,
        },
    )
    assert _json(payload) == {
        "timestamp": "2026-10-22T08:00:00Z",
        "data": {"air.temperature": 29.8, "tank.float_low_ok": 1},
    }


def test_status_omits_empty_fields():
    assert _json(StatusPayload(timestamp=NOW)) == {"timestamp": "2026-10-22T08:00:00Z"}
    full = StatusPayload(
        timestamp=NOW,
        power_source="MAINS",
        actuators={"DIST-PUMP": "OFF"},
        metadata={"agentVersion": "0.1.0"},
    )
    assert _json(full) == {
        "timestamp": "2026-10-22T08:00:00Z",
        "powerSource": "MAINS",
        "actuators": {"DIST-PUMP": "OFF"},
        "metadata": {"agentVersion": "0.1.0"},
    }


def test_status_rejects_unknown_power_source_and_state():
    with pytest.raises(ValidationError):
        StatusPayload(timestamp=NOW, power_source="PLN")
    with pytest.raises(ValidationError):
        StatusPayload(timestamp=NOW, actuators={"DIST-PUMP": "on"})


def test_ack_success_has_null_error_message():
    ack = AckPayload(command_uid="u1", success=True, actuator_state="ON", executed_at=NOW)
    assert _json(ack) == {
        "commandUid": "u1",
        "success": True,
        "actuatorState": "ON",
        "executedAt": "2026-10-22T08:00:00Z",
        "errorMessage": None,
    }


def test_failed_ack_needs_reason():
    with pytest.raises(ValidationError):
        AckPayload(command_uid="u1", success=False, executed_at=NOW)


def test_alert_shape():
    alert = AlertPayload(
        type="SSR_SHORT",
        severity="CRITICAL",
        title="Saklar pompa rusak",
        message="Matikan RCBO di panel.",
        timestamp=NOW,
        actuator_code="DIST-PUMP",
        metadata={"current": 1.4},
    )
    assert _json(alert) == {
        "type": "SSR_SHORT",
        "severity": "CRITICAL",
        "title": "Saklar pompa rusak",
        "message": "Matikan RCBO di panel.",
        "actuatorCode": "DIST-PUMP",
        "sensorCode": None,
        "timestamp": "2026-10-22T08:00:00Z",
        "metadata": {"current": 1.4},
    }
    with pytest.raises(ValidationError):
        AlertPayload(type="X", severity="FATAL", title="t", message="m", timestamp=NOW)


def test_ai_needs_label_or_scores():
    with pytest.raises(ValidationError):
        AiPayload(detected_at=NOW, detection_type="NUTRIENT_DEFICIENCY")
    ai = AiPayload(
        detected_at=NOW,
        detection_type="NUTRIENT_DEFICIENCY",
        scores={"Leaf_N_stress": 0.71},
        station_code="ST-03",
        pan_direction="TIMUR",
        capture_id="20261022-1905_S03_timur",
    )
    assert _json(ai) == {
        "detectedAt": "2026-10-22T08:00:00Z",
        "detectionType": "NUTRIENT_DEFICIENCY",
        "scores": {"Leaf_N_stress": 0.71},
        "stationCode": "ST-03",
        "panDirection": "TIMUR",
        "captureId": "20261022-1905_S03_timur",
    }


def test_command_from_backend_json():
    raw = {
        "commandUid": "e71802f2",
        "deviceId": "RIO-TANK-01",
        "actuatorCode": "DOSING-N",
        "command": "ON",
        "parameters": {"durationSeconds": 5},
        "issuedAt": "2026-10-22T08:00:00.123456789Z",
        "expiresAt": "2026-10-22T08:00:30Z",
        "fieldBaru": "diabaikan",
    }
    cmd = CommandPayload.model_validate(raw)
    assert cmd.duration_s == 5
    assert cmd.expires_at == datetime(2026, 10, 22, 8, 0, 30, tzinfo=timezone.utc)
    assert cmd.issued_at.microsecond == 123456


@pytest.mark.parametrize(
    ("parameters", "expected"),
    [
        (None, None),
        ({}, None),
        ({"durationSeconds": "10"}, 10.0),
        ({"durationSeconds": True}, None),
        ({"durationSeconds": "lama"}, None),
    ],
)
def test_command_duration_parsing(parameters, expected):
    cmd = CommandPayload.model_validate(
        {"commandUid": "u", "actuatorCode": "STIRRER", "command": "ON", "parameters": parameters}
    )
    assert cmd.duration_s == expected


def test_topics(config):
    topics = Topics(config.mqtt.topics)
    assert topics.telemetry("PZEM-01") == "fertimata/PZEM-01/telemetry"
    assert topics.command_ack("RIO-TANK-01") == "fertimata/RIO-TANK-01/command/ack"
    assert topics.command_subscription == "fertimata/+/command"
    assert topics.device_from_command("fertimata/PANEL-01/command") == "PANEL-01"
    assert topics.device_from_command("fertimata/PANEL-01/command/ack") is None
    assert topics.device_from_command("fertimata/a/b/command") is None
    with pytest.raises(ValueError):
        topics.status("a/b")
