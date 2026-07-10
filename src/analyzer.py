"""
Statistical analysis module for the Netflix Weekend Effect strategy.

Provides tools for evaluating whether the Weekend Effect is a statistically
significant phenomenon and estimating the expected edge when trading on
Kalshi prediction markets.

Classes:
    WeekendEffectAnalyzer: All analysis methods – hit rates, chi-squared
        significance, edge estimation, and Monte Carlo simulation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd
from scipy import stats as scipy_stats


# ---------------------------------------------------------------------------
# Data containers
# ---------------------------------------------------------------------------

@dataclass
class HitRateResult:
    """Stores a hit-rate computation and its sample size.

    Attributes:
        label: Description of the segment (e.g. 'TV', 'New releases').
        hits: Number of weeks where weekend #1 matched weekly winner.
        total: Total weeks evaluated.
        hit_rate: hits / total.
    """

    label: str
    hits: int
    total: int

    @property
    def hit_rate(self) -> float:
        return self.hits / self.total if self.total else 0.0


@dataclass
class MonteCarloResult:
    """Aggregated statistics from a Monte Carlo bankroll simulation.

    Attributes:
        mean_final_bankroll: Average ending bankroll across simulations.
        median_final_bankroll: Median ending bankroll.
        prob_profit: Probability of ending with more than the starting bankroll.
        prob_ruin: Probability of going bust (bankroll ≤ 0).
        percentile_5: 5th percentile of final bankrolls (worst-case estimate).
        percentile_95: 95th percentile of final bankrolls (best-case estimate).
        all_final_bankrolls: Array of final bankroll values (one per simulation).
    """

    mean_final_bankroll: float
    median_final_bankroll: float
    prob_profit: float
    prob_ruin: float
    percentile_5: float
    percentile_95: float
    all_final_bankrolls: np.ndarray = field(repr=False)


# ---------------------------------------------------------------------------
# Analyzer
# ---------------------------------------------------------------------------

class WeekendEffectAnalyzer:
    """Analyse the Netflix Weekend Effect across historical data.

    All public methods accept a ``data_df`` :class:`~pandas.DataFrame`
    that must contain **at minimum**:

    * ``date`` – daily observation date (parseable by pandas).
    * ``rank`` – integer daily rank (1 = best).
    * ``title`` – show / film title.
    * ``weekly_rank`` – official weekly rank (1 = winner).  Used to
      determine the ground-truth weekly winner.

    Optional columns for conditional analyses:

    * ``category`` – ``'TV'`` or ``'Films'``.
    * ``cumulative_weeks_in_top_10`` – integer.
    """

    # Day-of-week constants (Monday = 0, Sunday = 6)
    _WEEKEND_DAYS: set[int] = {5, 6}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def compute_hit_rate(self, data_df: pd.DataFrame) -> HitRateResult:
        """Compute the overall hit rate of the Weekend Effect.

        The *hit rate* is the fraction of weeks where the title that was
        #1 on the most weekend days (Sat/Sun) also turned out to be #1
        on the official weekly chart.

        Parameters
        ----------
        data_df : pd.DataFrame
            Historical daily + weekly ranking data.

        Returns
        -------
        HitRateResult
            Overall hit-rate result.
        """
        weekly_results = self._evaluate_weeks(data_df)
        hits = sum(1 for r in weekly_results if r["hit"])
        return HitRateResult(label="Overall", hits=hits, total=len(weekly_results))

    def compute_conditional_hit_rates(
        self, data_df: pd.DataFrame
    ) -> dict[str, list[HitRateResult]]:
        """Compute hit rates segmented by various conditions.

        Returns a dictionary with keys:

        * ``'by_category'`` – TV vs Films (requires ``category`` column).
        * ``'by_novelty'`` – New releases (≤ 2 weeks) vs established.
        * ``'by_dominance'`` – Strong (both weekend days) vs partial.

        Parameters
        ----------
        data_df : pd.DataFrame
            Historical daily + weekly ranking data.

        Returns
        -------
        dict[str, list[HitRateResult]]
        """
        weekly_results = self._evaluate_weeks(data_df)
        results: dict[str, list[HitRateResult]] = {}

        # --- By category ---
        if "category" in data_df.columns:
            by_cat: dict[str, list[dict]] = {}
            for r in weekly_results:
                cat = r.get("category", "Unknown")
                by_cat.setdefault(cat, []).append(r)
            results["by_category"] = [
                HitRateResult(
                    label=cat,
                    hits=sum(1 for r in rows if r["hit"]),
                    total=len(rows),
                )
                for cat, rows in sorted(by_cat.items())
            ]

        # --- By novelty ---
        if "cum_weeks" in (weekly_results[0] if weekly_results else {}):
            new = [r for r in weekly_results if r.get("cum_weeks", 99) <= 2]
            est = [r for r in weekly_results if r.get("cum_weeks", 99) > 2]
            results["by_novelty"] = [
                HitRateResult("New releases (≤2 wks)", sum(1 for r in new if r["hit"]), len(new)),
                HitRateResult("Established (>2 wks)", sum(1 for r in est if r["hit"]), len(est)),
            ]

        # --- By weekend dominance ---
        strong = [r for r in weekly_results if r.get("weekend_days_at_top", 0) == 2]
        partial = [r for r in weekly_results if r.get("weekend_days_at_top", 0) < 2]
        results["by_dominance"] = [
            HitRateResult("Strong (both days)", sum(1 for r in strong if r["hit"]), len(strong)),
            HitRateResult("Partial (<2 days)", sum(1 for r in partial if r["hit"]), len(partial)),
        ]

        return results

    def chi_squared_test(self, data_df: pd.DataFrame) -> dict[str, float]:
        """Run a chi-squared test of independence on the Weekend Effect.

        Tests whether being #1 on the weekend is independent of being #1
        on the weekly chart (null hypothesis).

        Parameters
        ----------
        data_df : pd.DataFrame
            Historical daily + weekly ranking data.

        Returns
        -------
        dict with keys:
            * ``chi2``: chi-squared statistic.
            * ``p_value``: p-value of the test.
            * ``dof``: degrees of freedom.
            * ``significant_at_05``: bool – significant at α = 0.05?
            * ``n``: sample size.
        """
        weekly_results = self._evaluate_weeks(data_df)
        if not weekly_results:
            return {"chi2": 0.0, "p_value": 1.0, "dof": 0, "significant_at_05": False, "n": 0}

        # Build a 2×2 contingency table:
        #                      Weekly winner | Not weekly winner
        # Weekend #1 matched        a               b
        # Weekend #1 didn't         c               d
        a = sum(1 for r in weekly_results if r["hit"])
        b = sum(1 for r in weekly_results if not r["hit"])
        # For a proper 2×2 table we need the complementary counts.
        # Here we test whether weekend-#1 status is associated with
        # weekly-winner status.  Because every row *has* a weekend #1
        # and a weekly winner, we compare to a baseline expectation.
        # We model the null as a uniform 1/N chance (N = unique titles
        # that week).  Approximate with average unique titles per week.
        avg_titles = np.mean([r.get("n_titles", 10) for r in weekly_results])
        expected_hit_rate = 1.0 / avg_titles if avg_titles > 0 else 0.1

        observed = np.array([a, b])
        expected = np.array([
            len(weekly_results) * expected_hit_rate,
            len(weekly_results) * (1 - expected_hit_rate),
        ])
        # Ensure expected values are at least 1 to avoid division by zero
        expected = np.maximum(expected, 1.0)

        chi2 = float(np.sum((observed - expected) ** 2 / expected))
        dof = 1
        p_value = float(1 - scipy_stats.chi2.cdf(chi2, dof))

        return {
            "chi2": round(chi2, 4),
            "p_value": round(p_value, 6),
            "dof": dof,
            "significant_at_05": p_value < 0.05,
            "n": len(weekly_results),
        }

    @staticmethod
    def compute_edge(hit_rate: float, avg_market_price: float) -> float:
        """Compute the expected value per $1 bet.

        Parameters
        ----------
        hit_rate : float
            Observed probability of the signal being correct.
        avg_market_price : float
            Average price paid per YES contract.

        Returns
        -------
        float
            Expected profit per dollar risked.  Positive values indicate
            an edge.

        Examples
        --------
        >>> WeekendEffectAnalyzer.compute_edge(0.65, 0.55)
        0.18...
        """
        # EV = hit_rate * (1 - price) - (1 - hit_rate) * price
        #    = hit_rate - price
        ev_per_contract = hit_rate * (1 - avg_market_price) - (1 - hit_rate) * avg_market_price
        ev_per_dollar = ev_per_contract / avg_market_price if avg_market_price > 0 else 0.0
        return round(ev_per_dollar, 4)

    def monte_carlo_simulation(
        self,
        hit_rate: float,
        num_trades: int,
        bankroll: float,
        num_simulations: int = 10_000,
        avg_market_price: float = 0.55,
        kelly_fraction: float = 0.25,
        seed: Optional[int] = None,
    ) -> MonteCarloResult:
        """Run a Monte Carlo simulation of bankroll evolution.

        Simulates *num_simulations* independent paths, each consisting
        of *num_trades* sequential bets at the given *hit_rate*.

        Parameters
        ----------
        hit_rate : float
            Probability each trade is a winner.
        num_trades : int
            Number of sequential trades per simulation path.
        bankroll : float
            Starting bankroll in dollars.
        num_simulations : int
            Number of simulation paths.
        avg_market_price : float
            Average entry price per YES contract.
        kelly_fraction : float
            Fraction of Kelly criterion used for bet sizing.
        seed : int, optional
            Random seed for reproducibility.

        Returns
        -------
        MonteCarloResult
        """
        rng = np.random.default_rng(seed)

        price = avg_market_price
        if price <= 0 or price >= 1:
            raise ValueError(f"avg_market_price must be in (0, 1), got {price}")

        b = (1 - price) / price  # payoff odds
        q = 1 - hit_rate
        kelly_f = (b * hit_rate - q) / b
        kelly_f = max(kelly_f, 0.0) * kelly_fraction

        final_bankrolls = np.empty(num_simulations)

        for i in range(num_simulations):
            br = bankroll
            outcomes = rng.random(num_trades) < hit_rate  # True = win
            for win in outcomes:
                if br <= 0:
                    break
                bet_dollars = br * kelly_f
                contracts = int(bet_dollars / price)
                if contracts <= 0:
                    continue
                if win:
                    br += (1 - price) * contracts
                else:
                    br -= price * contracts
            final_bankrolls[i] = br

        return MonteCarloResult(
            mean_final_bankroll=round(float(np.mean(final_bankrolls)), 2),
            median_final_bankroll=round(float(np.median(final_bankrolls)), 2),
            prob_profit=round(float(np.mean(final_bankrolls > bankroll)), 4),
            prob_ruin=round(float(np.mean(final_bankrolls <= 0)), 4),
            percentile_5=round(float(np.percentile(final_bankrolls, 5)), 2),
            percentile_95=round(float(np.percentile(final_bankrolls, 95)), 2),
            all_final_bankrolls=final_bankrolls,
        )

    def generate_report(self, data_df: pd.DataFrame) -> str:
        """Generate a full statistical report as a formatted string.

        Parameters
        ----------
        data_df : pd.DataFrame
            Historical daily + weekly ranking data.

        Returns
        -------
        str
            Multi-line formatted report.
        """
        lines: list[str] = []

        lines.append("═" * 55)
        lines.append("   WEEKEND EFFECT — STATISTICAL ANALYSIS REPORT")
        lines.append("═" * 55)

        # -- Overall hit rate --
        hr = self.compute_hit_rate(data_df)
        lines.append(f"\n▸ Overall hit rate: {hr.hit_rate:.1%}  ({hr.hits}/{hr.total} weeks)")

        # -- Conditional hit rates --
        cond = self.compute_conditional_hit_rates(data_df)
        for group_name, hr_list in cond.items():
            lines.append(f"\n▸ {group_name}:")
            for h in hr_list:
                lines.append(f"    {h.label:<30s} {h.hit_rate:.1%}  ({h.hits}/{h.total})")

        # -- Chi-squared test --
        chi = self.chi_squared_test(data_df)
        lines.append(f"\n▸ Chi-squared test (n={chi['n']}):")
        lines.append(f"    χ² = {chi['chi2']:.2f},  p = {chi['p_value']:.4f}")
        sig = "YES ✓" if chi["significant_at_05"] else "NO ✗"
        lines.append(f"    Significant at α=0.05? {sig}")

        # -- Edge estimate --
        if hr.hit_rate > 0:
            assumed_price = 0.55
            edge = self.compute_edge(hr.hit_rate, assumed_price)
            lines.append(f"\n▸ Edge estimate (assuming avg market price ${assumed_price:.2f}):")
            lines.append(f"    EV per $1 risked: ${edge:+.4f}")

        # -- Monte Carlo --
        if hr.hit_rate > 0:
            mc = self.monte_carlo_simulation(
                hit_rate=hr.hit_rate,
                num_trades=52,
                bankroll=100.0,
                seed=42,
            )
            lines.append(f"\n▸ Monte Carlo simulation (52 trades, $100 start, 10k paths):")
            lines.append(f"    Mean final bankroll  : ${mc.mean_final_bankroll:.2f}")
            lines.append(f"    Median final bankroll: ${mc.median_final_bankroll:.2f}")
            lines.append(f"    P(profit)            : {mc.prob_profit:.1%}")
            lines.append(f"    P(ruin)              : {mc.prob_ruin:.1%}")
            lines.append(f"    5th percentile       : ${mc.percentile_5:.2f}")
            lines.append(f"    95th percentile      : ${mc.percentile_95:.2f}")

        lines.append("\n" + "═" * 55)

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _evaluate_weeks(self, data_df: pd.DataFrame) -> list[dict]:
        """Break *data_df* into ISO weeks and evaluate each one.

        Returns a list of dicts, one per week, with keys:
            * ``week_key``: (year, week) tuple.
            * ``weekend_winner``: title that was #1 most weekend days.
            * ``weekly_winner``: actual weekly #1.
            * ``hit``: bool – did they match?
            * ``weekend_days_at_top``: how many weekend days the winner held #1.
            * ``category``: category string, if available.
            * ``cum_weeks``: min cumulative weeks for weekend winner, if available.
            * ``n_titles``: unique titles that week.
        """
        df = data_df.copy()
        df["date"] = pd.to_datetime(df["date"])
        df["dow"] = df["date"].dt.dayofweek
        df["_iso_year"] = df["date"].dt.isocalendar().year.astype(int)
        df["_iso_week"] = df["date"].dt.isocalendar().week.astype(int)
        df["_week_key"] = list(zip(df["_iso_year"], df["_iso_week"]))

        results: list[dict] = []

        for week_key in sorted(df["_week_key"].unique()):
            week_df = df[df["_week_key"] == week_key]

            # Weekend #1 title
            weekend_df = week_df[week_df["dow"].isin(self._WEEKEND_DAYS)]
            if weekend_df.empty:
                continue

            weekend_top1 = self._top1_by_day(weekend_df)
            weekend_counts: dict[str, int] = {}
            for title in weekend_top1.values():
                weekend_counts[title] = weekend_counts.get(title, 0) + 1
            weekend_winner = max(weekend_counts, key=weekend_counts.get)  # type: ignore[arg-type]
            weekend_days_at_top = weekend_counts[weekend_winner]

            # Actual weekly winner
            weekly_winner: Optional[str] = None
            if "weekly_rank" in week_df.columns:
                top = week_df.loc[week_df["weekly_rank"] == 1]
                if not top.empty:
                    weekly_winner = str(top.iloc[0]["title"])
            if weekly_winner is None:
                avg_ranks = week_df.groupby("title")["rank"].mean()
                weekly_winner = str(avg_ranks.idxmin()) if not avg_ranks.empty else ""

            entry: dict = {
                "week_key": week_key,
                "weekend_winner": weekend_winner,
                "weekly_winner": weekly_winner,
                "hit": weekend_winner == weekly_winner,
                "weekend_days_at_top": weekend_days_at_top,
                "n_titles": week_df["title"].nunique(),
            }

            # Optional fields
            if "category" in week_df.columns:
                cats = week_df.loc[week_df["title"] == weekend_winner, "category"].unique()
                entry["category"] = str(cats[0]) if len(cats) else "Unknown"

            if "cumulative_weeks_in_top_10" in week_df.columns:
                cw = week_df.loc[
                    week_df["title"] == weekend_winner, "cumulative_weeks_in_top_10"
                ]
                entry["cum_weeks"] = int(cw.min()) if not cw.empty else 99

            results.append(entry)

        return results

    @staticmethod
    def _top1_by_day(df: pd.DataFrame) -> dict[int, str]:
        """Return ``{day_of_week: title_at_rank_1}`` for each day present."""
        top1: dict[int, str] = {}
        for dow, group in df.groupby("dow"):
            rank1 = group.loc[group["rank"].idxmin()]
            top1[int(dow)] = str(rank1["title"])
        return top1
