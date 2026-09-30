from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", ".env.local"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    database_url: str = "sqlite+aiosqlite:///./data/polymarked.db"

    telegram_bot_token: str = ""
    telegram_owner_chat_id: int = 0

    polymarket_private_key: str = ""
    polymarket_funder_address: str = ""
    polymarket_chain_id: int = 137
    polymarket_signature_type: int = 0
    polymarket_api_key: str = ""
    polymarket_api_secret: str = ""
    polymarket_api_passphrase: str = ""

    # CLOB host + RPC. Override for testnet / alternative gateways.
    clob_host: str = "https://clob.polymarket.com"
    gamma_host: str = "https://gamma-api.polymarket.com"  # market resolutions (closed markets)
    polygon_rpc_url: str = "https://polygon-rpc.com"

    trade_mode: Literal["off", "paper", "live"] = "off"

    default_copy_ratio: float = 0.01
    default_max_per_trade_usd: float = 25.0
    default_daily_loss_cap_usd: float = 100.0

    # Min order size below which a trade is skipped entirely.
    min_order_usd: float = 1.0

    # Telegram updates. `telegram_alerts_enabled` is the master switch (gates the
    # periodic profit summary). Per-trade alerts are a separate, default-OFF switch
    # so the bot doesn't fire a message on every mirrored trade.
    telegram_alerts_enabled: bool = True
    telegram_trade_alerts_enabled: bool = False  # per-trade alert on each fill (off = no spam)
    telegram_profit_update_interval_seconds: int = 0  # 0 = off; >0 = DM owner a balance summary on cadence
    profit_alert_threshold_usd: float = 0.0  # also DM when total PnL moves by >= this since last update (0 = off)

    # Stop-loss: auto-close any open position whose live mark falls to/under this
    # price (0 = off). "Stop @ $0" — books the loss + frees the position.
    stop_loss_price: float = 0.0

    # Realism + portfolio risk
    slippage_bps: float = 50.0       # haircut on paper fill prices (50 = 0.5% worse)
    max_position_pct: float = 5.0    # cap one position at this % of bankroll (0 = off)
    mirror_exits: bool = True        # when a followed wallet SELLs a held asset, exit it too
    # Copy-positions filters — skip the target's dust + near-resolved markets
    copy_min_value_usd: float = 50.0
    copy_skip_price_low: float = 0.05
    copy_skip_price_high: float = 0.95

    # Favorite-longshot "value" strategy (own paper book, chat_id = -1). Buys the
    # token priced in the strong-favorite band — the side the bias underprices.
    # The band/spread defaults are the ONLY config that survived cost-aware,
    # out-of-sample replay (scripts/value_replay.py): broad 0.55-0.85 LOST ~2%/bet
    # after costs; the robust, both-cohort-positive zone is 0.80-0.92 (+4% recent /
    # +2% OOS at a 2c haircut). Edge is real but small — keep the spread gate tight.
    # Off by default; flip value_enabled + a scan interval in Settings to run it.
    value_enabled: bool = False
    value_scan_interval_seconds: int = 0  # 0 = off; >0 = scan open markets on this cadence
    value_buy_low: float = 0.80           # only buy tokens priced in [low, high]
    value_buy_high: float = 0.92
    value_min_liquidity_usd: float = 5000.0   # gamma liquidity floor (skip thin books)
    value_min_volume24_usd: float = 1000.0    # gamma 24h volume floor
    value_max_spread: float = 0.02            # skip if best ask - best bid exceeds this (edge is thin)
    value_order_usd: float = 25.0             # $ per value bet
    value_max_open: int = 40                  # cap concurrent open value positions
    value_scan_limit: int = 150               # how many top-volume open markets to scan
    value_min_hours_to_end: float = 12.0      # skip markets resolving sooner than this

    # Auto-sweep: periodically re-scrape + re-score the leaderboard into
    # data/top_scores.json (the Top-by-Smart-Score panel + /follow_top source).
    # PURE OPS, NOT AN EDGE: Smart Score does not predict forward copy return
    # (backtest.py +0.09 Spearman OOS) and copy_latency.py shows even a perfect
    # instant mirror is net-negative in both cohorts — so the sweep only keeps the
    # ranking fresh, and auto-follow is watch-only. Off by default.
    sweep_enabled: bool = False
    sweep_interval_seconds: int = 0       # 0 = off; >0 = re-sweep on this cadence (it's slow — use hours)
    sweep_candidates: int = 200           # leaderboard pool size to score each sweep
    sweep_top_n: int = 50                 # how many ranked wallets to write
    sweep_max_rows: int = 2000            # activity rows fetched per wallet when scoring
    sweep_auto_follow: bool = False       # after a sweep, also follow (watch-only) the top N
    sweep_follow_n: int = 10              # how many to auto-follow when sweep_auto_follow is on

    paper_starting_bankroll_usd: float = 10000.0

    # Live mark-to-market + equity history for the paper book.
    mark_cache_ttl_seconds: int = 30
    mark_negative_ttl_seconds: int = 3600  # resolved/dead markets stay dead; don't re-probe each tick
    mark_max_tokens: int = 60  # live-mark + resolution-check the N largest positions; rest at cost basis
    active_dust_usd: float = 1.0  # positions worth less than this are hidden from the active list/count
    resolution_negative_ttl_seconds: int = 1800  # how long an "open / not found" gamma result is cached
    equity_snapshot_interval_seconds: int = 60
    settle_resolved_interval_seconds: int = 0  # 0 = off; >0 auto-settles resolved positions on that cadence

    watch_poll_interval_seconds: int = 15
    watch_max_backfill_hours: int = 24

    llm_enabled: bool = True
    llm_base_url: str = "http://127.0.0.1:11434"
    llm_model: str = "qwen2.5:3b-instruct-q4_K_M"

    api_host: str = "127.0.0.1"
    api_port: int = 8765

    log_level: str = "INFO"
    log_format: Literal["json", "console"] = "json"


@lru_cache(maxsize=1)
def load_settings() -> Settings:
    return Settings()


def reset_settings_cache() -> None:
    load_settings.cache_clear()
