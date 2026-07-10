"""
Kalshi Master Prediction Agent — orchestration layer.

Ties the full pipeline together for live (out-of-sample) operation:

    1. SENSE   — scrape the current week's daily FlixPatrol US Top 10.
    2. THINK   — generate the baseline Weekend Effect signal.
    3. OVERRIDE— query the TikTok Advanced Social Oracle for the Top 3
                 contenders and apply the ">2x Rule" Alpha Override.
    4. REFINE  — run the Oracle Ensemble (Wikipedia pageviews, Google
                 Trends, TMDB release velocity, YouTube trailer velocity)
                 for bounded confidence adjustments.
    5. ACT     — record the final signal into the paper trading ledger
                 (fractional-Kelly sized).
    6. RESOLVE — on Tuesdays, settle open trades against the official
                 Netflix weekly winner.

Keyed oracles (TMDB, YouTube) activate automatically when TMDB_API_KEY /
YOUTUBE_API_KEY are set in the environment; otherwise they are skipped.

Usage
-----
    python master_agent.py run                 # steps 1-4 (run on Sun/Mon)
    python master_agent.py resolve "<winner>"  # settle open trades (Tuesday)
    python master_agent.py status              # ledger performance summary

Every external dependency (FlixPatrol, Apify/TikTok) is fail-safe: a
sensory failure degrades gracefully to the baseline signal or a no-trade,
never a crash.
"""

from __future__ import annotations

import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), 'src')))

import argparse
import json
import logging
import sys
from datetime import date, datetime, timedelta, timezone

import pandas as pd

import config as cfg
from kalshi_client import KalshiMarketAnalyzer
from data_collector import (
    FlixPatrolScraper,
    GoogleTrendsOracle,
    TikTokOracle,
    TMDBOracle,
    WikipediaOracle,
    YouTubeOracle,
)
from paper_trader import PaperTrader
from strategy import (
    AlphaOverrideStrategy,
    OracleEnsemble,
    Signal,
    WeekendEffectStrategy,
)

logger = logging.getLogger("master_agent")

DEFAULT_ENTRY_PRICE = 0.55  # assumed market price until live Kalshi quotes are wired in


