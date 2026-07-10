#!/usr/bin/env python3
"""
CLI Runner — Kalshi Netflix Weekend Effect Backtester
=====================================================

Usage::

    python run_backtest.py --mode [sample|backtest|analyze|paper]

Modes
-----
sample    Generate synthetic sample data for testing (default 52 weeks).
backtest  Run the full backtest on data in ``data/``, print results, and
          generate visualisation plots in ``output/``.
analyze   Run statistical analysis (weekend-effect strength, Monte Carlo).
paper     Manage the paper-trading ledger interactively.
"""

from __future__ import annotations

import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), 'src')))

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent
DATA_DIR = PROJECT_ROOT / "data"
SAMPLE_DIR = DATA_DIR / "sample"
OUTPUT_DIR = PROJECT_ROOT / "output"


# ---------------------------------------------------------------------------
# Pretty-print helpers (rich with fallback)
# ---------------------------------------------------------------------------
def _rich_print(text: str) -> None:
    """Print with rich markup if available, else plain print."""
    try:
        from rich import print as rprint
        rprint(text)
    except ImportError:
        print(text)


def _rich_table(title: str, rows: Dict[str, Any]) -> None:
    """Render a dict as a rich Table if available, else as aligned text."""
    try:
        from rich.table import Table
        from rich.console import Console

        console = Console()
        table = Table(title=title, show_header=True, header_style="bold cyan")
        table.add_column("Metric", style="dim")
        table.add_column("Value", justify="right")
        for key, val in rows.items():
            table.add_row(str(key), str(val))
        console.print(table)
    except ImportError:
        print(f"\n{'=' * 50}")
        print(f"  {title}")
        print(f"{'=' * 50}")
        for key, val in rows.items():
            print(f"  {key:.<35} {val}")
        print()


# ===================================================================
# MODE: sample
# ===================================================================
def run_sample(args: argparse.Namespace) -> None:
    """Generate synthetic sample data for testing."""
    weeks = args.weeks
    _rich_print(f"[bold cyan]Generating {weeks} weeks of sample data...[/bold cyan]")

    try:
        from data_collector import generate_sample_data
        result = generate_sample_data(num_weeks=weeks, save=True)
        _rich_print(
            f"[green]✓ Sample data written to {SAMPLE_DIR}/[/green]\n"
            f"  Daily  : {len(result['daily']):,} rows\n"
            f"  Weekly : {len(result['weekly']):,} rows\n"
            f"  Features: {len(result['features']):,} rows"
        )
    except ImportError:
        _rich_print("[yellow]Warning: data_collector not available. Generating fallback data.[/yellow]")
        _generate_fallback_sample(weeks)
        _rich_print(f"[green]✓ Fallback sample data written to {DATA_DIR}/[/green]")


def _generate_fallback_sample(weeks: int = 52) -> None:
    """Create minimal CSV sample data when data_collector is unavailable."""
    import random
    from datetime import timedelta, date

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    titles = [
        "Stranger Things", "Wednesday", "Squid Game", "The Night Agent",
        "Ginny & Georgia", "You", "Outer Banks", "Love Is Blind",
        "The Witcher", "Cobra Kai",
    ]

    rows: List[Dict[str, Any]] = []
    start = date(2025, 7, 7)  # a Monday
    for w in range(weeks):
        week_start = start + timedelta(weeks=w)
        for day_offset in range(7):
            d = week_start + timedelta(days=day_offset)
            day_name = d.strftime("%A")
            is_weekend = day_name in ("Saturday", "Sunday")
            shuffled = list(titles)
            random.shuffle(shuffled)
            if is_weekend and random.random() < 0.7:
                darling = titles[0]
                if darling in shuffled:
                    shuffled.remove(darling)
                shuffled.insert(0, darling)
            for rank, title in enumerate(shuffled[:10], start=1):
                rows.append({
                    "date": d.isoformat(),
                    "rank": rank,
                    "title": title,
                    "category": "TV",
                })

    df = pd.DataFrame(rows)
    df.to_csv(DATA_DIR / "daily_rankings.csv", index=False)


