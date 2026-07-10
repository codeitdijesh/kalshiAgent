"""
Visualization module for the Kalshi Netflix Weekend Effect Backtester.

Generates publication-quality charts using matplotlib and seaborn with a
consistent dark theme. All plots are saved to the output/ directory.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional

import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import matplotlib.ticker as mticker
import numpy as np
import pandas as pd
import seaborn as sns

# ---------------------------------------------------------------------------
# Theme / style constants
# ---------------------------------------------------------------------------
_PALETTE = {
    "green": "#00e676",
    "red": "#ff1744",
    "blue": "#2979ff",
    "orange": "#ff9100",
    "purple": "#d500f9",
    "cyan": "#00e5ff",
    "white": "#e0e0e0",
    "grey": "#616161",
    "bg": "#121212",
    "card": "#1e1e1e",
}

OUTPUT_DIR = Path("output")


def _ensure_output_dir() -> None:
    """Create the output directory if it doesn't already exist."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def _apply_theme() -> None:
    """Apply a consistent dark theme to all plots."""
    plt.style.use("dark_background")
    plt.rcParams.update(
        {
            "figure.facecolor": _PALETTE["bg"],
            "axes.facecolor": _PALETTE["card"],
            "axes.edgecolor": _PALETTE["grey"],
            "axes.labelcolor": _PALETTE["white"],
            "axes.titlesize": 14,
            "axes.labelsize": 11,
            "xtick.color": _PALETTE["white"],
            "ytick.color": _PALETTE["white"],
            "text.color": _PALETTE["white"],
            "legend.facecolor": _PALETTE["card"],
            "legend.edgecolor": _PALETTE["grey"],
            "grid.color": _PALETTE["grey"],
            "grid.alpha": 0.3,
            "font.family": "sans-serif",
            "savefig.dpi": 200,
            "savefig.bbox": "tight",
            "savefig.facecolor": _PALETTE["bg"],
        }
    )


