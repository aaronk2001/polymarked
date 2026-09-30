import pytest
from polymarket_agent_scoring import compute_smart_score
from polymarket_agent_scoring.components import (
    max_drawdown,
    profit_factor,
    realized_pnl_per_trade,
    win_rate,
)
from polymarket_agent_scoring.red_flags import (
    detect_recency_drought,
    detect_single_trade_survivor,
)


def _trade(ts, side, asset, size, price):
    return {
        "type": "TRADE",
        "timestamp": ts,
        "side": side,
        "asset": asset,
        "size": size,
        "usdcSize": size * price,
        "price": price,
        "transactionHash": f"0x{ts}{side}{asset}",
        "eventSlug": "evt-" + asset[:1],
        "conditionId": "cond-" + asset[:1],
    }


def test_realized_pnl_basic_round_trip():
    trades = [
        _trade(1, "BUY", "A", 100, 0.30),
        _trade(2, "SELL", "A", 100, 0.50),
    ]
    realized = realized_pnl_per_trade(trades)
    assert len(realized) == 1
    assert realized[0] == 20.0  # (0.50 - 0.30) * 100


def test_profit_factor_normalization():
    pf = profit_factor([10.0, -5.0, 5.0])
    assert pf.raw == 3.0
    assert 70.0 < pf.normalized < 80.0  # 100 * 3 / 4 = 75


def test_win_rate_with_no_trades():
    wr = win_rate([])
    assert wr.normalized == 0.0


def test_max_drawdown_monotonic_winning_streak():
    dd = max_drawdown([1.0, 2.0, 3.0])
    assert dd.raw == 0.0
    assert dd.normalized == 100.0


def test_recency_drought_when_no_recent_trade():
    old = [{"type": "TRADE", "timestamp": 1_700_000_000}]
    assert detect_recency_drought(old, now_ts=1_800_000_000) is True


def test_single_trade_survivor_flag():
    assert detect_single_trade_survivor([100.0, 5.0, 5.0]) is True
    assert detect_single_trade_survivor([10.0, 10.0, 10.0]) is False


def test_compute_smart_score_returns_full_report():
    trades = [
        _trade(1, "BUY", "A", 100, 0.30),
        _trade(2, "SELL", "A", 100, 0.50),
        _trade(3, "BUY", "B", 50, 0.40),
        _trade(4, "SELL", "B", 50, 0.30),
    ]
    report = compute_smart_score(trades)
    assert report.trade_count == 4
    assert report.realized_pnl_total == pytest.approx(15.0)
    assert report.tier in {"green", "yellow", "orange", "red"}
    assert "profit_factor" in report.components
    assert 0.0 <= report.smart_score <= 100.0