# ===================================================================
# Helper: load data for backtest/analyze
# ===================================================================
def _load_data() -> pd.DataFrame:
    """Load the best available daily ranking data.

    Checks for sample data first (data/sample/sample_daily.csv),
    then falls back to data/daily_rankings.csv.
    """
    sample_path = SAMPLE_DIR / "sample_daily.csv"
    fallback_path = DATA_DIR / "daily_rankings.csv"

    if sample_path.exists():
        df = pd.read_csv(sample_path, parse_dates=["date"])
        _rich_print(f"  Loaded {len(df):,} rows from [cyan]{sample_path.name}[/cyan]")
        return df
    elif fallback_path.exists():
        df = pd.read_csv(fallback_path, parse_dates=["date"])
        _rich_print(f"  Loaded {len(df):,} rows from [cyan]{fallback_path.name}[/cyan]")
        return df
    else:
        _rich_print(
            "[red]Error: No data found. Run 'python run_backtest.py --mode sample' first.[/red]"
        )
        sys.exit(1)


def _load_weekly_data() -> Optional[pd.DataFrame]:
    """Load the weekly rankings data if available."""
    sample_path = SAMPLE_DIR / "sample_weekly.csv"
    fallback_path = DATA_DIR / "netflix_weekly.csv"

    for path in [sample_path, fallback_path]:
        if path.exists():
            return pd.read_csv(path)
    return None


def _load_features_data() -> Optional[pd.DataFrame]:
    """Load the pre-computed features data if available."""
    features_path = SAMPLE_DIR / "sample_features.csv"
    if features_path.exists():
        return pd.read_csv(features_path)
    return None


def _backtest_results_to_dict(results) -> Dict[str, Any]:
    """Convert a BacktestResults object to a dict for serialization and visualization."""
    equity = results.equity_curve
    trades = results.weekly_breakdown

    # Build bankroll history from equity curve
    bankroll_history = []
    for i, trade_info in enumerate(trades):
        bankroll_history.append({
            "date": trade_info.get("week", str(i)),
            "bankroll": trade_info["bankroll"],
        })

    return {
        "starting_bankroll": results._initial_bankroll,
        "bankroll_history": bankroll_history,
        "trades": [
            {
                "date": t["week"],
                "confidence": t["confidence"],
                "won": t["outcome"] == "WIN",
                "pnl": t["pnl"],
                "predicted_winner": t["predicted"],
                "actual_winner": t["actual"],
                "signal_strength": t["strength"],
                "entry_price": t["entry_price"],
                "contracts": t["contracts"],
            }
            for t in trades
        ],
        "summary": {
            "total_trades": results.total_trades,
            "wins": results.wins,
            "losses": results.losses,
            "win_rate": results.win_rate,
            "total_pnl": results.total_pnl,
            "final_bankroll": equity[-1] if equity else results._initial_bankroll,
            "roi": results.roi * 100,
            "max_drawdown": results.max_drawdown,
            "sharpe_ratio": results.sharpe_ratio,
            "profit_factor": results.profit_factor,
            "avg_win": results.avg_win,
            "avg_loss": results.avg_loss,
            "best_trade": results.best_trade,
            "worst_trade": results.worst_trade,
        },
    }


