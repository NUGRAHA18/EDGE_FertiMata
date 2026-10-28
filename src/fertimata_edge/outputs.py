"""Model semua output relay: tulis + baca balik, watchdog durasi, dan pemantauan relay.

- S1: `all_off()` menulis OFF ke **semua** kanal setiap relay lalu membaca balik, sebelum logika
  lain jalan (Arsitektur §5, §12.1). Relay yang belum menjawab saat boot di-OFF-kan begitu
  pertama kali menjawab, sebelum boleh dipakai.
- S19: setiap tulis diikuti baca balik. Tidak cocok → coba OFF, alert `RELAY_MISMATCH`, output
  ditandai rusak (ON ditolak sampai agent di-restart setelah panel diperiksa).
- S2: output yang ON dimatikan sendiri setelah `min(durationSeconds, max_on_s)`. Alert
  `OUTPUT_TIMEOUT` hanya bila `max_on_s` yang memotong (usulan Q1).
- S8: IN1 (float LOW) dibaca tiap `polling.relays_s` dan dikirim sebagai telemetri (usulan Q2).
  Terbuka atau basi → output `blocked_on_float_low` yang ON dimatikan.
- S14: selama output jalan trolley ON, polling sensor dijeda.

State yang dilaporkan selalu hasil baca balik, bukan hasil tulisan (Kontrak §4.2).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import math
from collections.abc import Callable
from dataclasses import dataclass

from .bus import BusManager, Priority
from .config import Config
from .hal import BusUnavailable, Clock, ModbusDeviceError, ModbusTimeout
from .logs import fields
from .poller import TelemetrySink, alert_if_silent, device_label
from .profiles.relay import RelayProfile, build_relay_profiles

log = logging.getLogger(__name__)

BUS_ERRORS = (ModbusTimeout, ModbusDeviceError, BusUnavailable)

# Kode alasan kegagalan (awal errorMessage ACK, Kontrak §4.3).
REASON_DEVICE_OFFLINE = "DEVICE_OFFLINE"
REASON_RELAY_MISMATCH = "RELAY_MISMATCH"


@dataclass
class OutputState:
    on: bool | None = None  # hasil baca balik terakhir; None = belum/tidak diketahui
    faulty: bool = False  # S19: baca balik pernah tidak cocok
    changed_at: float = -math.inf  # monotonic saat state berubah (jeda S12)
    deadline: float | None = None  # monotonic saat watchdog mematikan
    timer: asyncio.Task[None] | None = None


@dataclass(frozen=True)
class SetResult:
    ok: bool
    state: bool | None  # state hasil baca balik setelah perintah
    reason: str | None = None
    text: str | None = None  # untuk petani


ChangeListener = Callable[[str], None]  # dipanggil dengan nama perangkat relay


def capitalize(text: str) -> str:
    return text[:1].upper() + text[1:]


class Outputs:
    def __init__(
        self, config: Config, bus: BusManager, clock: Clock, sink: TelemetrySink | None = None
    ) -> None:
        self.config = config
        self.bus = bus
        self.clock = clock
        self.sink = sink
        self.profiles: dict[str, RelayProfile] = build_relay_profiles(config.devices)
        self.states: dict[str, OutputState] = {name: OutputState() for name in config.outputs}
        self.initialized: dict[str, bool] = {name: False for name in self.profiles}
        self.listeners: list[ChangeListener] = []
        self.logical_of: dict[str, str] = {
            src: name for name, ld in config.logical_devices.items() for src in ld.sources
        }
        self._by_channel: dict[tuple[str, int], str] = {
            (out.device, out.ch): name for name, out in config.outputs.items()
        }
        self._float_blocked = config.safety.expand(config.safety.blocked_on_float_low)
        self.float_closed: bool | None = None
        self.float_read_at = -math.inf
        # Bertambah setiap tulis per relay; hasil polling yang bersilangan dengan tulis dibuang.
        self._write_seq: dict[str, int] = {name: 0 for name in self.profiles}
        self._writing: dict[str, int] = {name: 0 for name in self.profiles}  # tulis sedang jalan
        self._lock = asyncio.Lock()
        self._task: asyncio.Task[None] | None = None

    # --- siklus hidup -------------------------------------------------------------------------

    def start(self) -> None:
        self._task = asyncio.create_task(self._loop(), name="relay-poll")

    async def stop(self) -> None:
        tasks = [st.timer for st in self.states.values() if st.timer is not None]
        if self._task is not None:
            tasks.append(self._task)
        for task in tasks:
            task.cancel()
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._task = None

    @property
    def tasks(self) -> list[asyncio.Task[None]]:
        return [self._task] if self._task is not None else []

    async def _loop(self) -> None:
        while True:
            await self.poll()
            await self.clock.sleep(self.config.polling.relays_s)

    # --- pertanyaan ---------------------------------------------------------------------------

    def state(self, name: str) -> bool | None:
        return self.states[name].on

    def label(self, name: str) -> str:
        out = self.config.outputs[name]
        return out.label or out.actuator_code

    def float_ok(self) -> bool:
        """S8: float tertutup dan bacaannya belum basi (`safety.float_max_age_s`)."""
        age = self.clock.monotonic() - self.float_read_at
        return self.float_closed is True and age <= self.config.safety.float_max_age_s

    def actuator_states(self, logical: str) -> dict[str, str]:
        """Map `actuators` untuk status device logis: hanya output yang state-nya diketahui."""
        sources = set(self.config.logical_devices[logical].sources)
        return {
            out.actuator_code: "ON" if self.states[name].on else "OFF"
            for name, out in self.config.outputs.items()
            if out.device in sources and self.states[name].on is not None
        }

    # --- S1 -----------------------------------------------------------------------------------

    async def all_off(self) -> bool:
        """S1: semua kanal semua relay OFF + baca balik. True bila semuanya terbukti OFF."""
        ok = True
        async with self._lock:
            for device in self.profiles:
                ok = await self._init_device(device) and ok
        return ok

    async def _init_device(self, device: str) -> bool:
        profile = self.profiles[device]
        self._write_seq[device] += 1
        for ch in range(profile.channels):
            with contextlib.suppress(*BUS_ERRORS):
                await profile.write(self.bus, ch, False, Priority.STOP)
        try:
            states = await profile.read_states(self.bus, Priority.STOP)
        except BUS_ERRORS as err:
            log.error(
                "relay belum bisa dimatikan saat mulai, dicoba lagi saat menjawab",
                extra=fields(device=device, err=err),
            )
            return False
        self.initialized[device] = True
        for name in self._outputs_of(device):
            st = self.states[name]
            self._cancel_timer(name)
            st.deadline = None
        changed = self._apply_readback(device, states)
        stuck = [ch for ch, on in enumerate(states) if on]
        for ch in stuck:
            self._mismatch(device, ch, wanted=False, actual=True, cause="boot")
        log.info("relay dimatikan semua", extra=fields(device=device, stuck=stuck or None))
        if changed:
            self._notify(device)
        return not stuck

    # --- tulis --------------------------------------------------------------------------------

    async def set(
        self,
        name: str,
        on: bool,
        *,
        duration_s: float | None = None,
        priority: Priority = Priority.COMMAND,
        cause: str = "command",
    ) -> SetResult:
        """Tulis satu output lalu baca balik. ON memasang watchdog durasi (S2)."""
        out = self.config.outputs[name]
        profile = self.profiles[out.device]
        st = self.states[name]
        changed = False
        async with self._lock:
            if not on:
                self._cancel_timer(name)
                st.deadline = None
            if on and not self.initialized[out.device] and not await self._init_device(out.device):
                return self._offline(name)
            self._write_seq[out.device] += 1
            # Baca polling yang dimulai sesudah kenaikan di atas tapi dijalankan bus sesudah tulisan
            # ini melihat state baru sebelum model diperbarui; tanpa penanda ini ia dianggap
            # "ON tanpa perintah" (S19 palsu).
            self._writing[out.device] += 1
            try:
                try:
                    await profile.write(self.bus, out.ch, on, priority)
                    states = await profile.read_states(self.bus, priority)
                finally:
                    self._writing[out.device] -= 1
                    self._write_seq[out.device] += 1
            except asyncio.CancelledError:
                # Dibatalkan (agent berhenti) di tengah tulis: tulisan mungkin sudah sampai tapi
                # belum dibaca balik. State tidak diketahui = "mungkin ON" bagi safety, dan
                # pemanggil (mis. trolley) tetap mematikan output di `finally`-nya.
                st.on = None
                self._update_polling_pause()
                raise
            except BUS_ERRORS as err:
                log.error(
                    "tulis relay gagal",
                    extra=fields(output=name, on=on, cause=cause, err=err),
                )
                # Tulisan mungkin sudah sampai; polling berikutnya memastikan dan mematikannya.
                if st.on is not None:
                    st.on = None
                    changed = True
                    self._update_polling_pause()
                result = self._offline(name)
            else:
                changed = self._apply_readback(out.device, states)
                actual = states[out.ch]
                if actual != on:
                    await self._force_off(profile, out.ch)
                    self._mismatch(out.device, out.ch, wanted=on, actual=actual, cause=cause)
                    result = SetResult(
                        False,
                        st.on,
                        REASON_RELAY_MISMATCH,
                        f"saklar {self.label(name)} tidak menurut, periksa relay di panel",
                    )
                else:
                    if on:
                        self._arm_timer(name, duration_s)
                    log.info(
                        "output diubah",
                        extra=fields(
                            output=name, on=on, cause=cause, limit_s=self._remaining(name)
                        ),
                    )
                    result = SetResult(True, actual)
        if changed:
            self._notify(out.device)
        return result

    async def stop_all(self, names, cause: str) -> None:
        """Matikan output yang ON dengan prioritas STOP (S3).

        Output yang state-nya tidak diketahui dilewati: relaynya tidak menjawab, dan begitu menjawab
        lagi polling mematikan kanal yang ON tanpa perintah.
        """
        for name in names:
            if self.states[name].on is True:
                await self.set(name, False, priority=Priority.STOP, cause=cause)

    async def _force_off(self, profile: RelayProfile, ch: int) -> None:
        with contextlib.suppress(*BUS_ERRORS):
            await profile.write(self.bus, ch, False, Priority.STOP)
            states = await profile.read_states(self.bus, Priority.STOP)
            self._apply_readback(profile.name, states)

    def _offline(self, name: str) -> SetResult:
        device = self.config.outputs[name].device
        return SetResult(
            False,
            self.states[name].on,
            REASON_DEVICE_OFFLINE,
            f"{device_label(self.config, device)} tidak menjawab, periksa kabel dan dayanya",
        )

    # --- watchdog (S2) ------------------------------------------------------------------------

    def _arm_timer(self, name: str, duration_s: float | None) -> None:
        out = self.config.outputs[name]
        self._cancel_timer(name)
        limits = [x for x in (duration_s, out.max_on_s) if x is not None]
        st = self.states[name]
        if not limits:
            st.deadline = None
            return
        limit = min(limits)
        cut = out.max_on_s is not None and (duration_s is None or duration_s > out.max_on_s)
        st.deadline = self.clock.monotonic() + limit
        st.timer = asyncio.create_task(
            self._expire(name, limit, cut, duration_s), name=f"wd-{name}"
        )

    def _cancel_timer(self, name: str) -> None:
        st = self.states[name]
        if st.timer is not None and st.timer is not asyncio.current_task():
            st.timer.cancel()
        st.timer = None

    def _remaining(self, name: str) -> float | None:
        deadline = self.states[name].deadline
        return None if deadline is None else max(0.0, deadline - self.clock.monotonic())

    async def _expire(self, name: str, limit: float, cut: bool, duration_s: float | None) -> None:
        await self.clock.sleep(limit)
        result = await self.set(name, False, priority=Priority.STOP, cause="timer")
        if not cut or self.sink is None:
            return
        out = self.config.outputs[name]
        label = self.label(name)
        self.sink.publish_alert(
            self.logical_of[out.device],
            "OUTPUT_TIMEOUT",
            "WARNING",
            f"{capitalize(label)} dimatikan otomatis",
            f"{capitalize(label)} sudah menyala {limit:g} detik, batas paling lama. Alat "
            "mematikannya sendiri. Nyalakan lagi bila memang perlu.",
            actuator_code=out.actuator_code,
            metadata={
                "output": name,
                "maxOnSeconds": out.max_on_s,
                "requestedSeconds": duration_s,
                "stopped": result.ok,
            },
        )

    # --- baca balik ---------------------------------------------------------------------------

    def _outputs_of(self, device: str) -> list[str]:
        return [n for n, out in self.config.outputs.items() if out.device == device]

    def _apply_readback(self, device: str, states: list[bool]) -> bool:
        """Simpan hasil baca balik ke model. True bila ada output yang berubah."""
        changed = False
        now = self.clock.monotonic()
        for name in self._outputs_of(device):
            st = self.states[name]
            actual = states[self.config.outputs[name].ch]
            if st.on is not actual:
                if st.on is not None:
                    st.changed_at = now
                st.on = actual
                changed = True
        if changed:
            self._update_polling_pause()
        return changed

    def _update_polling_pause(self) -> None:
        travel = self.config.safety.travel
        if travel is None:
            return
        # Hanya RUN yang terbukti ON yang menjeda polling; state tak diketahui tidak boleh
        # menjeda selamanya, karena baca balik relay sendiri ikut antre sebagai polling.
        if self.states[travel.run].on is True:
            self.bus.pause_polling()
        else:
            self.bus.resume_polling()

    def _mismatch(self, device: str, ch: int, *, wanted: bool, actual: bool, cause: str) -> None:
        name = self._by_channel.get((device, ch))
        if name is not None:
            self.states[name].faulty = True
        log.error(
            "baca balik relay tidak cocok",
            extra=fields(device=device, ch=ch, output=name, wanted=wanted, actual=actual),
        )
        if self.sink is None:
            return
        what = self.label(name) if name else f"kanal R{ch} {device_label(self.config, device)}"
        self.sink.publish_alert(
            self.logical_of[device],
            "RELAY_MISMATCH",
            "CRITICAL",
            f"Saklar {what} tidak menurut",
            f"Saklar {what} tidak berubah seperti yang diperintahkan dan sekarang dikunci. "
            "Periksa relay di panel, lalu nyalakan ulang alat kendali.",
            actuator_code=self.config.outputs[name].actuator_code if name else None,
            metadata={
                "device": device,
                "modbusAddress": self.config.devices[device].address,
                "channel": ch,
                "output": name,
                "wanted": "ON" if wanted else "OFF",
                "actual": "ON" if actual else "OFF",
                "cause": cause,
            },
        )

    def _notify(self, device: str) -> None:
        for listener in self.listeners:
            listener(device)

    # --- polling relay (§4.6) -----------------------------------------------------------------

    async def poll(self) -> None:
        """Satu siklus: baca balik semua relay, cocokkan dengan model, baca IN1."""
        for device in self.profiles:
            if not self.initialized[device]:
                async with self._lock:
                    if not self.initialized[device]:
                        await self._init_device(device)
                if not self.initialized[device]:
                    self._alert_silent(device)
                    continue
            else:
                await self._check_device(device)
            await self._read_float(device)
        await self._enforce_float()

    async def _check_device(self, device: str) -> None:
        profile = self.profiles[device]
        seq = self._write_seq[device]
        try:
            states = await profile.read_states(self.bus, Priority.POLL)
        except BUS_ERRORS as err:
            log.debug("baca relay gagal", extra=fields(device=device, err=err))
            self._alert_silent(device)
            return
        if seq != self._write_seq[device] or self._writing[device]:
            return  # ada tulisan di tengah baca; hasil ini sudah basi
        unexpected: list[int] = []  # ON padahal tercatat OFF → S19
        unknown_on: list[int] = []  # ON setelah tulis yang gagal; bukan relay rusak
        for ch, actual in enumerate(states):
            name = self._by_channel.get((device, ch))
            expected = self.states[name].on if name else False
            if actual and expected is False:
                unexpected.append(ch)
            elif actual and expected is None:
                unknown_on.append(ch)
            elif not actual and expected is True:
                log.warning("output mati sendiri", extra=fields(device=device, output=name))
                assert name is not None
                self._cancel_timer(name)
                self.states[name].deadline = None
        if self._apply_readback(device, states):
            self._notify(device)
        # ON tanpa perintah agent tidak dibiarkan: dimatikan ke keadaan aman.
        for ch in unexpected + unknown_on:
            async with self._lock:
                self._write_seq[device] += 1
                await self._force_off(profile, ch)
            if ch in unexpected:
                self._mismatch(device, ch, wanted=False, actual=True, cause="poll")
            else:
                log.warning("kanal ON tanpa perintah dimatikan", extra=fields(device=device, ch=ch))
            self._notify(device)

    def _alert_silent(self, device: str) -> None:
        if self.sink is not None:
            alert_if_silent(self.config, self.bus, self.sink, device, self.logical_of[device])

    # --- float LOW (S8) -----------------------------------------------------------------------

    async def _read_float(self, device: str) -> None:
        float_cfg = self.config.inputs.float_low_ok
        if float_cfg.device != device:
            return
        try:
            closed = await self.profiles[device].read_input(
                self.bus, float_cfg.input, Priority.POLL
            )
        except BUS_ERRORS as err:
            log.debug("baca float gagal", extra=fields(device=device, err=err))
            return
        if closed != self.float_closed:
            log.info("float LOW berubah", extra=fields(closed=closed))
        self.float_closed = closed
        self.float_read_at = self.clock.monotonic()
        logical = self.logical_of[device]
        if self.sink is None:
            return
        if float_cfg.metric:
            # Angka 1/0, bukan boolean: interlock FLOAT_LOW backend menerima "1" (Kontrak §3).
            self.sink.publish_telemetry(logical, {float_cfg.metric: 1 if closed else 0})
        if not closed:
            self.sink.publish_alert(
                logical,
                "FLOAT_LOW",
                "WARNING",
                "Air di drum hampir habis",
                "Pompa siram dan pompa pupuk tidak bisa dinyalakan sampai drum terisi. Bila drum "
                "sudah penuh, periksa kabel pelampung.",
                metadata={"device": device, "input": float_cfg.input},
            )

    async def _enforce_float(self) -> None:
        if self.float_ok():
            return
        running = [n for n in sorted(self._float_blocked) if self.states[n].on is True]
        if running:
            log.warning(
                "float LOW terbuka/basi, output dimatikan",
                extra=fields(outputs=",".join(running), closed=self.float_closed),
            )
            await self.stop_all(running, cause="float")
