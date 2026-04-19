"""Entrypoint: load config, spawn one worker per account, handle signals."""
from __future__ import annotations

import asyncio
import os
import signal
import sys

from dotenv import load_dotenv

from .account_worker import AccountWorker
from .config import load_config
from .utils.logger import configure_logging, get_logger


async def _main() -> int:
    load_dotenv()
    configure_logging(os.environ.get("LOG_LEVEL", "INFO"))
    log = get_logger("main")

    try:
        cfg = load_config()
    except Exception as exc:
        log.error("config_load_failed", error=str(exc))
        return 2

    workers = [AccountWorker(cfg=a) for a in cfg.accounts]
    log.info("starting", accounts=[a.name for a in cfg.accounts])

    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    def _handle_signal(sig: int) -> None:
        log.info("signal_received", signal=sig)
        stop_event.set()
        for w in workers:
            w.stop()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _handle_signal, sig)
        except NotImplementedError:
            # Windows fallback
            signal.signal(sig, lambda s, _f: _handle_signal(s))

    tasks = [asyncio.create_task(w.run(), name=f"worker:{w.cfg.name}") for w in workers]
    try:
        await stop_event.wait()
    finally:
        for w in workers:
            w.stop()
        await asyncio.gather(*tasks, return_exceptions=True)
    log.info("shutdown_complete")
    return 0


def main() -> None:
    try:
        import uvloop  # type: ignore

        uvloop.install()
    except ImportError:
        pass
    sys.exit(asyncio.run(_main()))


if __name__ == "__main__":
    main()