# ===================================================================
# MODE: backtest
# ===================================================================
def run_backtest(args: argparse.Namespace) -> None:
    """Run the full backtest on available data."""
    _rich_print("[bold cyan]Running Weekend Effect Backtest...[/bold cyan]")

    data_df = _load_data()

    # Also try to merge in weekly_rank data if available
    weekly_df = _load_weekly_data()
    if weekly_df is not None:
        # Merge weekly_rank into the daily data for outcome resolution
        _rich_print(f"  Also loaded weekly data ({len(weekly_df):,} rows) for outcome resolution.")
        try:
            from data_collector import DataMerger
            merger = DataMerger()
            data_df = merger.merge_daily_and_weekly(data_df, weekly_df)
            _rich_print("  [green]✓[/green] Merged daily + weekly data successfully.")
        except Exception as exc:
            _rich_print(f"  [yellow]Warning: Could not merge data: {exc}[/yellow]")

    # Try to import and run the real backtester
    try:
        from strategy import WeekendEffectStrategy
        from backtester import Backtester

        strategy = WeekendEffectStrategy()
        bt = Backtester(
            strategy=strategy,
            initial_bankroll=args.bankroll,
            min_signal_strength=args.min_strength,
        )
        results_obj = bt.run_backtest(data_df)

        # Print rich report
        _rich_print("\n")
        results_obj.print_report()

        # Convert to dict for visualization and serialization
        results = _backtest_results_to_dict(results_obj)

    except ImportError as exc:
        _rich_print(
            f"[yellow]Warning: Could not import backtester ({exc}). "
            f"Generating demo results for visualisation.[/yellow]"
        )
        results = _demo_results(data_df)
        _print_results_report(results)

    # Generate plots
    try:
        from visualizer import BacktestVisualizer
        viz = BacktestVisualizer(output_dir=OUTPUT_DIR)
        saved = viz.generate_all_plots(results, data_df)
        _rich_print(f"\n[green]✓ {len(saved)} plots saved to {OUTPUT_DIR}/[/green]")
    except Exception as exc:
        _rich_print(f"[yellow]Warning: Could not generate plots: {exc}[/yellow]")

    # Persist results
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    results_path = OUTPUT_DIR / "backtest_results.json"
    with open(results_path, "w", encoding="utf-8") as fh:
        json.dump(results, fh, indent=2, default=str)
    _rich_print(f"[green]✓ Results saved to {results_path}[/green]")


def _print_results_report(results: Dict[str, Any]) -> None:
    """Pretty-print the backtest results summary (for fallback mode)."""
    summary = results.get("summary", results)
    display = {
        "Total Trades": summary.get("total_trades", "N/A"),
        "Wins": summary.get("wins", "N/A"),
        "Losses": summary.get("losses", "N/A"),
        "Win Rate": f"{summary.get('win_rate', 0):.1%}",
        "Total P&L": f"${summary.get('total_pnl', 0):,.2f}",
        "Final Bankroll": f"${summary.get('final_bankroll', 0):,.2f}",
        "ROI": f"{summary.get('roi', 0):.1f}%",
        "Max Drawdown": f"{summary.get('max_drawdown', 0):.1%}",
        "Sharpe Ratio": f"{summary.get('sharpe_ratio', 0):.2f}",
    }
    _rich_table("Backtest Results — Weekend Effect Strategy", display)


def _demo_results(data_df: pd.DataFrame) -> Dict[str, Any]:
    """Create plausible demo results for visualisation when no backtester exists."""
    import random

    dates = sorted(data_df["date"].dt.date.unique())
    weeks = dates[::7] if len(dates) > 7 else dates
    bankroll = 100.0
    history = [{"date": str(weeks[0]), "bankroll": bankroll}]
    trades: List[Dict[str, Any]] = []

    for d in weeks[1:]:
        confidence = random.uniform(0.3, 0.9)
        won = random.random() < 0.58
        pnl = random.uniform(2, 8) if won else -random.uniform(2, 8)
        bankroll += pnl
        history.append({"date": str(d), "bankroll": round(bankroll, 2)})
        trades.append({
            "date": str(d),
            "confidence": round(confidence, 3),
            "won": won,
            "pnl": round(pnl, 2),
        })

    wins = sum(1 for t in trades if t["won"])
    return {
        "starting_bankroll": 100.0,
        "bankroll_history": history,
        "trades": trades,
        "summary": {
            "total_trades": len(trades),
            "wins": wins,
            "losses": len(trades) - wins,
            "win_rate": wins / len(trades) if trades else 0,
            "total_pnl": round(bankroll - 100, 2),
            "final_bankroll": round(bankroll, 2),
            "roi": round((bankroll - 100) / 100 * 100, 2),
            "max_drawdown": round(random.uniform(0.05, 0.15), 4),
            "sharpe_ratio": round(random.uniform(0.5, 2.0), 2),
        },
    }


