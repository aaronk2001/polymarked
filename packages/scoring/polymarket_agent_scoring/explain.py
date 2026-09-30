from __future__ import annotations

from .smart_score import RED_FLAG_DEDUCTIONS, WEIGHTS, ScoreReport

REASONS: dict[str, str] = {
    "profit_factor": "ratio of gross wins to gross losses",
    "sharpe_like": "mean per-trade return divided by stdev",
    "win_rate": "fraction of profitable trades, sample-size adjusted",
    "max_drawdown": "worst peak-to-trough on cumulative PnL",
    "trade_count": "log-scale sample-size confidence",
    "calibration_error": "edge vs market price (Phase 1 placeholder)",
}

FLAG_REASONS: dict[str, str] = {
    "arb_bot": ">=80% of trades have a hedge counter-trade within 60s",
    "single_trade_survivor": "top trade > 50% of total realized PnL",
    "recency_drought": "no trade in the last 30 days",
    "low_effective_volume": "lifetime risk-adjusted USDC volume below $500 (cap at 30)",
}


def explain(report: ScoreReport, wallet: str) -> str:
    lines: list[str] = []
    lines.append(f"Wallet:       {wallet}")
    lines.append(f"Smart Score:  {report.smart_score:.1f}  ({report.tier.upper()})")
    lines.append(f"Trades:       {report.trade_count}")
    lines.append(f"Realized PnL: ${report.realized_pnl_total:,.2f}")
    lines.append("")
    lines.append("Components:")
    for k in WEIGHTS:
        c = report.components[k]
        lines.append(
            f"  {k:<22}  raw={c.raw:>10.4f}  norm={c.normalized:>6.1f}  "
            f"weight={WEIGHTS[k]:.2f}  --  {REASONS[k]}"
        )
    lines.append("")
    if report.red_flags:
        lines.append("Red flags:")
        for f in report.red_flags:
            ded = RED_FLAG_DEDUCTIONS.get(f)
            mark = f"{ded:+.0f}" if ded is not None else "cap@30"
            lines.append(f"  {f:<25}  {mark}  {FLAG_REASONS.get(f, '')}")
    else:
        lines.append("Red flags: none")
    return "\n".join(lines)
