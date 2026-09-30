import json

from polymarket_agent_executor.spreads import _best
from polymarket_agent_executor.value import _candidates


def _mk(prices, **over):
    """A gamma open-market dict that passes every gate by default."""
    m = {
        "enableOrderBook": True,
        "clobTokenIds": json.dumps(["TOK0", "TOK1"]),
        "outcomePrices": json.dumps(prices),
        "liquidityNum": 50000.0,
        "volume24hr": 20000.0,
        "endDate": "2099-01-01T00:00:00Z",
        "question": "Q?",
        "slug": "q",
        "conditionId": "C",
    }
    m.update(over)
    return m


# band used throughout: strong-favorite 0.80-0.92
LO, HI, LIQ, VOL, HRS = 0.80, 0.92, 5000.0, 1000.0, 12.0


def _scan(markets):
    return _candidates(markets, LO, HI, LIQ, VOL, HRS)


def test_picks_token0_when_in_band():
    cands = _scan([_mk(["0.85", "0.15"])])
    assert len(cands) == 1 and cands[0].token == "TOK0"


def test_picks_token1_when_token0_is_longshot():
    # token0 priced 0.15 (longshot) -> fade it by buying token1 at 0.85
    cands = _scan([_mk(["0.15", "0.85"])])
    assert len(cands) == 1 and cands[0].token == "TOK1"


def test_skips_coinflip_below_band():
    assert _scan([_mk(["0.50", "0.50"])]) == []


def test_skips_near_certain_above_band():
    assert _scan([_mk(["0.95", "0.05"])]) == []


def test_skips_non_binary_market():
    m = _mk(["0.85", "0.10", "0.05"], clobTokenIds=json.dumps(["A", "B", "C"]))
    assert _scan([m]) == []


def test_skips_no_orderbook():
    assert _scan([_mk(["0.85", "0.15"], enableOrderBook=False)] ) == []


def test_skips_thin_liquidity():
    assert _scan([_mk(["0.85", "0.15"], liquidityNum=100.0)]) == []


def test_skips_low_volume():
    assert _scan([_mk(["0.85", "0.15"], volume24hr=10.0)]) == []


def test_skips_near_resolution():
    assert _scan([_mk(["0.85", "0.15"], endDate="2000-01-01T00:00:00Z")]) == []


def test_at_most_one_side_per_market():
    # both tokens technically parseable but only the in-band one is taken
    cands = _scan([_mk(["0.88", "0.12"])])
    assert len(cands) == 1


# ---- spreads._best ----

def test_best_picks_top_of_book():
    book = {"bids": [{"price": "0.78", "size": "5"}, {"price": "0.80", "size": "3"}],
            "asks": [{"price": "0.83", "size": "4"}, {"price": "0.81", "size": "2"}]}
    assert _best(book) == (0.80, 0.81)


def test_best_none_when_one_sided():
    assert _best({"bids": [{"price": "0.80", "size": "5"}], "asks": []}) is None


def test_best_ignores_zero_size_levels():
    assert _best({"bids": [{"price": "0.80", "size": "0"}],
                  "asks": [{"price": "0.83", "size": "5"}]}) is None
