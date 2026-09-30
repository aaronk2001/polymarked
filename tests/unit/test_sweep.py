import asyncio
import json

from polymarket_agent_ingester import sweep


class _Rep:
    def __init__(self, score, tier="green", trades=10, pnl=1000.0, flags=None):
        self.smart_score = score
        self.tier = tier
        self.trade_count = trades
        self.realized_pnl_total = pnl
        self.red_flags = flags or []


def test_sweep_ranks_desc_and_caps_top(monkeypatch):
    async def fake_cands(client, *, category, time_period, order_by, n):
        return [(f"0x{i:040x}", f"w{i}") for i in range(5)]

    async def fake_act(client, *, wallet, event_type=None, max_rows=0):
        return [{"wallet": wallet}]

    def fake_score(events):
        return _Rep(score=float(int(events[0]["wallet"], 16)))

    monkeypatch.setattr(sweep, "_candidates", fake_cands)
    monkeypatch.setattr(sweep, "fetch_all_wallet_activity", fake_act)
    monkeypatch.setattr(sweep, "compute_smart_score", fake_score)

    rows = asyncio.run(sweep.sweep_top_scores(client=None, candidates=5, top=3, concurrency=2))
    assert [r["wallet"] for r in rows] == [f"0x{i:040x}" for i in (4, 3, 2)]
    assert rows[0]["smart_score"] == 4.0


def test_sweep_empty_when_no_candidates(monkeypatch):
    async def fake_cands(client, *, category, time_period, order_by, n):
        return []

    monkeypatch.setattr(sweep, "_candidates", fake_cands)
    assert asyncio.run(sweep.sweep_top_scores(client=None)) == []


def test_write_top_scores_roundtrip(tmp_path):
    rows = [{"wallet": "0xabc", "name": "w", "smart_score": 9.1, "tier": "green",
             "trades": 5, "realized_pnl": 100.0, "red_flags": ["x"]}]
    p = sweep.write_top_scores(rows, str(tmp_path / "ts"))
    assert json.loads(p.read_text(encoding="utf-8"))[0]["wallet"] == "0xabc"
    assert (tmp_path / "ts.csv").exists()
