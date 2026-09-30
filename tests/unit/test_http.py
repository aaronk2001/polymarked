import httpx
import pytest
import respx
from polymarket_agent_core.http import PolymarketHttpClient


@pytest.mark.asyncio
async def test_get_json_returns_payload():
    with respx.mock(base_url="https://example.test") as mock:
        mock.get("/leaderboard").mock(
            return_value=httpx.Response(200, json=[{"proxyWallet": "0xabc"}])
        )
        async with PolymarketHttpClient(base_url="https://example.test") as c:
            data = await c.get_json("/leaderboard")
        assert data == [{"proxyWallet": "0xabc"}]


@pytest.mark.asyncio
async def test_get_json_retries_on_429():
    with respx.mock(base_url="https://example.test") as mock:
        mock.get("/leaderboard").mock(
            side_effect=[
                httpx.Response(429, headers={"Retry-After": "0"}),
                httpx.Response(200, json={"ok": True}),
            ]
        )
        async with PolymarketHttpClient(base_url="https://example.test", max_retries=2) as c:
            data = await c.get_json("/leaderboard")
        assert data == {"ok": True}
