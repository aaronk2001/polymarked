import asyncio
from types import SimpleNamespace

from polymarket_agent_bot import alerts

EV = SimpleNamespace(
    wallet="0x" + "a" * 40, title="Will it rain?", outcome="Yes", side="buy",
    price=0.42, size=100.0, usdc_size=42.0,
)
DEC = SimpleNamespace(decision="PAPER_FILLED", reason="virtual fill")


def test_format_without_note_has_no_narration_line():
    assert "💬" not in alerts._format(EV, DEC)


def test_format_appends_escaped_note():
    text = alerts._format(EV, DEC, "Betting on rain.")
    assert text.endswith("💬 _Betting on rain\\._")


async def test_narrate_returns_none_on_timeout(monkeypatch):
    async def _slow(*_):
        await asyncio.sleep(1)

    monkeypatch.setattr(alerts, "summarize_trade", _slow)
    monkeypatch.setattr(alerts, "_NARRATION_TIMEOUT_S", 0.01)
    assert await alerts._narrate(EV) is None


async def test_slow_narration_does_not_delay_next_decision(monkeypatch):
    recorded: list[float] = []
    two = asyncio.Event()
    loop = asyncio.get_running_loop()

    async def _record(ev):
        recorded.append(loop.time())
        if len(recorded) == 2:
            two.set()
        return DEC

    async def _slow(*_):
        await asyncio.sleep(0.5)

    async def _true(*_):
        return True

    async def _send(**_):
        pass

    monkeypatch.setattr(alerts, "record_decision", _record)
    monkeypatch.setattr(alerts, "summarize_trade", _slow)
    monkeypatch.setattr(alerts.rc, "get_bool", _true)
    monkeypatch.setattr(alerts, "load_settings", lambda: SimpleNamespace(
        telegram_owner_chat_id=1, telegram_trade_alerts_enabled=True))
    app = SimpleNamespace(bot=SimpleNamespace(send_message=_send))
    queue: asyncio.Queue = asyncio.Queue()
    stop = asyncio.Event()
    queue.put_nowait(EV)
    queue.put_nowait(EV)
    pump = asyncio.create_task(alerts.alert_pump(app, queue, stop))
    await asyncio.wait_for(two.wait(), timeout=2)
    stop.set()
    await pump
    assert recorded[1] - recorded[0] < 0.2
