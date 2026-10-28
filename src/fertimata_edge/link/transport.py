"""Transport MQTT: antarmuka + implementasi paho-mqtt.

paho menjalankan jaringannya di thread sendiri (`loop_start`), dan setiap callback dipindahkan ke
event loop asyncio lewat `call_soon_threadsafe`. Pola ini dipilih ketimbang aiomqtt karena
ProactorEventLoop di Windows tidak mendukung `add_reader`, sedangkan agent harus bisa jalan di
laptop Windows dan Jetson.
"""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from collections.abc import Callable

from ..config import MqttConfig

log = logging.getLogger(__name__)

ConnectCallback = Callable[[bool], None]  # argumen: session_present
DisconnectCallback = Callable[[str], None]  # argumen: alasan
MessageCallback = Callable[[str, bytes], None]


class MqttTransport(ABC):
    """Semua callback dipanggil di thread event loop."""

    def __init__(self) -> None:
        self.on_connect: ConnectCallback = lambda session_present: None
        self.on_disconnect: DisconnectCallback = lambda reason: None
        self.on_message: MessageCallback = lambda topic, payload: None

    @property
    @abstractmethod
    def connected(self) -> bool: ...

    @abstractmethod
    async def start(self, subscriptions: list[str]) -> None:
        """Mulai menyambung tanpa menunggu. Langganan dipasang ulang setiap kali tersambung."""

    @abstractmethod
    async def stop(self) -> None: ...

    @abstractmethod
    def publish(self, topic: str, payload: bytes) -> bool:
        """Serahkan pesan ke klien (QoS dari config, tanpa retain). False = belum bisa dikirim."""


class PahoTransport(MqttTransport):
    def __init__(self, config: MqttConfig) -> None:
        super().__init__()
        import paho.mqtt.client as mqtt  # impor lambat: test unit tidak butuh paho

        self._mqtt = mqtt
        self._config = config
        self._loop: asyncio.AbstractEventLoop | None = None
        self._subscriptions: list[str] = []
        self._connected = False
        self._client = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            client_id=config.client_id,
            protocol=mqtt.MQTTv5,
        )
        if config.username:
            self._client.username_pw_set(config.username, config.password)
        self._client.reconnect_delay_set(
            min_delay=max(1, int(config.reconnect_min_s)),
            max_delay=max(1, int(config.reconnect_max_s)),
        )
        # Kontrak §1: tanpa Last Will. Backend menganggap tiap pesan status tanda hidup.
        self._client.on_connect = self._paho_connect
        self._client.on_disconnect = self._paho_disconnect
        self._client.on_message = self._paho_message

    @property
    def connected(self) -> bool:
        return self._connected

    async def start(self, subscriptions: list[str]) -> None:
        from paho.mqtt.packettypes import PacketTypes
        from paho.mqtt.properties import Properties

        self._loop = asyncio.get_running_loop()
        self._subscriptions = list(subscriptions)
        props = Properties(PacketTypes.CONNECT)
        props.SessionExpiryInterval = self._config.session_expiry_s
        log.info("menyambung ke broker %s:%d", self._config.host, self._config.port)
        # connect_async + loop_start: tidak memblokir, dan paho menyambung ulang sendiri.
        self._client.connect_async(
            self._config.host,
            self._config.port,
            keepalive=self._config.keepalive_s,
            clean_start=self._config.clean_start,
            properties=props,
        )
        self._client.loop_start()

    async def stop(self) -> None:
        self._client.disconnect()
        await asyncio.to_thread(self._client.loop_stop)
        self._connected = False

    def publish(self, topic: str, payload: bytes) -> bool:
        if not self._connected:
            return False
        info = self._client.publish(topic, payload, qos=self._config.qos, retain=False)
        return info.rc == self._mqtt.MQTT_ERR_SUCCESS

    # --- callback dari thread paho ------------------------------------------------------------

    def _paho_connect(self, client, userdata, flags, reason_code, properties) -> None:
        if reason_code.is_failure:
            log.warning("broker menolak sambungan: %s", reason_code)
            return
        for topic in self._subscriptions:
            client.subscribe(topic, qos=self._config.qos)
        self._connected = True
        self._post(self.on_connect, bool(flags.session_present))

    def _paho_disconnect(self, client, userdata, flags, reason_code, properties) -> None:
        self._connected = False
        self._post(self.on_disconnect, str(reason_code))

    def _paho_message(self, client, userdata, message) -> None:
        self._post(self.on_message, message.topic, bytes(message.payload))

    def _post(self, callback, *args) -> None:
        loop = self._loop
        if loop is not None and not loop.is_closed():
            loop.call_soon_threadsafe(callback, *args)