class MasterAgent:
    """The Master Prediction Agent: sense → think → override → act."""

    def __init__(
        self,
        category: str = cfg.CATEGORY_TV,
        country: str = "united-states",
        ledger_file: str = "data/paper_trades.json",
    ) -> None:
        self.category = category
        self.country = country
        self.scraper = FlixPatrolScraper()
        self.baseline = WeekendEffectStrategy()
        self.oracle = TikTokOracle()
        self.override = AlphaOverrideStrategy(volume_fn=self.oracle.get_tiktok_volume)
        self.wiki = WikipediaOracle()
        self.trends = GoogleTrendsOracle()
        self.tmdb = TMDBOracle()
        self.youtube = YouTubeOracle()
        self.ensemble = OracleEnsemble(
            wiki_fn=self.wiki.get_pageviews,
            trends_fn=self.trends.get_interest,
            tmdb_fn=self.tmdb.get_metadata if self.tmdb.enabled else None,
            youtube_fn=(
                self.youtube.get_trailer_velocity if self.youtube.enabled else None
            ),
        )
        if not self.tmdb.enabled:
            logger.info("TMDB oracle disabled (set TMDB_API_KEY to enable)")
        if not self.youtube.enabled:
            logger.info("YouTube oracle disabled (set YOUTUBE_API_KEY to enable)")
        self.trader = PaperTrader(
            initial_bankroll=cfg.KALSHI_BANKROLL, log_file=ledger_file
        )
        self.kalshi = KalshiMarketAnalyzer()

    # ------------------------------------------------------------------
    # Pipeline
    # ------------------------------------------------------------------

    def run_cycle(
        self,
        entry_price: float = DEFAULT_ENTRY_PRICE,
        force_trade: bool = False,
    ) -> Signal | None:
        """Execute one full sense→think→override→analyze→act cycle.

        When live Kalshi quotes are available, the real ask price replaces
        *entry_price* and the trade is only recorded when our edge
        (confidence − ask) clears ``config.KALSHI_MIN_EDGE`` — unless
        *force_trade* is set.

        Returns the final signal, or ``None`` when no signal was possible.
        """
        # --- 1. SENSE: current Netflix week (Monday → today) ---
        today = date.today()
        week_monday = today - timedelta(days=today.weekday())
        logger.info("Scraping FlixPatrol %s from %s to %s", self.country, week_monday, today)
        rows = self.scraper.scrape_date_range(week_monday, today, country=self.country)
        if not rows:
            logger.error("No FlixPatrol data collected — no trade this cycle.")
            return None

        df = pd.DataFrame(rows)
        self._persist_rankings(df)  # archive every scrape, signal or not
        df = df[df["category"] == self.category]
        if df.empty:
            logger.error("No %s rows in scraped data — no trade.", self.category)
            return None

        # --- 2. THINK: baseline Weekend Effect signal ---
        baseline_signal = self.baseline.generate_signal(df)
        if baseline_signal is None:
            logger.warning(
                "Baseline strategy returned no signal (weekend data missing?) — no trade."
            )
            return None
        logger.info(
            "Baseline: %r @ %.2f (%s)",
            baseline_signal.predicted_winner,
            baseline_signal.confidence,
            baseline_signal.signal_strength,
        )

        # --- Trading calendar: weekday runs capture data; Sat–Mon spend
        # Apify credit; only Sun/Mon (full weekend data, market still open)
        # record trades. --force bypasses both gates.
        dow = today.weekday()
        decision_day = dow in cfg.TRADE_DAYS or force_trade
        tiktok_day = dow in cfg.TIKTOK_DAYS or force_trade

        # --- 3. OVERRIDE: Top 3 contenders → TikTok >2x Rule ---
        contenders = self._latest_top_contenders(df)
        logger.info("Top %d contenders: %s", len(contenders), contenders)
        if tiktok_day:
            override_signal = self.override.apply(baseline_signal, contenders)
        else:
            logger.info(
                "TikTok oracle skipped (%s is not in TIKTOK_DAYS — capture-only).",
                today.strftime("%A"),
            )
            override_signal = baseline_signal
        if override_signal.predicted_winner != baseline_signal.predicted_winner:
            logger.info(
                "ALPHA OVERRIDE fired: %r → %r @ %.2f",
                baseline_signal.predicted_winner,
                override_signal.predicted_winner,
                override_signal.confidence,
            )

        # --- 4. REFINE: Oracle Ensemble confidence adjustments ---
        final_signal = self.ensemble.apply(
            override_signal, contenders, week_start=pd.Timestamp(week_monday)
        )
        if final_signal.confidence != override_signal.confidence:
            logger.info(
                "Ensemble adjusted confidence %.2f → %.2f",
                override_signal.confidence,
                final_signal.confidence,
            )

        # --- 4b. LLM DECISION: Kalshi Predictor ---
        import llm_agent
        payload = self._build_llm_payload(df, week_monday, today, contenders)
        logger.info("Calling LLM Agent for final prediction...")
        try:
            llm_response = llm_agent.run_llm_prediction(payload)
            conf = float(llm_response.get("confidence", final_signal.confidence))
            llm_winner = llm_response.get("predicted_winner", final_signal.predicted_winner)
            llm_reasoning = llm_response.get("reasoning", "")
            
            final_signal = Signal(
                predicted_winner=llm_winner,
                confidence=conf,
                signal_strength="strong" if conf > 0.8 else "medium" if conf >= 0.5 else "weak",
                reasoning=f"LLM Reasoning: {llm_reasoning}\n--- Ensemble reasoning ---\n{final_signal.reasoning}",
                week=final_signal.week,
                category=final_signal.category
            )
            logger.info("LLM Prediction: %r @ %.2f", final_signal.predicted_winner, final_signal.confidence)
        except Exception as e:
            logger.exception("LLM Agent failed: %s. Falling back to ensemble.", e)

        # --- 4b. CALIBRATE: shrink confidence toward the ledger's
        # empirical win rate on similar past signals (no-op until enough
        # trades have resolved). Hand-tuned constants lose to history.
        pre_calibration_confidence = final_signal.confidence
        calibrated = self.trader.get_calibrated_confidence(final_signal.confidence)
        if calibrated != final_signal.confidence:
            logger.info(
                "Ledger calibration adjusted confidence %.2f → %.2f",
                final_signal.confidence, calibrated,
            )
            final_signal.confidence = calibrated

        # --- 5. ANALYZE: live Kalshi market — prices, edge, EV ---
        market = None
        try:
            market = self.kalshi.analyze(
                contenders=contenders,
                predicted_winner=final_signal.predicted_winner,
                our_prob=final_signal.confidence,
                bankroll=self.trader.bankroll,
                category=self.category,
            )
        except Exception:  # noqa: BLE001 — market data is never fatal
            logger.exception("Kalshi market analysis failed — using assumed price.")

        # Pick the trade expression: YES on our winner, or NO on the
        # overpriced market favorite — whichever side has the better EV.
        entry_source = "assumed"
        edge = None
        side = "yes"
        target_title = final_signal.predicted_winner
        ticker = None
        if market is not None:
            for note in market.notes:
                logger.info("Kalshi: %s", note)
            if market.tradeable and market.best_side == "no":
                side = "no"
                target_title = market.fade_title
                entry_price = market.fade_no_price
                entry_source = "kalshi_no_price"
                edge = market.fade_edge
                ticker = market.fade_ticker
            elif market.winner_ask is not None:
                entry_price = market.winner_ask
                entry_source = "kalshi_ask"
                edge = market.edge
                ticker = market.winner_ticker

        # --- 6. ACT: record into the ledger (day-gated + edge-gated) ---
        should_trade = decision_day
        skip_reason = (
            ""
            if decision_day
            else f"capture-only day ({today.strftime('%A')}) — "
            f"trades are recorded Sun/Mon when weekend data is in"
        )
        if should_trade and market is not None and not market.tradeable:
            should_trade = force_trade
            skip_reason = (
                f"no side clears min edge {cfg.KALSHI_MIN_EDGE:.2f} with EV > 0"
            )

        trade = None
        if should_trade:
            trade = self.trader.record_signal(
                date=today.isoformat(),
                signal={
                    "predicted_winner": final_signal.predicted_winner,
                    "side": side,
                    "target_title": target_title,
                    "confidence": final_signal.confidence,
                    "entry_price": entry_price,
                    "entry_source": entry_source,
                    "edge": edge,
                    "kalshi_ticker": ticker,
                    "signal_strength": final_signal.signal_strength,
                    "reasoning": final_signal.reasoning,
                    "category": self.category,
                    "alpha_override": override_signal.predicted_winner
                    != baseline_signal.predicted_winner,
                    "baseline_prediction": baseline_signal.predicted_winner,
                    "baseline_confidence": baseline_signal.confidence,
                    "pre_ensemble_confidence": override_signal.confidence,
                    "pre_calibration_confidence": pre_calibration_confidence,
                },
            )
            logger.info(
                "Recorded trade #%d: %s %d contracts on %r at $%.2f (%s)",
                trade["id"],
                side.upper(),
                trade["contracts"],
                target_title,
                trade["entry_price"],
                entry_source,
            )
        else:
            logger.warning("NO TRADE: %s", skip_reason)

        # --- 7. SNAPSHOT: persist every variable for the dashboard ---
        self._write_snapshot(
            df=df,
            week_monday=week_monday,
            today=today,
            contenders=contenders,
            baseline_signal=baseline_signal,
            override_signal=override_signal,
            final_signal=final_signal,
            market=market,
            entry_price=entry_price,
            entry_source=entry_source,
            edge=edge,
            traded=should_trade,
            decision_day=decision_day,
            side=side,
            target_title=target_title,
            skip_reason=skip_reason,
            trade=trade,
        )
        return final_signal

    def _write_snapshot(self, **kw) -> None:
        """Write data/latest_cycle.json — the dashboard's single source of truth."""
        df = kw["df"].copy()
        df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
        final = kw["final_signal"]
        baseline = kw["baseline_signal"]
        override = kw["override_signal"]
        market = kw["market"]
        snapshot = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "week_monday": kw["week_monday"].isoformat(),
            "as_of_date": kw["today"].isoformat(),
            "category": self.category,
            "country": self.country,
            "daily_rankings": df[
                [c for c in ("date", "rank", "title", "category") if c in df.columns]
            ].to_dict(orient="records"),
            "contenders": kw["contenders"],
            "baseline": {
                "predicted_winner": baseline.predicted_winner,
                "confidence": baseline.confidence,
                "signal_strength": baseline.signal_strength,
                "breakdown": self.baseline.last_breakdown,
            },
            "alpha_override": {
                "fired": self.override.last_fired,
                "tiktok_volumes": self.override.last_volumes,
                "predicted_winner": override.predicted_winner,
                "confidence": override.confidence,
                "multiplier": cfg.ALPHA_OVERRIDE_MULTIPLIER,
            },
            "ensemble": self.ensemble.last_diagnostics,
            "final_signal": {
                "predicted_winner": final.predicted_winner,
                "confidence": final.confidence,
                "signal_strength": final.signal_strength,
                "reasoning": final.reasoning,
                "week": final.week,
            },
            "kalshi": market.to_dict() if market is not None else None,
            "decision": {
                "action": (
                    "TRADE" if kw["traded"]
                    else "CAPTURE_ONLY" if not kw["decision_day"]
                    else "NO_TRADE"
                ),
                "side": kw["side"],
                "target_title": kw["target_title"],
                "entry_price": kw["entry_price"],
                "entry_source": kw["entry_source"],
                "edge": kw["edge"],
                "min_edge": cfg.KALSHI_MIN_EDGE,
                "contracts": (kw["trade"] or {}).get("contracts"),
                "bet_size": (kw["trade"] or {}).get("bet_size"),
                "skip_reason": kw["skip_reason"],
            },
            "ledger_summary": self.trader.get_performance_summary(),
        }
        path = cfg.DATA_DIR / "latest_cycle.json"
        path.write_text(
            json.dumps(snapshot, indent=2, default=str), encoding="utf-8"
        )
        logger.info("Cycle snapshot written to %s", path)
        try:
            from dashboard import generate_dashboard

            out = generate_dashboard()
            logger.info("Dashboard updated: %s", out)
        except Exception:  # noqa: BLE001 — a dashboard bug must never block trading
            logger.exception("Dashboard generation failed (non-fatal).")

    def resolve(self, actual_winner: str, signal_date: str | None = None) -> None:
        """Settle open trades against the official Netflix weekly winner."""
        if signal_date:
            resolved = self.trader.record_outcome(signal_date, actual_winner)
        else:
            resolved = []
            for trade in list(self.trader.get_open_positions()):
                resolved.extend(
                    self.trader.record_outcome(trade["signal_date"], actual_winner)
                )
        for t in resolved:
            outcome = "WON" if t["won"] else "LOST"
            logger.info(
                "Trade #%d %s: predicted %r, actual %r, P&L $%.2f",
                t["id"], outcome, t["predicted_winner"], t["actual_winner"], t["pnl"],
            )
        if not resolved:
            logger.warning("No open trades matched for resolution.")

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _persist_rankings(self, df: pd.DataFrame) -> None:
        """Append scraped rankings to data/daily_rankings.csv, deduplicated.

        Every run archives what it saw — the historical daily record grows
        even on capture-only weekdays when no signal is possible.
        """
        try:
            path = cfg.DATA_DIR / "daily_rankings.csv"
            new = df.copy()
            new["date"] = pd.to_datetime(new["date"]).dt.strftime("%Y-%m-%d")
            if path.exists():
                old = pd.read_csv(path, dtype=str)
                new = new.astype(str)
                merged = pd.concat([old, new], ignore_index=True)
            else:
                merged = new.astype(str)
            key_cols = [
                c for c in ("date", "category", "rank", "country") if c in merged.columns
            ]
            merged = merged.drop_duplicates(subset=key_cols or None, keep="last")
            merged.to_csv(path, index=False)
            logger.info("Rankings archived to %s (%d rows total)", path, len(merged))
        except Exception:  # noqa: BLE001 — archiving must never block the cycle
            logger.exception("Failed to archive daily rankings (non-fatal).")

    def _latest_top_contenders(self, df: pd.DataFrame) -> list[str]:
        """Top 3 titles by rank on the most recent scraped day."""
        df = df.copy()
        df["date"] = pd.to_datetime(df["date"])
        latest = df[df["date"] == df["date"].max()].sort_values("rank")
        return latest["title"].head(cfg.TIKTOK_MAX_CONTENDERS).tolist()

    def _build_llm_payload(self, df: pd.DataFrame, week_monday: date, today: date, contenders: list[str]) -> dict:
        import promo_agent
        payload = {
            "context": {
                "netflix_week_start": week_monday.isoformat(),
                "current_date": today.isoformat(),
                "category": self.category,
                "web_scouted_promos": promo_agent.fetch_promoted_movies(),
            },
            "contenders": []
        }
        for title in contenders:
            title_df = df[df["title"] == title]
            weekend_df = title_df[title_df["date"].dt.dayofweek.isin({5, 6})]
            weekday_df = title_df[title_df["date"].dt.dayofweek.isin({0, 1, 2, 3, 4})]
            weekend_rank_avg = float(weekend_df["rank"].mean()) if not weekend_df.empty else None
            weekday_rank_avg = float(weekday_df["rank"].mean()) if not weekday_df.empty else None
            days_at_number_one = len(title_df[title_df["rank"] == 1])
            
            tiktok = self.oracle.get_tiktok_volume(title)
            wiki = self.wiki.get_pageviews(title, self.category)
            
            trends_dict = self.trends.get_interest([title])
            trends = trends_dict.get(title) if trends_dict else None
            
            tmdb_meta = self.tmdb.get_metadata(title, self.category) if self.tmdb.enabled else {}
            youtube_meta = self.youtube.get_trailer_velocity(title) if self.youtube.enabled else {}
            
            contender_data = {
                "title": title,
                "flixpatrol_data": {
                    "weekend_rank_avg": weekend_rank_avg,
                    "weekday_rank_avg": weekday_rank_avg,
                    "days_at_number_one": days_at_number_one
                },
                "oracle_data": {
                    "tiktok_weekly_views": tiktok,
                    "wikipedia_7d_pageviews": wiki,
                    "google_trends_score": trends,
                    "youtube_trailer_views_per_day": youtube_meta.get("views_per_day") if youtube_meta else None,
                    "tmdb_release_date": tmdb_meta.get("release_date") if tmdb_meta else None,
                    "tmdb_runtime_min": tmdb_meta.get("runtime_min") if tmdb_meta else None
                }
            }
            payload["contenders"].append(contender_data)
        return payload


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
    )
    parser = argparse.ArgumentParser(description="Kalshi Master Prediction Agent")
    sub = parser.add_subparsers(dest="command", required=True)

    run_p = sub.add_parser("run", help="Run one full prediction cycle")
    run_p.add_argument(
        "--category", default=cfg.CATEGORY_TV, choices=cfg.CATEGORIES
    )
    run_p.add_argument(
        "--entry-price", type=float, default=DEFAULT_ENTRY_PRICE,
        help="Fallback YES price when live Kalshi quotes are unavailable",
    )
    run_p.add_argument(
        "--force", action="store_true",
        help="Bypass the trading calendar and edge gates: run TikTok and "
        "record the trade regardless of day or edge",
    )

    res_p = sub.add_parser("resolve", help="Settle open trades (run on Tuesday)")
    res_p.add_argument("actual_winner", help="Official Netflix weekly #1 title")
    res_p.add_argument("--date", default=None, help="Signal date to resolve (ISO)")

    sub.add_parser("status", help="Show ledger performance summary")

    mkt_p = sub.add_parser("markets", help="Show live Kalshi Netflix market quotes")
    mkt_p.add_argument(
        "--category", default=cfg.CATEGORY_TV, choices=cfg.CATEGORIES
    )

    sub.add_parser("dashboard", help="Regenerate output/dashboard.html")

    args = parser.parse_args()

    if args.command == "run":
        agent = MasterAgent(category=args.category)
        signal = agent.run_cycle(entry_price=args.entry_price, force_trade=args.force)
        if signal is None:
            print("No trade this cycle.")
            return 1
        print(f"\nFinal prediction : {signal.predicted_winner}")
        print(f"Confidence       : {signal.confidence}")
        print(f"Strength         : {signal.signal_strength}")
        print(f"Reasoning:\n{signal.reasoning}")
        print("\nDashboard        : output/dashboard.html")
    elif args.command == "resolve":
        agent = MasterAgent()
        agent.resolve(args.actual_winner, signal_date=args.date)
        print(agent.trader.get_performance_summary())
    elif args.command == "status":
        agent = MasterAgent()
        for key, value in agent.trader.get_performance_summary().items():
            print(f"{key:>18}: {value}")
    elif args.command == "markets":
        analyzer = KalshiMarketAnalyzer()
        event = analyzer.find_netflix_event(args.category)
        if not event:
            print("No open Kalshi Netflix event found.")
            return 1
        print(f"\n{event.get('title')}  [{event.get('event_ticker')}]\n")
        markets = event.get("markets") or []
        print(f"{'Outcome':<42} {'Bid':>6} {'Ask':>6} {'Last':>6} {'Vol':>8} {'OI':>8}")
        for m in markets:
            label = m.get("yes_sub_title") or m.get("title") or m.get("ticker", "")
            fmt = lambda v: f"{v/100:.2f}" if v else "—"
            print(
                f"{label[:40]:<42} {fmt(m.get('yes_bid')):>6} {fmt(m.get('yes_ask')):>6}"
                f" {fmt(m.get('last_price')):>6} {m.get('volume') or 0:>8}"
                f" {m.get('open_interest') or 0:>8}"
            )
    elif args.command == "dashboard":
        from dashboard import generate_dashboard

        print(f"Dashboard written to {generate_dashboard()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