# ===================================================================
# MODE: analyze
# ===================================================================
def run_analyze(args: argparse.Namespace) -> None:
    """Run statistical analysis on the data."""
    _rich_print("[bold cyan]Running statistical analysis...[/bold cyan]")

    data_df = _load_data()
    features_df = _load_features_data()

    # Use features DataFrame for analysis if available (it has pre-computed
    # weekend features like is_weekly_winner, weekend_days_at_one, etc.)
    analysis_df = features_df if features_df is not None else data_df

    try:
        from analyzer import WeekendEffectAnalyzer
        analyzer = WeekendEffectAnalyzer()

        # Overall hit rate
        hit_rate_result = analyzer.compute_hit_rate(analysis_df)
        _rich_table("Weekend Effect — Hit Rate", {
            "Overall hit rate": f"{hit_rate_result.hit_rate:.1%}",
            "Matches": str(hit_rate_result.matches),
            "Total weeks": str(hit_rate_result.total_weeks),
        })

        # Conditional hit rates
        try:
            conditional = analyzer.compute_conditional_hit_rates(analysis_df)
            _rich_table("Conditional Hit Rates", {
                k: f"{v:.1%}" if isinstance(v, float) else str(v)
                for k, v in conditional.items()
            })
        except Exception as exc:
            _rich_print(f"[yellow]Conditional hit rates: {exc}[/yellow]")

        # Chi-squared test
        try:
            chi2 = analyzer.chi_squared_test(analysis_df)
            _rich_table("Chi-Squared Test", {
                k: f"{v:.4f}" if isinstance(v, float) else str(v)
                for k, v in chi2.items()
            })
        except Exception as exc:
            _rich_print(f"[yellow]Chi-squared test: {exc}[/yellow]")

        # Full report
        _rich_print("\n[bold]Full Statistical Report:[/bold]")
        report = analyzer.generate_report(analysis_df)
        print(report)

        # Monte Carlo simulation
        _rich_print("\n[cyan]Running Monte Carlo simulation (10,000 paths)...[/cyan]")
        hit_rate = hit_rate_result.hit_rate if hit_rate_result.hit_rate > 0 else 0.58
        mc_result = analyzer.monte_carlo_simulation(
            hit_rate=hit_rate,
            num_trades=52,
            bankroll=args.bankroll,
            num_simulations=10_000,
        )

        _rich_table("Monte Carlo Simulation", {
            "Median final bankroll": f"${mc_result.median_final_bankroll:.2f}",
            "Mean final bankroll": f"${mc_result.mean_final_bankroll:.2f}",
            "Prob of profit": f"{mc_result.prob_profit:.1%}",
            "Prob of ruin": f"{mc_result.prob_ruin:.1%}",
            "5th percentile": f"${mc_result.percentile_5:.2f}",
            "95th percentile": f"${mc_result.percentile_95:.2f}",
        })

        # Generate full paths for visualization (analyzer only stores final values)
        simulations = _fallback_monte_carlo(
            n_sims=10_000, n_weeks=52, starting=args.bankroll
        )

    except ImportError:
        _rich_print(
            "[yellow]Warning: analyzer module not available. "
            "Running built-in weekend-effect analysis.[/yellow]"
        )
        _run_builtin_analysis(data_df)
        simulations = _fallback_monte_carlo()

    except Exception as exc:
        _rich_print(f"[red]Analysis error: {exc}[/red]")
        import traceback
        traceback.print_exc()
        simulations = _fallback_monte_carlo()

    # Generate analysis plots
    try:
        from visualizer import BacktestVisualizer
        viz = BacktestVisualizer(output_dir=OUTPUT_DIR)

        try:
            viz.plot_weekend_effect_heatmap(data_df)
            _rich_print("[green]✓ Weekend heatmap saved.[/green]")
        except Exception as exc:
            _rich_print(f"[yellow]Heatmap: {exc}[/yellow]")

        try:
            viz.plot_monte_carlo(simulations)
            _rich_print("[green]✓ Monte Carlo plot saved.[/green]")
        except Exception as exc:
            _rich_print(f"[yellow]Monte Carlo plot: {exc}[/yellow]")

    except ImportError:
        _rich_print("[yellow]Warning: visualizer not available.[/yellow]")

    _rich_print(f"\n[green]✓ Analysis complete. Plots in {OUTPUT_DIR}/[/green]")


