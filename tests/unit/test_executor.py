import pytest
from polymarket_agent_core.models import Follow
from polymarket_agent_executor.sizer import MIN_ORDER_USDC, size_trade


def _follow(**kw):
    base = dict(
        chat_id=1, proxy_wallet="0xabc", nickname=None,
        copy_ratio=0.01, max_per_trade_usd=25.0,
        daily_loss_cap_usd=100.0, paused=False, auto_execute=False,
    )
    base.update(kw)
    return Follow(**base)


def test_sizer_below_min_order():
    res = size_trade(target_usdc=50.0, follow=_follow(copy_ratio=0.01))  # $0.50 desired
    assert res.size_usdc == 0.0
    assert res.skip_reason == "BELOW_MIN_ORDER"


def test_sizer_capped_at_max_per_trade():
    res = size_trade(target_usdc=10000.0, follow=_follow(copy_ratio=0.10, max_per_trade_usd=25.0))
    assert res.size_usdc == 25.0
    assert res.skip_reason is None


def test_sizer_normal():
    res = size_trade(target_usdc=2000.0, follow=_follow(copy_ratio=0.01, max_per_trade_usd=25.0))
    assert res.size_usdc == pytest.approx(20.0)
    assert res.skip_reason is None


def test_sizer_at_minimum_passes():
    res = size_trade(target_usdc=100.0, follow=_follow(copy_ratio=0.01))
    assert res.size_usdc >= MIN_ORDER_USDC