class BacktestVisualizer:
    """Generate all visualisation artefacts for the backtester.

    Every public method accepts the relevant data structure, renders a
    matplotlib figure, saves it to ``output/``, and returns the
    :class:`~matplotlib.figure.Figure` so callers can display it
    interactively if desired.

    Parameters
    ----------
    output_dir : str | Path, optional
        Directory to save plots into.  Defaults to ``output/``.
    """

    def __init__(self, output_dir: str | Path = OUTPUT_DIR) -> None:
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        _apply_theme()

    # ------------------------------------------------------------------
    # 1. Equity Curve
    # ------------------------------------------------------------------
    def plot_equity_curve(self, results: Dict[str, Any]) -> plt.Figure:
        """Plot bankroll over time with drawdown shading.

        Parameters
        ----------
        results : dict
            Must contain keys:
            - ``bankroll_history`` : list[dict] with ``date`` and ``bankroll``
            - ``starting_bankroll`` : float

        Returns
        -------
        matplotlib.figure.Figure
        """
        history = pd.DataFrame(results["bankroll_history"])
        history["date"] = pd.to_datetime(history["date"])
        history = history.sort_values("date").reset_index(drop=True)

        starting = results.get("starting_bankroll", history["bankroll"].iloc[0])

        fig, ax = plt.subplots(figsize=(14, 6))

        # --- win / loss shading -------------------------------------------
        for i in range(1, len(history)):
            colour = (
                _PALETTE["green"]
                if history["bankroll"].iloc[i] >= history["bankroll"].iloc[i - 1]
                else _PALETTE["red"]
            )
            ax.fill_between(
                history["date"].iloc[i - 1 : i + 1],
                starting,
                history["bankroll"].iloc[i - 1 : i + 1],
                color=colour,
                alpha=0.12,
            )

        # --- equity line ---------------------------------------------------
        ax.plot(
            history["date"],
            history["bankroll"],
            color=_PALETTE["cyan"],
            linewidth=2,
            label="Bankroll",
        )

        # --- starting bankroll reference -----------------------------------
        ax.axhline(
            starting,
            color=_PALETTE["grey"],
            linewidth=1,
            linestyle="--",
            label=f"Starting (${starting:,.2f})",
        )

        # --- max drawdown highlight ----------------------------------------
        running_max = history["bankroll"].cummax()
        drawdown = history["bankroll"] - running_max
        max_dd_idx = drawdown.idxmin()
        peak_idx = history["bankroll"].iloc[: max_dd_idx + 1].idxmax()

        ax.fill_between(
            history["date"].iloc[peak_idx : max_dd_idx + 1],
            history["bankroll"].iloc[peak_idx : max_dd_idx + 1],
            running_max.iloc[peak_idx : max_dd_idx + 1],
            color=_PALETTE["red"],
            alpha=0.30,
            label=f"Max Drawdown (${abs(drawdown.iloc[max_dd_idx]):,.2f})",
        )

        ax.set_title("Equity Curve — Weekend Effect Strategy", fontsize=16)
        ax.set_xlabel("Date")
        ax.set_ylabel("Bankroll ($)")
        ax.legend(loc="upper left")
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %Y"))
        ax.xaxis.set_major_locator(mdates.MonthLocator(interval=2))
        fig.autofmt_xdate()
        ax.grid(True)

        path = self.output_dir / "equity_curve.png"
        fig.savefig(path)
        plt.close(fig)
        return fig

    # ------------------------------------------------------------------
    # 2. Win Rate by Confidence Bucket
    # ------------------------------------------------------------------
    def plot_win_rate_by_confidence(self, results: Dict[str, Any]) -> plt.Figure:
        """Bar chart of win rate grouped by confidence bucket.

        Parameters
        ----------
        results : dict
            Must contain ``trades`` — a list of dicts, each with keys
            ``confidence`` (float 0-1) and ``won`` (bool).

        Returns
        -------
        matplotlib.figure.Figure
        """
        trades = pd.DataFrame(results["trades"])
        bins = [0.0, 0.3, 0.5, 0.7, 1.0]
        labels = ["0–0.3", "0.3–0.5", "0.5–0.7", "0.7–1.0"]
        trades["bucket"] = pd.cut(trades["confidence"], bins=bins, labels=labels, include_lowest=True)

        stats = (
            trades.groupby("bucket", observed=False)
            .agg(win_rate=("won", "mean"), count=("won", "size"))
            .reset_index()
        )

        fig, ax = plt.subplots(figsize=(10, 6))
        x = np.arange(len(stats))
        width = 0.38

        # Actual win rate
        ax.bar(
            x - width / 2,
            stats["win_rate"],
            width,
            color=_PALETTE["cyan"],
            label="Actual Win Rate",
            edgecolor="none",
        )

        # Predicted (mid-point of bucket)
        midpoints = [0.15, 0.4, 0.6, 0.85]
        ax.bar(
            x + width / 2,
            midpoints,
            width,
            color=_PALETTE["orange"],
            alpha=0.6,
            label="Predicted Confidence",
            edgecolor="none",
        )

        # Counts as text
        for i, row in stats.iterrows():
            ax.text(
                i - width / 2,
                row["win_rate"] + 0.02,
                f'n={row["count"]:.0f}',
                ha="center",
                va="bottom",
                fontsize=9,
                color=_PALETTE["white"],
            )

        ax.set_xticks(x)
        ax.set_xticklabels(labels)
        ax.set_xlabel("Confidence Bucket")
        ax.set_ylabel("Win Rate")
        ax.set_title("Win Rate vs. Confidence Level", fontsize=16)
        ax.set_ylim(0, 1.1)
        ax.yaxis.set_major_formatter(mticker.PercentFormatter(xmax=1))
        ax.legend()
        ax.grid(axis="y")

        path = self.output_dir / "win_rate_by_confidence.png"
        fig.savefig(path)
        plt.close(fig)
        return fig

    # ------------------------------------------------------------------
    # 3. Weekend Effect Heatmap
    # ------------------------------------------------------------------
    def plot_weekend_effect_heatmap(
        self,
        data_df: pd.DataFrame,
        top_n: int = 15,
    ) -> plt.Figure:
        """Heatmap of daily rankings per title across days of the week.

        Parameters
        ----------
        data_df : pd.DataFrame
            Must contain columns ``title``, ``date``, and ``rank``.
        top_n : int
            Number of top titles to display (by average rank).

        Returns
        -------
        matplotlib.figure.Figure
        """
        df = data_df.copy()
        df["date"] = pd.to_datetime(df["date"])
        df["dow"] = df["date"].dt.day_name()

        day_order = [
            "Monday",
            "Tuesday",
            "Wednesday",
            "Thursday",
            "Friday",
            "Saturday",
            "Sunday",
        ]

        pivot = df.pivot_table(
            index="title",
            columns="dow",
            values="rank",
            aggfunc="mean",
        )
        pivot = pivot.reindex(columns=day_order)

        # Keep only the top_n titles with the best (lowest) average rank
        pivot["avg"] = pivot.mean(axis=1)
        pivot = pivot.nsmallest(top_n, "avg").drop(columns="avg")

        fig, ax = plt.subplots(figsize=(12, max(6, top_n * 0.55)))
        sns.heatmap(
            pivot,
            annot=True,
            fmt=".1f",
            cmap="YlOrRd_r",  # lower rank = darker/better
            linewidths=0.5,
            linecolor=_PALETTE["grey"],
            cbar_kws={"label": "Average Rank (1 = best)"},
            ax=ax,
        )
        ax.set_title("Weekend Effect — Average Daily Rank by Title", fontsize=16)
        ax.set_xlabel("Day of Week")
        ax.set_ylabel("")

        path = self.output_dir / "weekend_heatmap.png"
        fig.savefig(path)
        plt.close(fig)
        return fig

    # ------------------------------------------------------------------
    # 4. Monthly P&L
    # ------------------------------------------------------------------
    def plot_monthly_pnl(self, results: Dict[str, Any]) -> plt.Figure:
        """Bar chart of monthly profit & loss.

        Parameters
        ----------
        results : dict
            Must contain ``trades`` — a list of dicts with ``date`` (str)
            and ``pnl`` (float).

        Returns
        -------
        matplotlib.figure.Figure
        """
        trades = pd.DataFrame(results["trades"])
        trades["date"] = pd.to_datetime(trades["date"])
        trades["month"] = trades["date"].dt.to_period("M")
        monthly = trades.groupby("month")["pnl"].sum()

        fig, ax = plt.subplots(figsize=(14, 6))
        colours = [
            _PALETTE["green"] if v >= 0 else _PALETTE["red"] for v in monthly.values
        ]
        ax.bar(
            monthly.index.astype(str),
            monthly.values,
            color=colours,
            edgecolor="none",
            width=0.7,
        )

        ax.axhline(0, color=_PALETTE["grey"], linewidth=0.8)
        ax.set_title("Monthly P&L", fontsize=16)
        ax.set_xlabel("Month")
        ax.set_ylabel("Profit / Loss ($)")
        fig.autofmt_xdate(rotation=45)
        ax.grid(axis="y")

        path = self.output_dir / "monthly_pnl.png"
        fig.savefig(path)
        plt.close(fig)
        return fig

    # ------------------------------------------------------------------
    # 5. Rolling Hit Rate
    # ------------------------------------------------------------------
    def plot_hit_rate_over_time(
        self,
        results: Dict[str, Any],
        window: int = 10,
    ) -> plt.Figure:
        """Rolling win-rate line chart.

        Parameters
        ----------
        results : dict
            Must contain ``trades`` — list of dicts with ``date`` and ``won``.
        window : int
            Rolling window size in weeks (trades).

        Returns
        -------
        matplotlib.figure.Figure
        """
        trades = pd.DataFrame(results["trades"])
        trades["date"] = pd.to_datetime(trades["date"])
        trades = trades.sort_values("date").reset_index(drop=True)
        trades["rolling_hit"] = (
            trades["won"].astype(float).rolling(window, min_periods=1).mean()
        )

        fig, ax = plt.subplots(figsize=(14, 6))
        ax.plot(
            trades["date"],
            trades["rolling_hit"],
            color=_PALETTE["cyan"],
            linewidth=2,
            label=f"{window}-Week Rolling Hit Rate",
        )
        ax.axhline(
            0.5,
            color=_PALETTE["grey"],
            linestyle="--",
            linewidth=1,
            label="Break-Even (50 %)",
        )

        # Shade above / below 50 %
        ax.fill_between(
            trades["date"],
            trades["rolling_hit"],
            0.5,
            where=trades["rolling_hit"] >= 0.5,
            color=_PALETTE["green"],
            alpha=0.15,
        )
        ax.fill_between(
            trades["date"],
            trades["rolling_hit"],
            0.5,
            where=trades["rolling_hit"] < 0.5,
            color=_PALETTE["red"],
            alpha=0.15,
        )

        ax.set_title("Edge Stability — Rolling Hit Rate", fontsize=16)
        ax.set_xlabel("Date")
        ax.set_ylabel("Win Rate")
        ax.yaxis.set_major_formatter(mticker.PercentFormatter(xmax=1))
        ax.set_ylim(0, 1.05)
        ax.legend()
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %Y"))
        ax.xaxis.set_major_locator(mdates.MonthLocator(interval=2))
        fig.autofmt_xdate()
        ax.grid(True)

        path = self.output_dir / "hit_rate_over_time.png"
        fig.savefig(path)
        plt.close(fig)
        return fig

    # ------------------------------------------------------------------
    # 6. Monte Carlo Fan Chart
    # ------------------------------------------------------------------
    def plot_monte_carlo(
        self,
        simulations: np.ndarray,
        starting_bankroll: float = 100.0,
        ruin_threshold: float = 0.0,
    ) -> plt.Figure:
        """Fan chart of Monte Carlo bankroll simulations.

        Parameters
        ----------
        simulations : np.ndarray
            2-D array of shape ``(n_simulations, n_periods)`` containing
            bankroll paths.
        starting_bankroll : float
            Used only for the reference line.
        ruin_threshold : float
            Bankroll level considered "ruin".

        Returns
        -------
        matplotlib.figure.Figure
        """
        n_sims, n_periods = simulations.shape
        x = np.arange(n_periods)

        median = np.median(simulations, axis=0)
        p5 = np.percentile(simulations, 5, axis=0)
        p25 = np.percentile(simulations, 25, axis=0)
        p75 = np.percentile(simulations, 75, axis=0)
        p95 = np.percentile(simulations, 95, axis=0)

        # Probability of ruin
        ruin_count = np.sum(np.any(simulations <= ruin_threshold, axis=1))
        prob_ruin = ruin_count / n_sims

        fig, ax = plt.subplots(figsize=(14, 7))

        # Fan layers
        ax.fill_between(x, p5, p95, color=_PALETTE["blue"], alpha=0.10, label="5th–95th %ile")
        ax.fill_between(x, p25, p75, color=_PALETTE["blue"], alpha=0.25, label="25th–75th %ile")
        ax.plot(x, median, color=_PALETTE["cyan"], linewidth=2.5, label="Median")

        # Reference
        ax.axhline(
            starting_bankroll,
            color=_PALETTE["grey"],
            linewidth=1,
            linestyle="--",
            label=f"Start (${starting_bankroll:,.0f})",
        )
        ax.axhline(
            ruin_threshold,
            color=_PALETTE["red"],
            linewidth=1,
            linestyle=":",
            label="Ruin Threshold",
        )

        # Probability of ruin label
        ax.text(
            n_periods * 0.72,
            ax.get_ylim()[1] * 0.92,
            f"P(Ruin) = {prob_ruin:.1%}",
            fontsize=14,
            fontweight="bold",
            color=_PALETTE["red"] if prob_ruin > 0.05 else _PALETTE["green"],
            bbox=dict(
                facecolor=_PALETTE["card"],
                edgecolor=_PALETTE["grey"],
                boxstyle="round,pad=0.4",
            ),
        )

        ax.set_title(
            f"Monte Carlo Simulation ({n_sims:,} paths, {n_periods} weeks)",
            fontsize=16,
        )
        ax.set_xlabel("Week")
        ax.set_ylabel("Bankroll ($)")
        ax.legend(loc="upper left")
        ax.grid(True)

        path = self.output_dir / "monte_carlo.png"
        fig.savefig(path)
        plt.close(fig)
        return fig

    # ------------------------------------------------------------------
    # 7. Generate All Plots
    # ------------------------------------------------------------------
    def generate_all_plots(
        self,
        results: Dict[str, Any],
        data_df: pd.DataFrame,
        simulations: Optional[np.ndarray] = None,
    ) -> List[Path]:
        """Generate every available plot and return the saved file paths.

        Parameters
        ----------
        results : dict
            Backtest results dictionary.
        data_df : pd.DataFrame
            Raw daily ranking data.
        simulations : np.ndarray, optional
            Monte Carlo simulation array.  If *None* the Monte Carlo plot
            is skipped.

        Returns
        -------
        list[Path]
            Paths of the saved plot images.
        """
        saved: List[Path] = []

        try:
            self.plot_equity_curve(results)
            saved.append(self.output_dir / "equity_curve.png")
        except Exception as exc:
            print(f"[visualizer] Skipping equity_curve: {exc}")

        try:
            self.plot_win_rate_by_confidence(results)
            saved.append(self.output_dir / "win_rate_by_confidence.png")
        except Exception as exc:
            print(f"[visualizer] Skipping win_rate_by_confidence: {exc}")

        try:
            self.plot_weekend_effect_heatmap(data_df)
            saved.append(self.output_dir / "weekend_heatmap.png")
        except Exception as exc:
            print(f"[visualizer] Skipping weekend_heatmap: {exc}")

        try:
            self.plot_monthly_pnl(results)
            saved.append(self.output_dir / "monthly_pnl.png")
        except Exception as exc:
            print(f"[visualizer] Skipping monthly_pnl: {exc}")

        try:
            self.plot_hit_rate_over_time(results)
            saved.append(self.output_dir / "hit_rate_over_time.png")
        except Exception as exc:
            print(f"[visualizer] Skipping hit_rate_over_time: {exc}")

        if simulations is not None:
            try:
                starting = results.get("starting_bankroll", 100.0)
                self.plot_monte_carlo(simulations, starting_bankroll=starting)
                saved.append(self.output_dir / "monte_carlo.png")
            except Exception as exc:
                print(f"[visualizer] Skipping monte_carlo: {exc}")

        print(f"[visualizer] Saved {len(saved)} plots to {self.output_dir}/")
        return saved
