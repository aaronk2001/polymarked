"""Run the wallet watcher daemon. Prints TradeDetected events to stdout."""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import signal
from datetime import UTC, datetime

from polymarket_agent_core.logging import configure_logging
from polymarket_agent_watcher import TradeDetected, WalletWatcher


async def _amain(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="run_watcher")
    p.add_argument("--once", action="store_true",
                   help="Single poll cycle then exit (for smoke tests).")
    args = p.parse_args(argv)

    configure_logging()
    queue: asyncio.Queue[TradeDetected] = asyncio.Queue()
    watcher = WalletWatcher(queue=queue)
    wallets = await watcher.load_active_follows()
    if not wallets:
        print("No active follows. Use scripts/follow_wallet.py to add one.")
        await watcher.aclose()
        return 0

    print(f"Watching {len(wallets)} wallet(s): {', '.join(w[:10] + '...' for w in wallets)}")
    stop = asyncio.Event()

    def _signal_handler() -> None:
        stop.set()

    loop = asyncio.get_running_loop()
    try:
        for sig in (signal.SIGINT, signal.SIGTERM):
            with contextlib.suppress(NotImplementedError):
                loop.add_signal_handler(sig, _signal_handler)
    except Exception:
        pass

    if args.once:
        for w in wallets:
            await watcher._poll_once(w)
        drained = 0
        while not queue.empty():
            ev = await queue.get()
            print(f"  TRADE  {ev.wallet[:10]}...  {ev.side:<4}  {ev.size:>10.2f}  ${ev.usdc_size:>10.2f}  {ev.title or ''}")
            drained += 1
        print(f"One poll cycle complete. {drained} TRADE events emitted.")
        await watcher.aclose()
        return 0

    watch_task = asyncio.create_task(watcher.watch(wallets))

    async def _printer() -> None:
        while not stop.is_set():
            try:
                ev = await asyncio.wait_for(queue.get(), timeout=1.0)
            except TimeoutError:
                continue
            now = datetime.now(UTC).strftime("%H:%M:%S")
            print(f"[{now}]  {ev.wallet[:10]}...  {ev.side:<4}  size={ev.size:.2f}  ${ev.usdc_size:.2f}  {ev.title or ''}")

    printer_task = asyncio.create_task(_printer())
    await stop.wait()
    await watcher.aclose()
    printer_task.cancel()
    watch_task.cancel()
    return 0


def cli_entry() -> None:
    raise SystemExit(asyncio.run(_amain()))


if __name__ == "__main__":
    cli_entry()
