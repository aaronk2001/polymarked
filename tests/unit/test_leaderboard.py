from polymarket_agent_core.schemas import LeaderboardEntry


def test_entry_parses_camelcase():
    entry = LeaderboardEntry.model_validate({
        "proxyWallet": "0xabc",
        "name": "alice",
        "pnl": 1234.5,
        "vol": 99000.0,
        "xUsername": "@alice",
    })
    assert entry.proxy_wallet == "0xabc"
    assert entry.user_name == "alice"
    assert entry.pnl == 1234.5
    assert entry.x_username == "@alice"


def test_entry_tolerates_extra_fields():
    entry = LeaderboardEntry.model_validate({
        "proxyWallet": "0xabc",
        "unknown_future_field": 42,
    })
    assert entry.proxy_wallet == "0xabc"


def test_entry_rank_is_set_via_copy():
    base = LeaderboardEntry.model_validate({"proxyWallet": "0xabc"})
    ranked = base.model_copy(update={"rank": 5})
    assert ranked.rank == 5
    assert ranked.proxy_wallet == "0xabc"