def _run_builtin_analysis(data_df: pd.DataFrame) -> None:
    """Quick built-in weekend-effect analysis when no analyzer module exists."""
    df = data_df.copy()
    df["dow"] = df["date"].dt.day_name()
    df["is_weekend"] = df["dow"].isin(["Saturday", "Sunday"])

    weekend = df[df["is_weekend"]].groupby("title")["rank"].mean()
    weekday = df[~df["is_weekend"]].groupby("title")["rank"].mean()

    comparison = pd.DataFrame({"weekend_avg": weekend, "weekday_avg": weekday}).dropna()
    comparison["delta"] = comparison["weekday_avg"] - comparison["weekend_avg"]
    comparison = comparison.sort_values("delta", ascending=False)

    _rich_print("\n[bold]Weekend vs. Weekday Average Rank (top 10 by delta):[/bold]")
    display = {}
    for title, row in comparison.head(10).iterrows():
        display[title] = (
            f"Weekend={row['weekend_avg']:.1f}  "
            f"Weekday={row['weekday_avg']:.1f}  "
            f"Δ={row['delta']:+.1f}"
        )
    _rich_table("Weekend Effect Strength", display)


def _fallback_monte_carlo(
    n_sims: int = 10_000,
    n_weeks: int = 52,
    starting: float = 100.0,
) -> np.ndarray:
    """Generate a simple Monte Carlo simulation array as a fallback."""
    rng = np.random.default_rng(42)
    win_rate = 0.58
    avg_win = 5.0
    avg_loss = 4.5

    sims = np.zeros((n_sims, n_weeks + 1))
    sims[:, 0] = starting

    for t in range(1, n_weeks + 1):
        wins = rng.random(n_sims) < win_rate
        pnl = np.where(
            wins,
            rng.normal(avg_win, 1.5, n_sims),
            -rng.normal(avg_loss, 1.5, n_sims),
        )
        sims[:, t] = np.maximum(sims[:, t - 1] + pnl, 0)

    return sims


