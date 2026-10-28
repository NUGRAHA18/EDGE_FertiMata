"""Sumber daya `MAINS`/`BATTERY`/`UNKNOWN`, respons PLN padam (S7), dan arus pompa (S10, S11).

Penentu sumber daya (Arsitektur §7.4):
- PZEM (`power.pzem_device`) diam sementara relay acuan (`power.reference_device`) menjawab →
  `BATTERY`, karena sisi ukur PZEM ditenagai PLN. Keduanya diam → `UNKNOWN` (gangguan bus).
- PZEM menjawab: tegangan < `outage_voltage_v` → `BATTERY`; kembali `MAINS` baru setelah
  ≥ `restore_voltage_v` (histeresis). Di antara keduanya state lama dipertahankan; tanpa state
  lama dianggap `BATTERY` (konservatif).
- Sebelum ada data, sumber daya `None`: status tidak memuat `powerSource` (Q7), dan output
  `blocked_on_backup_power` tetap ditolak `safety` sampai terbukti `MAINS`.

Setiap kali sumber daya menjadi bukan `MAINS`, output `blocked_on_backup_power` yang ON dimatikan
dengan prioritas STOP, tanpa menunggu backend. Pemantauan arus (§7.5) memakai arus total panel:
arus > baseline + margin saat pompa OFF = SSR short; kenaikan arus < `dry_run_ratio` × nominal
saat pompa ON = pompa kering → pompa dimatikan.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable

from .bus import BusManager, Priority
from .config import Config
from .hal import Clock
from .logs import fields
from .outputs import Outputs
from .poller import TelemetrySink

log = logging.getLogger(__name__)

MAINS = "MAINS"
BATTERY = "BATTERY"
UNKNOWN = "UNKNOWN"

SourceListener = Callable[[str], None]


def _number(value: float, digits: int = 1) -> str:
    # Angka untuk petani memakai koma desimal.
    return f"{value:.{digits}f}".replace(".", ",")


class PowerMonitor:
    def __init__(
        self,
        config: Config,
        bus: BusManager,
        outputs: Outputs,
        clock: Clock,
        sink: TelemetrySink | None = None,
    ) -> None:
        self.config = config
        self.power = config.power
        self.bus = bus
        self.outputs = outputs
        self.clock = clock
        self.sink = sink
        self.source: str | None = None
        self.voltage: float | None = None
        self.listeners: list[SourceListener] = []
        self._blocked = config.safety.expand(config.safety.blocked_on_backup_power)
        prefix = config.devices[self.power.pzem_device].metric_prefix
        self._voltage_key = f"{prefix}.voltage"
        self._current_key = f"{prefix}.current"
        self._short_reads = 0
        self._dry_reads = 0
        self._stop_pump = False
        self._wake = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        if self._current_checks_enabled() is False:
            log.warning("pemantauan arus pompa mati: baseline/nominal belum diisi (P-H11)")

    # --- siklus hidup -------------------------------------------------------------------------

    def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name="power")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    @property
    def tasks(self) -> list[asyncio.Task[None]]:
        return [self._task] if self._task is not None else []

    # --- masukan ------------------------------------------------------------------------------

    def on_read(self, device: str, values: dict[str, float]) -> None:
        """Listener poller: dipanggil setiap perangkat selesai dibaca."""
        if device != self.power.pzem_device:
            return
        if self._needs_stop():
            self._wake.set()  # stop sebelumnya gagal (relay diam): diulang tiap siklus PZEM
        voltage = values.get(self._voltage_key)
        if voltage is not None:
            self.voltage = voltage
            self.evaluate()
        current = values.get(self._current_key)
        if current is not None and self.source == MAINS:
            self._check_current(current)

    def on_device(self, device: str, responding: bool) -> None:
        """Listener bus manager: perangkat mulai menjawab / dinyatakan diam."""
        if device == self.power.pzem_device and not responding:
            # Tegangan lama tidak boleh dipakai lagi saat PZEM menjawab kembali.
            self.voltage = None
        if device in (self.power.pzem_device, self.power.reference_device):
            self.evaluate()

    def on_bus(self, available: bool) -> None:
        self.evaluate()

    # --- penentu sumber daya ------------------------------------------------------------------

    def decide(self) -> str | None:
        if self.bus.bus_available is False:
            return UNKNOWN
        pzem = self.bus.is_responding(self.power.pzem_device)
        reference = self.bus.is_responding(self.power.reference_device)
        if pzem is False:
            if reference is True:
                return BATTERY
            if reference is False:
                return UNKNOWN
            return self.source
        if self.voltage is None:
            return self.source
        if self.source == MAINS:
            return BATTERY if self.voltage < self.power.outage_voltage_v else MAINS
        if self.voltage >= self.power.restore_voltage_v:
            return MAINS
        if self.voltage < self.power.outage_voltage_v:
            return BATTERY
        return self.source or BATTERY

    def evaluate(self) -> None:
        new = self.decide()
        if new is None or new == self.source:
            return
        old, self.source = self.source, new
        log.log(
            logging.INFO if new == MAINS else logging.WARNING,
            "sumber daya berubah",
            extra=fields(old=old, new=new, voltage=self.voltage),
        )
        self._short_reads = self._dry_reads = 0
        for listener in self.listeners:
            listener(new)
        if new != MAINS:
            self._wake.set()

    # --- pemantauan arus pompa (§7.5) ---------------------------------------------------------

    def _current_checks_enabled(self) -> bool:
        p = self.power
        return (
            p.pump_output is not None
            and p.pump_baseline_a is not None
            and p.pump_nominal_a is not None
        )

    def _check_current(self, current: float) -> None:
        if not self._current_checks_enabled():
            return
        p = self.power
        assert p.pump_output is not None and p.pump_baseline_a is not None
        assert p.pump_nominal_a is not None
        st = self.outputs.states[p.pump_output]
        settled = self.clock.monotonic() - st.changed_at >= p.current_settle_s
        if st.on is None or not settled:
            self._short_reads = self._dry_reads = 0
            return
        if st.on is False:
            self._dry_reads = 0
            if p.ssr_short_margin_a is None:
                return
            limit = p.pump_baseline_a + p.ssr_short_margin_a
            self._short_reads = self._short_reads + 1 if current > limit else 0
            if self._short_reads >= p.anomaly_reads:
                self._alert_ssr_short(current, limit)
            return
        self._short_reads = 0
        rise = current - p.pump_baseline_a
        minimum = p.dry_run_ratio * p.pump_nominal_a
        self._dry_reads = self._dry_reads + 1 if rise < minimum else 0
        if self._dry_reads >= p.anomaly_reads:
            self._dry_reads = 0
            self._stop_pump = True
            self._wake.set()
            self._alert_dry_run(current, rise, minimum)

    def _pump_target(self) -> tuple[str, str]:
        pump = self.power.pump_output
        assert pump is not None
        out = self.config.outputs[pump]
        return self.outputs.logical_of[out.device], out.actuator_code

    def _alert_ssr_short(self, current: float, limit: float) -> None:
        log.error("arus pompa saat OFF", extra=fields(current=current, limit=limit))
        if self.sink is None:
            return
        device, actuator = self._pump_target()
        label = self.outputs.label(self.power.pump_output or "")
        self.sink.publish_alert(
            device,
            "SSR_SHORT",
            "CRITICAL",
            f"Saklar {label} rusak, pompa tidak bisa dimatikan",
            f"Arus listrik {_number(current, 2)} A padahal pompa dimatikan. Matikan RCBO pompa di "
            "panel dan hubungi teknisi.",
            actuator_code=actuator,
            metadata={
                "current": current,
                "baseline": self.power.pump_baseline_a,
                "limit": limit,
            },
        )

    def _alert_dry_run(self, current: float, rise: float, minimum: float) -> None:
        log.error("arus pompa terlalu kecil", extra=fields(current=current, rise=rise))
        if self.sink is None:
            return
        device, actuator = self._pump_target()
        label = self.outputs.label(self.power.pump_output or "")
        self.sink.publish_alert(
            device,
            "PUMP_DRY_RUN",
            "CRITICAL",
            f"{label[:1].upper()}{label[1:]} jalan tanpa air",
            "Pompa dimatikan karena arusnya terlalu kecil. Periksa air di sumber, selang, dan "
            "saringan pompa sebelum menyalakan lagi.",
            actuator_code=actuator,
            metadata={
                "current": current,
                "rise": round(rise, 3),
                "minimumRise": minimum,
                "nominal": self.power.pump_nominal_a,
            },
        )

    # --- tindakan -----------------------------------------------------------------------------

    def _needs_stop(self) -> bool:
        if self.source is None or self.source == MAINS:
            return False
        return any(self.outputs.state(name) is True for name in self._blocked)

    async def _run(self) -> None:
        while True:
            await self._wake.wait()
            self._wake.clear()
            if self._stop_pump and self.power.pump_output is not None:
                self._stop_pump = False
                await self.outputs.set(
                    self.power.pump_output, False, priority=Priority.STOP, cause="dry-run"
                )
            if self.source is not None and self.source != MAINS:
                # S7: hentikan lokal, tanpa menunggu backend.
                await self.outputs.stop_all(sorted(self._blocked), cause=f"power-{self.source}")
