"""Copy-trade execution: paper-mode by default, live via py-clob-client. Phase 4."""
from .copy_positions import CopyResult, copy_trader_positions
from .decisions import record_decision
from .marks import fetch_marks
from .paper import (
    PaperBalance,
    PaperPosition,
    SettlementResult,
    paper_balance,
    paper_cash,
    paper_snapshot,
    settle_resolved_positions,
    write_paper_fill,
)
from .resolutions import fetch_resolutions
from .risk import check_caps
from .sizer import MIN_ORDER_USDC, IntendedTrade, size_trade
from .system_state import (
    VALID_MODES,
    get_effective_mode,
    is_globally_paused,
    set_globally_paused,
    set_mode_override,
)
from .value import VALUE_CHAT_ID, ValueScanResult, scan_value_markets

__all__ = [
    "MIN_ORDER_USDC",
    "VALID_MODES",
    "VALUE_CHAT_ID",
    "IntendedTrade",
    "CopyResult",
    "ValueScanResult",
    "PaperBalance",
    "PaperPosition",
    "SettlementResult",
    "check_caps",
    "copy_trader_positions",
    "fetch_marks",
    "fetch_resolutions",
    "get_effective_mode",
    "is_globally_paused",
    "paper_balance",
    "paper_cash",
    "paper_snapshot",
    "record_decision",
    "scan_value_markets",
    "settle_resolved_positions",
    "set_globally_paused",
    "set_mode_override",
    "size_trade",
    "write_paper_fill",
]
