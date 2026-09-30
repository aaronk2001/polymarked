"""Out-of-sample validation: does a wallet's CLV in an OLDER window predict its
copy-return in a LATER window? This is the decisive test — it separates a metric
that *describes* the past from one that *predicts* the future, and compares CLV's
predictive power head-to-head with the Smart Score.

    uv run python scripts/validate_clv.py --top 40 --span 120

train window = [now-2*span, now-span]  ->  CLV (both windows now resolved, so clean)
test window  = [now-span,  now]        ->  forward copy-return (the backtest)
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

import structlog

sys.path.insert(0, str(Path(__file__).resolve().parent))
from backtest import backtest_wallet  # noqa: E402
from clv_score import _spearman, clv_for_wallet  # noqa: E402
from polymarket_agent_core.config import load_settings  # noqa: E402
from polymarket_agent_core.http import PolymarketHttpClient  # noqa: E402
from polymarket_agent_core.logging import configure_logging  # noqa: E402

log = structlog.get_logger(__name__)


async def _amain(argv=None) -> int:
    s = load_settings()
    p = argparse.ArgumentParser(prog="validate_clv")
    p.add_argument("--top", type=int, default=40)
    p.add_argument("--span", type=int, default=120, help="window length in days")
    p.add_argument("--min-trades", type=int, default=50, help="min train-window trades to include")
    p.add_argument("--concurrency", type=int, default=6)
    args = p.parse_args(argv)
    configure_logging()

    rows = json.loads(Path("data/top_scores.json").read_text(encoding="utf-8"))[:args.top]
    wallets = [(r["wallet"].lower(), r.get("smart_score")) for r in rows]

    now = int(time.time())
    span = args.span * 86400
    train = (now - 2 * span, now - span)
    test = (now - span, now)
    kw = dict(bankroll=s.paper_starting_bankroll_usd, copy_ratio=s.default_copy_ratio,
              max_per_trade=s.default_max_per_trade_usd, max_pos_pct=s.max_position_pct,
              slip=s.slippage_bps / 10000.0)

    sem = asyncio.Semaphore(args.concurrency)

    async def one(client, w, sc):
        async with sem:
            try:
                clv = await clv_for_wallet(client, w, start_ts=train[0], end_ts=train[1], max_rows=2500)
                if clv is None:
                    return None
                bt = await backtest_wallet(client, w, start_ts=test[0], end_ts=test[1], **kw)
                return {"wallet": w, "score": sc, "clv_train": clv["clv_return_pct"],
                        "trades_train": clv["trades"], "ret_test": bt["return_pct"]}
            except Exception as e:
                log.warning("validate_failed", wallet=w, error=str(e))
                return None

    async with PolymarketHttpClient() as client:
        res = [r for r in await asyncio.gather(*[one(client, w, sc) for w, sc in wallets]) if r]

    res = [r for r in res if r["trades_train"] >= args.min_trades]
    res.sort(key=lambda r: r["clv_train"], reverse=True)
    if len(res) < 4:
        print(f"Only {len(res)} wallets had enough train-window activity — widen --span or --top.")
        return 1

    print(f"\nOut-of-sample: CLV over older {args.span}d  ->  copy-return over recent {args.span}d  "
          f"(n={len(res)}, min {args.min_trades} train trades)\n")
    print(f"{'CLVtrn':>7}  {'trds':>5}  {'TESTret':>8}  {'score':>5}  wallet")
    for r in res:
        print(f"{r['clv_train']:>+6.1f}%  {r['trades_train']:>5}  {r['ret_test']:>+7.1f}%  "
              f"{r['score']:>5.1f}  {r['wallet']}")

    clvs = [r["clv_train"] for r in res]
    rets = [r["ret_test"] for r in res]
    scores = [r["score"] for r in res if r["score"] is not None]
    rets_for_score = [r["ret_test"] for r in res if r["score"] is not None]
    rho_clv = _spearman(clvs, rets)
    rho_sc = _spearman(scores, rets_for_score)
    h = len(res) // 2
    top, bot = res[:h], res[h:]
    mt = sum(r["ret_test"] for r in top) / len(top)
    mb = sum(r["ret_test"] for r in bot) / len(bot)
    print(f"\nSpearman(past CLV, forward return)    = {rho_clv:+.2f}   <- does CLV predict?")
    print(f"Spearman(Smart Score, forward return) = {rho_sc:+.2f}   <- the old metric, same test")
    print(f"\nForward return — top-half by past CLV: {mt:+.1f}%   bottom-half: {mb:+.1f}%   "
          f"(spread {mt - mb:+.1f}pp)")
    print("  positive CLV corr + positive spread = CLV genuinely predicts forward copy returns.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_amain()))
