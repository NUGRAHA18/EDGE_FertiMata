"""Entry point `python -m fertimata_edge --config ...`."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import signal
import sys
from collections.abc import Sequence

from . import __version__
from .app import Agent
from .config import Config, ConfigError, load_config
from .hal import HalError, create_hal
from .link import PahoTransport
from .logs import setup_logging
from .sdnotify import SystemdNotifier

log = logging.getLogger(__name__)

# Kode keluar: systemd me-restart untuk keduanya, tapi pesan di journald membedakan sebabnya.
EXIT_OK = 0
EXIT_CONFIG = 2
EXIT_HAL = 3


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="fertimata_edge", description="Edge agent FertiMata")
    parser.add_argument("--config", required=True, help="berkas fertimata.yaml")
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="menimpa agent.log_level di config",
    )
    parser.add_argument("--check-config", action="store_true", help="validasi config lalu keluar")
    parser.add_argument("--version", action="version", version=__version__)
    return parser.parse_args(argv)


def install_stop_handlers(loop: asyncio.AbstractEventLoop, stop: asyncio.Event) -> None:
    """SIGINT/SIGTERM → `stop`. Windows tidak punya `add_signal_handler`, pakai `signal.signal`."""
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except (NotImplementedError, RuntimeError):
            signal.signal(sig, lambda *_: loop.call_soon_threadsafe(stop.set))


async def _run(config: Config) -> int:
    try:
        hal = create_hal(config)
    except HalError as err:
        log.error("HAL tidak bisa dibuat: %s", err)
        return EXIT_HAL
    stop = asyncio.Event()
    install_stop_handlers(asyncio.get_running_loop(), stop)
    try:
        await Agent(
            config,
            hal,
            transport=PahoTransport(config.mqtt),
            notifier=SystemdNotifier.from_env(),
        ).run(stop)
    except HalError as err:
        log.error("galat hardware: %s", err)
        return EXIT_HAL
    return EXIT_OK


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        config = load_config(args.config)
    except ConfigError as err:
        print(f"Config ditolak: {err}", file=sys.stderr)
        return EXIT_CONFIG
    if args.check_config:
        print(f"Config valid: {args.config}")
        return EXIT_OK
    setup_logging(args.log_level or config.agent.log_level)
    with contextlib.suppress(KeyboardInterrupt):
        return asyncio.run(_run(config))
    return EXIT_OK
