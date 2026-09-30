from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from polymarket_agent_executor import decisions, system_state

from .test_executor import _follow


@pytest.fixture
def kv(monkeypatch):
    store: dict[str, str] = {}

    async def _get(key):
        return store.get(key)

    async def _set(key, value):
        store[key] = value

    monkeypatch.setattr(system_state, "_get", _get)
    monkeypatch.setattr(system_state, "_set", _set)
    return store


def _env_mode(monkeypatch, mode):
    monkeypatch.setattr(system_state, "load_settings", lambda: SimpleNamespace(trade_mode=mode))


@pytest.mark.asyncio
async def test_live_override_rejected_unless_env_is_live(kv, monkeypatch):
    _env_mode(monkeypatch, "paper")
    with pytest.raises(ValueError, match="TRADE_MODE=live"):
        await system_state.set_mode_override("live")
    assert await system_state.get_effective_mode() == "paper"


@pytest.mark.asyncio
async def test_stored_live_override_is_ignored(kv, monkeypatch):
    _env_mode(monkeypatch, "off")
    kv[system_state.MODE_KEY] = "live"  # e.g. written by an older build
    assert await system_state.get_effective_mode() == "off"


@pytest.mark.asyncio
async def test_override_can_step_down_and_back_to_env_live(kv, monkeypatch):
    _env_mode(monkeypatch, "live")
    await system_state.set_mode_override("paper")
    assert await system_state.get_effective_mode() == "paper"
    await system_state.set_mode_override("live")
    assert await system_state.get_effective_mode() == "live"


def test_api_refuses_live_mode(kv, monkeypatch):
    _env_mode(monkeypatch, "paper")
    from polymarket_agent_api.main import app

    r = TestClient(app).post("/api/v1/mode", json={"mode": "live"})
    assert r.status_code == 400
    assert "TRADE_MODE=live" in r.json()["detail"]


@pytest.mark.asyncio
@pytest.mark.parametrize("auto_execute, expect_live_call", [(False, False), (True, True)])
async def test_live_order_requires_per_wallet_opt_in(monkeypatch, auto_execute, expect_live_call):
    follow = _follow(copy_ratio=0.01, auto_execute=auto_execute)
    live_calls = []

    async def _mode():
        return "live"

    async def _follow_for(*_):
        return follow

    async def _no_risk(**_):
        return None

    async def _not_paused():
        return False

    async def _float(_key, default):
        return default

    async def _execute_live(ev, size_usd, f):
        live_calls.append(size_usd)
        return decisions.Decision("LIVE_FILLED", "stub", size_usd, ev.price, ev.side)

    class _Session:
        def add(self, _):
            pass

    @asynccontextmanager
    async def _session_scope():
        yield _Session()

    monkeypatch.setattr(decisions, "get_effective_mode", _mode)
    monkeypatch.setattr(decisions, "_load_follow", _follow_for)
    monkeypatch.setattr(decisions, "check_caps", _no_risk)
    monkeypatch.setattr(decisions, "is_globally_paused", _not_paused)
    monkeypatch.setattr(decisions.rc, "get_float", _float)
    monkeypatch.setattr(decisions, "_execute_live", _execute_live)
    monkeypatch.setattr(decisions, "session_scope", _session_scope)

    # Sizes to $1.00: the min order, and exactly what the removed $1 threshold auto-executed.
    ev = SimpleNamespace(wallet="0xabc", usdc_size=100.0, price=0.5, side="BUY",
                         asset="tok", transaction_hash="0xtx")
    d = await decisions.record_decision(ev)

    assert bool(live_calls) is expect_live_call
    if not auto_execute:
        assert d.decision == "SKIPPED_DRY_RUN"
