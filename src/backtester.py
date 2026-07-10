"""
Backtesting engine for the Kalshi Netflix Weekend Effect strategy.

Iterates over historical daily-ranking data week by week, generates trading
signals via :class:`~strategy.WeekendEffectStrategy`, simulates market
entry / exit through :class:`~strategy.KalshiSimulator`, and produces a
comprehensive :class:`BacktestResults` report.

Classes:
    Trade: Record of a single simulated trade.
    BacktestResults: Aggregated performance metrics, equity curve, and trade log.
    Backtester: Orchestrates the week-by-week back-test loop.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd

from strategy import KalshiSimulator, Signal, WeekendEffectStrategy


# ---------------------------------------------------------------------------
# Data containers
# ---------------------------------------------------------------------------

@dataclass
class Trade:
    """Record of a single simulated trade.

    Attributes:
        week: Label for the trading week (e.g. '2025-W03').
        predicted_winner: Title the strategy predicted.
        actual_winner: Title that actually topped the weekly chart.
        signal_strength: 'strong', 'medium', or 'weak'.
        confidence: Strategy confidence at signal time.
        entry_price: Simulated market price paid per contract.
        contracts: Number of contracts purchased.
        outcome: Whether the prediction was correct.
        pnl: Dollar profit or loss.
        bankroll_after: Bankroll balance after this trade settles.
        reasoning: Strategy reasoning string.
    """

    week: str
    predicted_winner: str
    actual_winner: str
    signal_strength: str
    confidence: float
    entry_price: float
    contracts: int
    outcome: bool
    pnl: float
    bankroll_after: float
    reasoning: str = ""


# ---------------------------------------------------------------------------
# Backtest results
# ---------------------------------------------------------------------------

class BacktestResults:
    """Aggregated performance metrics for a completed back-test.

    Provides summary statistics, an equity curve, a full trade log, and
    formatted console reporting via the *rich* library.
    """

    def __init__(self, trades: list[Trade], initial_bankroll: float) -> None:
        self._trades = trades
        self._initial_bankroll = initial_bankroll

    # -- Core metrics -------------------------------------------------------

    @property
    def total_trades(self) -> int:
        return len(self._trades)

    @property
    def wins(self) -> int:
        return sum(1 for t in self._trades if t.outcome)

    @property
    def losses(self) -> int:
        return self.total_trades - self.wins

    @property
    def win_rate(self) -> float:
        return self.wins / self.total_trades if self.total_trades else 0.0

    @property
    def total_pnl(self) -> float:
        return round(sum(t.pnl for t in self._trades), 4)

    @property
    def roi(self) -> float:
        """Return on investment as a fraction of initial bankroll."""
        return self.total_pnl / self._initial_bankroll if self._initial_bankroll else 0.0

    @property
    def equity_curve(self) -> list[float]:
        """Bankroll value after each trade."""
        return [t.bankroll_after for t in self._trades]

    @property
    def trade_log(self) -> list[Trade]:
        return list(self._trades)

    @property
    def weekly_breakdown(self) -> list[dict]:
        """List of per-trade dictionaries suitable for tabular display."""
        return [
            {
                "week": t.week,
                "predicted": t.predicted_winner,
                "actual": t.actual_winner,
                "strength": t.signal_strength,
                "confidence": t.confidence,
                "entry_price": t.entry_price,
                "contracts": t.contracts,
                "outcome": "WIN" if t.outcome else "LOSS",
                "pnl": t.pnl,
                "bankroll": t.bankroll_after,
            }
            for t in self._trades
        ]

    # -- Derived metrics ----------------------------------------------------

    @property
    def max_drawdown(self) -> float:
        """Maximum peak-to-trough decline in the equity curve."""
        if not self._trades:
            return 0.0
        peak = self._initial_bankroll
        max_dd = 0.0
        for bal in self.equity_curve:
            if bal > peak:
                peak = bal
            dd = (peak - bal) / peak if peak > 0 else 0.0
            max_dd = max(max_dd, dd)
        return round(max_dd, 4)

    @property
    def sharpe_ratio(self) -> float:
        """Annualised Sharpe ratio (assuming weekly trades, 52 per year)."""
        if len(self._trades) < 2:
            return 0.0
        returns = [t.pnl for t in self._trades]
        mean_r = np.mean(returns)
        std_r = np.std(returns, ddof=1)
        if std_r == 0:
            return 0.0
        weekly_sharpe = mean_r / std_r
        return round(float(weekly_sharpe * math.sqrt(52)), 4)

    @property
    def profit_factor(self) -> float:
        """Gross profits divided by gross losses (absolute value)."""
        gross_profit = sum(t.pnl for t in self._trades if t.pnl > 0)
        gross_loss = abs(sum(t.pnl for t in self._trades if t.pnl < 0))
        if gross_loss == 0:
            return float("inf") if gross_profit > 0 else 0.0
        return round(gross_profit / gross_loss, 4)

    @property
    def avg_win(self) -> float:
        wins = [t.pnl for t in self._trades if t.outcome]
        return round(float(np.mean(wins)), 4) if wins else 0.0

    @property
    def avg_loss(self) -> float:
        losses = [t.pnl for t in self._trades if not t.outcome]
        return round(float(np.mean(losses)), 4) if losses else 0.0

    @property
    def best_trade(self) -> float:
        return max((t.pnl for t in self._trades), default=0.0)

    @property
    def worst_trade(self) -> float:
        return min((t.pnl for t in self._trades), default=0.0)

    # -- Reporting ----------------------------------------------------------

    def summary(self) -> str:
        """Return a multi-line formatted string of all key metrics."""
        lines = [
            "═══════════════════════════════════════════════",
            "       BACKTEST RESULTS — WEEKEND EFFECT       ",
            "═══════════════════════════════════════════════",
            f"  Total trades      : {self.total_trades}",
            f"  Wins / Losses     : {self.wins} / {self.losses}",
            f"  Win rate          : {self.win_rate:.1%}",
            f"  Total P&L         : ${self.total_pnl:+.2f}",
            f"  ROI               : {self.roi:+.1%}",
            f"  Max drawdown      : {self.max_drawdown:.1%}",
            f"  Sharpe ratio      : {self.sharpe_ratio:.2f}",
            f"  Profit factor     : {self.profit_factor:.2f}",
            f"  Avg win           : ${self.avg_win:+.2f}",
            f"  Avg loss          : ${self.avg_loss:+.2f}",
            f"  Best trade        : ${self.best_trade:+.2f}",
            f"  Worst trade       : ${self.worst_trade:+.2f}",
            "═══════════════════════════════════════════════",
        ]
        return "\n".join(lines)

    def to_dataframe(self) -> pd.DataFrame:
        """Return the trade log as a :class:`pandas.DataFrame`."""
        return pd.DataFrame(self.weekly_breakdown)

    def print_report(self) -> None:
        """Print a richly-formatted report to the console.

        Uses the *rich* library when available; falls back to plain text.
        """
        try:
            from rich.console import Console
            from rich.table import Table
            from rich.panel import Panel
            from rich.text import Text
        except ImportError:
            print(self.summary())
            return

        console = Console()

        # -- Summary panel --
        console.print(Panel(self.summary(), title="Backtest Summary", expand=False))

        # -- Trade log table --
        table = Table(title="Trade Log", show_lines=True)
        table.add_column("Week", style="cyan", no_wrap=True)
        table.add_column("Predicted", style="white")
        table.add_column("Actual", style="white")
        table.add_column("Strength", justify="center")
        table.add_column("Conf.", justify="right")
        table.add_column("Entry $", justify="right")
        table.add_column("Contracts", justify="right")
        table.add_column("Outcome", justify="center")
        table.add_column("P&L", justify="right")
        table.add_column("Bankroll", justify="right")

        for row in self.weekly_breakdown:
            outcome_style = "bold green" if row["outcome"] == "WIN" else "bold red"
            pnl_style = "green" if row["pnl"] >= 0 else "red"

            strength_style = {
                "strong": "bold green",
                "medium": "yellow",
                "weak": "dim red",
            }.get(row["strength"], "white")

            table.add_row(
                row["week"],
                row["predicted"][:30],
                row["actual"][:30],
                Text(row["strength"], style=strength_style),
                f"{row['confidence']:.2f}",
                f"${row['entry_price']:.2f}",
                str(row["contracts"]),
                Text(row["outcome"], style=outcome_style),
                Text(f"${row['pnl']:+.2f}", style=pnl_style),
                f"${row['bankroll']:.2f}",
            )

        console.print(table)


# ---------------------------------------------------------------------------
# Backtester
# ---------------------------------------------------------------------------

class Backtester:
    """Week-by-week backtesting engine.

    Parameters
    ----------
    strategy : WeekendEffectStrategy
        Strategy instance used to generate signals.
    initial_bankroll : float
        Starting capital in dollars.
    simulator_seed : int, optional
        Seed for :class:`KalshiSimulator` random price noise.
    min_signal_strength : str
        Minimum signal strength required to enter a trade.
        One of ``'strong'``, ``'medium'``, ``'weak'`` (default ``'weak'``
        means all signals are traded).
    """

    _STRENGTH_ORDER = {"strong": 3, "medium": 2, "weak": 1}

    def __init__(
        self,
        strategy: WeekendEffectStrategy,
        initial_bankroll: float = 100.0,
        simulator_seed: Optional[int] = None,
        min_signal_strength: str = "weak",
    ) -> None:
        self._strategy = strategy
        self._initial_bankroll = initial_bankroll
        self._simulator = KalshiSimulator(seed=simulator_seed)
        self._min_strength = min_signal_strength

    def run_backtest(self, data_df: pd.DataFrame) -> BacktestResults:
        """Execute the back-test over *data_df*.

        Parameters
        ----------
        data_df : pd.DataFrame
            Daily ranking data with **at least** the columns:
                * ``date`` – daily date (parseable).
                * ``rank`` – integer daily rank.
                * ``title`` – show / film title.
                * ``weekly_rank`` – the *actual* weekly rank used for
                  resolution (rank 1 = winner).  This column is used
                  **only** for outcome evaluation, never for signal
                  generation.

            Optional columns that improve signal quality:
                * ``cumulative_weeks_in_top_10``
                * ``category``

        Returns
        -------
        BacktestResults
            Complete results object with metrics, equity curve, and trade log.
        """
        df = data_df.copy()
        df["date"] = pd.to_datetime(df["date"])

        # Assign each row to an ISO week key (year, week)
        df["_iso_year"] = df["date"].dt.isocalendar().year.astype(int)
        df["_iso_week"] = df["date"].dt.isocalendar().week.astype(int)
        df["_week_key"] = list(zip(df["_iso_year"], df["_iso_week"]))

        trades: list[Trade] = []
        bankroll = self._initial_bankroll

        for week_key in sorted(df["_week_key"].unique()):
            week_df = df[df["_week_key"] == week_key]

            # --- 1. Generate signal (using only pre-Monday-close data) ---
            # In a real scenario we would only use Mon–Sun data available
            # before 11:59 PM ET Monday.  In this simulation we use the
            # full week's daily data as our input (Sat/Sun are the key).
            signal = self._strategy.generate_signal(week_df)

            if signal is None:
                continue

            # Skip if signal strength is below the minimum threshold
            if self._STRENGTH_ORDER.get(signal.signal_strength, 0) < self._STRENGTH_ORDER.get(
                self._min_strength, 1
            ):
                continue

            # --- 2. Determine actual weekly winner ---
            actual_winner = self._resolve_actual_winner(week_df)
            if actual_winner is None:
                continue

            # --- 3. Simulate market entry ---
            entry_price = self._simulator.simulate_market_price(signal.confidence)
            contracts = self._simulator.calculate_position_size(
                confidence=signal.confidence,
                bankroll=bankroll,
                market_price=entry_price,
            )
            if contracts <= 0:
                continue

            # --- 4. Evaluate outcome ---
            outcome = signal.predicted_winner == actual_winner
            pnl = self._simulator.calculate_pnl(entry_price, outcome, contracts)
            bankroll = round(bankroll + pnl, 4)

            week_label = f"{week_key[0]}-W{week_key[1]:02d}"

            trades.append(
                Trade(
                    week=week_label,
                    predicted_winner=signal.predicted_winner,
                    actual_winner=actual_winner,
                    signal_strength=signal.signal_strength,
                    confidence=signal.confidence,
                    entry_price=entry_price,
                    contracts=contracts,
                    outcome=outcome,
                    pnl=pnl,
                    bankroll_after=bankroll,
                    reasoning=signal.reasoning,
                )
            )

            # Stop if bankroll is depleted
            if bankroll <= 0:
                break

        return BacktestResults(trades, self._initial_bankroll)

    def get_results(self, data_df: pd.DataFrame) -> BacktestResults:
        """Convenience alias for :meth:`run_backtest`."""
        return self.run_backtest(data_df)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve_actual_winner(week_df: pd.DataFrame) -> Optional[str]:
        """Determine the actual weekly #1 title from *week_df*.

        Looks for a ``weekly_rank`` column first; otherwise falls back to
        the title that held the best (lowest) average daily rank.
        """
        if "weekly_rank" in week_df.columns:
            top = week_df.loc[week_df["weekly_rank"] == 1]
            if not top.empty:
                return str(top.iloc[0]["title"])

        # Fallback: title with the lowest average daily rank
        avg_ranks = week_df.groupby("title")["rank"].mean()
        if avg_ranks.empty:
            return None
        return str(avg_ranks.idxmin())