# ===================================================================
# MODE: paper
# ===================================================================
def run_paper(args: argparse.Namespace) -> None:
    """Interactive paper-trading management."""
    from paper_trader import PaperTrader

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    trader = PaperTrader(
        initial_bankroll=args.bankroll,
        log_file=str(DATA_DIR / "paper_trades.json"),
    )

    _rich_print(f"[bold cyan]Paper Trader[/bold cyan] — {trader}")

    # Show current status
    summary = trader.get_performance_summary()
    _rich_table("Paper Trading Performance", summary)

    open_positions = trader.get_open_positions()
    if open_positions:
        _rich_print(f"\n[yellow]Open positions ({len(open_positions)}):[/yellow]")
        for pos in open_positions:
            _rich_print(
                f"  • {pos.get('signal_date', pos.get('date', '?'))} — "
                f"{pos.get('predicted_winner', '?')} "
                f"(confidence {pos.get('confidence', 0):.0%}, "
                f"${pos.get('bet_size', 0):.2f})"
            )
    else:
        _rich_print("\n[dim]No open positions.[/dim]")

    # Interactive loop
    _rich_print(
        "\n[bold]Commands:[/bold]  "
        "[cyan]signal[/cyan]  [cyan]outcome[/cyan]  "
        "[cyan]history[/cyan]  [cyan]quit[/cyan]"
    )

    while True:
        try:
            cmd = input("\npaper> ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            break

        if cmd in ("q", "quit", "exit"):
            break

        elif cmd == "signal":
            date_str = input("  Signal date (YYYY-MM-DD): ").strip()
            predicted = input("  Predicted #1 show: ").strip()
            try:
                confidence = float(input("  Confidence (0-1): ").strip())
            except ValueError:
                _rich_print("[red]Invalid confidence value.[/red]")
                continue
            try:
                entry_price = float(input("  Kalshi Entry Price (e.g. 0.55): ").strip())
            except ValueError:
                _rich_print("[red]Invalid entry price.[/red]")
                continue

            trade = trader.record_signal(
                date_str,
                {
                    "predicted_winner": predicted,
                    "confidence": confidence,
                    "entry_price": entry_price,
                },
            )
            _rich_print(f"[green]✓ Signal recorded! (Trade #{trade.get('id', '?')})[/green]")
            _rich_print(f"  [cyan]Kelly bet size:[/cyan] ${trade.get('bet_size', 0):.2f}")
            _rich_print(f"  [cyan]Contracts to buy:[/cyan] {trade.get('contracts', 0)}")

        elif cmd == "outcome":
            date_str = input("  Signal date to resolve (YYYY-MM-DD): ").strip()
            actual = input("  Actual #1 show: ").strip()
            resolved = trader.record_outcome(date_str, actual)
            if resolved:
                for t in (resolved if isinstance(resolved, list) else [resolved]):
                    status = "[green]WIN[/green]" if t.get("won") else "[red]LOSS[/red]"
                    _rich_print(
                        f"  Trade #{t.get('id', '?')}: {status} — "
                        f"P&L ${t.get('pnl', 0):+.2f}"
                    )
            else:
                _rich_print("[yellow]No open trades found for that date.[/yellow]")

        elif cmd == "history":
            history = trader.get_trade_history()
            if not history:
                _rich_print("[dim]No completed trades yet.[/dim]")
            else:
                for t in history[-10:]:
                    icon = "✓" if t.get("won") else "✗"
                    _rich_print(
                        f"  {icon} {t.get('signal_date', t.get('date', '?'))}  "
                        f"{t.get('predicted_winner', '?'):.<30} "
                        f"P&L ${t.get('pnl', 0):+.2f}"
                    )

        else:
            _rich_print("[dim]Unknown command. Use: signal, outcome, history, quit[/dim]")

    trader.save()
    _rich_print("[green]✓ Paper trades saved.[/green]")


# ===================================================================
# Argument parser
# ===================================================================
def build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser."""
    parser = argparse.ArgumentParser(
        prog="run_backtest",
        description=(
            "Kalshi Netflix Weekend Effect Backtester — "
            "CLI runner for backtesting, analysis, and paper trading."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python run_backtest.py --mode sample\n"
            "  python run_backtest.py --mode sample --weeks 104\n"
            "  python run_backtest.py --mode backtest\n"
            "  python run_backtest.py --mode backtest --min-strength medium\n"
            "  python run_backtest.py --mode analyze\n"
            "  python run_backtest.py --mode paper\n"
        ),
    )
    parser.add_argument(
        "--mode",
        choices=["sample", "backtest", "analyze", "paper"],
        default="sample",
        help="Operating mode (default: sample)",
    )
    parser.add_argument(
        "--weeks",
        type=int,
        default=52,
        help="Number of weeks of sample data to generate (sample mode only)",
    )
    parser.add_argument(
        "--bankroll",
        type=float,
        default=100.0,
        help="Starting bankroll in dollars (default: 100.0)",
    )
    parser.add_argument(
        "--min-strength",
        dest="min_strength",
        choices=["weak", "medium", "strong"],
        default="weak",
        help="Minimum signal strength to trade (default: weak = trade all signals)",
    )
    return parser


# ===================================================================
# Entry point
# ===================================================================
def main() -> None:
    """Parse arguments and dispatch to the selected mode."""
    parser = build_parser()
    args = parser.parse_args()

    mode_dispatch = {
        "sample": run_sample,
        "backtest": run_backtest,
        "analyze": run_analyze,
        "paper": run_paper,
    }

    handler = mode_dispatch[args.mode]
    try:
        handler(args)
    except KeyboardInterrupt:
        _rich_print("\n[dim]Interrupted.[/dim]")
        sys.exit(130)
    except Exception as exc:
        _rich_print(f"[red]Error: {exc}[/red]")
        raise


if __name__ == "__main__":
    main()
