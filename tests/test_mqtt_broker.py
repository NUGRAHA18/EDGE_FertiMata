"""Uji link melawan Mosquitto sungguhan di 127.0.0.1:1883. Dilewati bila broker tidak ada.

Menyalakan broker: `docker compose up -d mosquitto` di repo backend.
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import threading
import uuid
from datetime import timedelta

import pytest

from fertimata_edge.hal.clock import SystemClock, to_iso_z
from fertimata_edge.health import DeviceHealth
from fertimata_edge.link import Link, PahoTransport

from .fakes import RecordingHandler

BROKER = ("127.0.0.1", 1883)


def _broker_up() -> bool:
    try:
        with socket.create_connection(BROKER, timeout=0.5):
            return True
    except OSError:
        return False


pytestmark = pytest.mark.skipif(not _broker_up(), reason="Mosquitto tidak jalan di 127.0.0.1:1883")


class Peer:
    """Klien paho yang berperan seperti backend: mendengar fertimata/# dan menerbitkan perintah."""

    def __init__(self, prefix: str) -> None:
        import paho.mqtt.client as mqtt

        self.received: list[tuple[str, dict]] = []
        self._lock = threading.Lock()
        self._ready = threading.Event()
        self.client = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            client_id=f"fertimata-test-peer-{uuid.uuid4().hex[:8]}",
            protocol=mqtt.MQTTv5,
        )
        self.client.on_connect = lambda c, *_: (c.subscribe(f"{prefix}/#", qos=1), None)
        self.client.on_subscribe = lambda *_: self._ready.set()
        self.client.on_message = self._message
        self.client.connect(*BROKER)
        self.client.loop_start()
        assert self._ready.wait(5), "peer gagal subscribe"

    def _message(self, client, userdata, message) -> None:
        with self._lock:
            self.received.append((message.topic, json.loads(message.payload or b"{}")))

    def find(self, suffix: str) -> list[tuple[str, dict]]:
        with self._lock:
            return [m for m in self.received if m[0].endswith(suffix)]

    def close(self) -> None:
        self.client.disconnect()
        self.client.loop_stop()


async def _wait_for(predicate, timeout: float = 5.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("waktu habis menunggu pesan")
        await asyncio.sleep(0.05)


async def test_round_trip_with_real_broker(config):
    # Prefix topik unik supaya tidak mengganggu backend/agent yang sedang jalan di broker yang sama.
    prefix = f"fertimata-test-{os.getpid()}-{uuid.uuid4().hex[:6]}"
    topics = {
        k: v.replace("fertimata/", f"{prefix}/", 1)
        for k, v in config.mqtt.topics.model_dump().items()
    }
    mqtt_cfg = config.mqtt.model_copy(
        update={
            "client_id": f"fertimata-edge-test-{uuid.uuid4().hex[:8]}",
            "session_expiry_s": 0,
            "topics": config.mqtt.topics.model_validate(topics),
        }
    )
    config = config.model_copy(update={"mqtt": mqtt_cfg})

    peer = Peer(prefix)
    clock = SystemClock()
    handler = RecordingHandler()
    link = Link(config, PahoTransport(config.mqtt), clock, DeviceHealth(config), handler)
    try:
        await link.start()
        await _wait_for(lambda: peer.find("/JETSON-01/status"))
        status = peer.find("/JETSON-01/status")[0][1]
        assert status["metadata"]["agentVersion"]

        link.publish_telemetry("PZEM-01", {"mains.voltage": 221.4})
        await _wait_for(lambda: peer.find("/PZEM-01/telemetry"))
        assert peer.find("/PZEM-01/telemetry")[0][1]["data"] == {"mains.voltage": 221.4}

        command = {
            "commandUid": "it-1",
            "deviceId": "RIO-TANK-01",
            "actuatorCode": "STIRRER",
            "command": "ON",
            "parameters": {"durationSeconds": 10},
            "issuedAt": to_iso_z(clock.now()),
            "expiresAt": to_iso_z(clock.now() + timedelta(seconds=30)),
        }
        expired = dict(command, commandUid="it-2", expiresAt=to_iso_z(clock.now()))
        for body in (command, command, expired):  # it-1 dikirim dua kali: QoS 1 bisa mengulang
            peer.client.publish(f"{prefix}/RIO-TANK-01/command", json.dumps(body), qos=1)
        await _wait_for(lambda: len(peer.find("/RIO-TANK-01/command/ack")) >= 2)
        await asyncio.sleep(0.3)  # beri waktu ACK ganda (yang tidak boleh ada) untuk muncul
        acks = {body["commandUid"]: body for _, body in peer.find("/command/ack")}
        assert len(peer.find("/command/ack")) == 2
        assert acks["it-1"]["success"] is True
        assert acks["it-2"]["errorMessage"].startswith("EXPIRED")
        assert [c.payload.command_uid for c in handler.commands] == ["it-1"]
    finally:
        await link.stop()
        peer.close()
