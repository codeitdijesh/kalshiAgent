"""
Weekend Effect Trading Strategy for Kalshi Netflix Top 10 Markets.

The core hypothesis: shows that dominate Netflix viewership on weekends (Sat/Sun)
tend to top the official weekly chart, even if another show held #1 for more
weekdays. This creates a predictable signal for Kalshi prediction markets.

Classes:
    Signal: Dataclass holding a trading signal's prediction, confidence, and reasoning.
    WeekendEffectStrategy: Generates signals from daily Netflix ranking data.
    AlphaOverrideStrategy: TikTok-volume ">2x Rule" that can override the baseline signal.
    KalshiSimulator: Simulates Kalshi contract mechanics, position sizing, and P&L.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Callable, Literal, Optional

import pandas as pd
import numpy as np

import config as cfg


# ---------------------------------------------------------------------------
# Data containers
# ---------------------------------------------------------------------------

@dataclass
class Signal:
    """A single trading signal produced by the Weekend Effect strategy.

    Attributes:
        predicted_winner: Title predicted to be #1 on the official weekly chart.
        confidence: Float in [0, 1] representing how confident the signal is.
        signal_strength: Categorical strength label derived from confidence.
        reasoning: Human-readable explanation of why this signal was generated.
        week: ISO week string (e.g. '2025-W03') the signal pertains to.
        category: 'TV' or 'Films', if known.
    """

    predicted_winner: str
    confidence: float
    signal_strength: Literal["strong", "medium", "weak"]
    reasoning: str
    week: str = ""
    category: str = ""


# ---------------------------------------------------------------------------
# Weekend Effect Strategy
# ---------------------------------------------------------------------------

class WeekendEffectStrategy:
    """Generate trading signals based on the Netflix Weekend Effect.

    The strategy inspects daily rankings for a given week and predicts
    which title will top the official weekly chart released on Tuesday.

    Signal generation rules
    -----------------------
    * **Strong (confidence > 0.8)** – Title was #1 on *both* Saturday and
      Sunday AND was *not* #1 for more than 3 weekdays.
    * **Medium (confidence 0.5–0.8)** – Title was #1 on one weekend day, or
      dominated both weekend days *and* most weekdays.
    * **Weak (confidence < 0.5)** – Ambiguous data; no clear weekend dominance.

    Confidence boosters
    -------------------
    * New release (cumulative_weeks_in_top_10 ≤ 2): **+0.10**
    * Title has been *rising* in rank over the week: **+0.05**
    * Large gap between #1 and #2 on the weekend: **+0.05**
    """

    # Day-of-week integers (Monday=0 … Sunday=6)
    _WEEKEND_DAYS: set[int] = {5, 6}  # Saturday, Sunday
    _WEEKDAY_INDICES: set[int] = {0, 1, 2, 3, 4}

    def __init__(self) -> None:
        #: Raw variables behind the last signal (for dashboards/telemetry).
        self.last_breakdown: dict = {}

    def generate_signal(self, week_data: pd.DataFrame) -> Optional[Signal]:
        """Produce a Weekend Effect signal for a single week of daily data.

        Parameters
        ----------
        week_data : pd.DataFrame
            Must contain at minimum the columns:
                * ``date`` – date of the observation (parseable by pandas).
                * ``rank`` – integer daily rank (1 = best).
                * ``title`` – show / film title.
            Optional columns that enable confidence boosters:
                * ``cumulative_weeks_in_top_10`` – integer.
                * ``category`` – 'TV' or 'Films'.

        Returns
        -------
        Signal or None
            ``None`` when there is insufficient data (e.g. no weekend data).
        """
        if week_data.empty:
            return None

        df = week_data.copy()
        df["date"] = pd.to_datetime(df["date"])
        df["dow"] = df["date"].dt.dayofweek  # Mon=0 … Sun=6

        # --- Identify weekend and weekday #1 titles ---
        weekend_df = df[df["dow"].isin(self._WEEKEND_DAYS)]
        weekday_df = df[df["dow"].isin(self._WEEKDAY_INDICES)]

        if weekend_df.empty:
            return None

        weekend_top1 = self._top1_by_day(weekend_df)
        weekday_top1 = self._top1_by_day(weekday_df)

        # Count how many weekend days each title held #1
        weekend_counts: dict[str, int] = {}
        for title in weekend_top1.values():
            weekend_counts[title] = weekend_counts.get(title, 0) + 1

        # The title that held #1 on the most weekend days
        weekend_winner = max(weekend_counts, key=weekend_counts.get)  # type: ignore[arg-type]
        weekend_days_at_top = weekend_counts[weekend_winner]

        # How many weekdays did the weekend winner also hold #1?
        weekday_days_at_top = sum(
            1 for t in weekday_top1.values() if t == weekend_winner
        )

        # --- Base confidence ---
        both_weekend_days = weekend_days_at_top == 2
        dominated_weekdays = weekday_days_at_top > 3

        if both_weekend_days and not dominated_weekdays:
            base_confidence = 0.82
        elif both_weekend_days and dominated_weekdays:
            base_confidence = 0.65
        elif weekend_days_at_top == 1:
            base_confidence = 0.55
        else:
            base_confidence = 0.40

        # --- Confidence boosters ---
        boost = 0.0
        reasoning_parts: list[str] = []

        # 1. New release boost
        if "cumulative_weeks_in_top_10" in df.columns:
            winner_rows = df[df["title"] == weekend_winner]
            if not winner_rows.empty:
                cum_weeks = winner_rows["cumulative_weeks_in_top_10"].min()
                if cum_weeks <= 2:
                    boost += 0.10
                    reasoning_parts.append(
                        f"New release (week {cum_weeks} on chart): +0.10"
                    )

        # 2. Rising rank boost – compare early-week rank to late-week rank
        boost_rising = self._rising_rank_boost(df, weekend_winner)
        if boost_rising > 0:
            boost += boost_rising
            reasoning_parts.append("Title rising in rank over the week: +0.05")

        # 3. Large weekend gap boost
        boost_gap = self._weekend_gap_boost(weekend_df, weekend_winner)
        if boost_gap > 0:
            boost += boost_gap
            reasoning_parts.append("Large gap vs #2 on weekends: +0.05")

        confidence = min(base_confidence + boost, 0.99)

        # --- Determine signal strength ---
        if confidence > 0.8:
            strength: Literal["strong", "medium", "weak"] = "strong"
        elif confidence >= 0.5:
            strength = "medium"
        else:
            strength = "weak"

        # --- Build reasoning string ---
        reasoning_header = (
            f"'{weekend_winner}' held weekend #1 for {weekend_days_at_top}/2 day(s) "
            f"and weekday #1 for {weekday_days_at_top}/5 day(s)."
        )
        reasoning = "\n".join([reasoning_header] + reasoning_parts)

        # --- Week label ---
        week_label = str(df["date"].min().isocalendar()[:2])

        # --- Category ---
        category = ""
        if "category" in df.columns:
            cats = df.loc[df["title"] == weekend_winner, "category"].unique()
            if len(cats):
                category = str(cats[0])

        self.last_breakdown = {
            "weekend_winner": weekend_winner,
            "weekend_days_at_top": weekend_days_at_top,
            "weekday_days_at_top": weekday_days_at_top,
            "weekend_top1": {int(k): v for k, v in weekend_top1.items()},
            "weekday_top1": {int(k): v for k, v in weekday_top1.items()},
            "base_confidence": base_confidence,
            "boosts": reasoning_parts,
            "boost_total": round(boost, 4),
            "final_confidence": round(confidence, 4),
        }

        return Signal(
            predicted_winner=weekend_winner,
            confidence=round(confidence, 4),
            signal_strength=strength,
            reasoning=reasoning,
            week=week_label,
            category=category,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _top1_by_day(df: pd.DataFrame) -> dict[int, str]:
        """Return a mapping ``{day_of_week: title_at_rank_1}``."""
        top1: dict[int, str] = {}
        for dow, group in df.groupby("dow"):
            rank1 = group.loc[group["rank"].idxmin()]
            top1[int(dow)] = str(rank1["title"])
        return top1

    @staticmethod
    def _rising_rank_boost(df: pd.DataFrame, title: str) -> float:
        """Return +0.05 if *title* improved rank from the first half to the second half of the week."""
        title_df = df[df["title"] == title].sort_values("date")
        if len(title_df) < 2:
            return 0.0
        midpoint = len(title_df) // 2
        early_avg = title_df.iloc[:midpoint]["rank"].mean()
        late_avg = title_df.iloc[midpoint:]["rank"].mean()
        # Lower rank number = better, so improvement means late < early
        if late_avg < early_avg:
            return 0.05
        return 0.0

    @staticmethod
    def _weekend_gap_boost(weekend_df: pd.DataFrame, winner: str) -> float:
        """Return +0.05 if the gap between #1 and #2 on weekends is ≥ 2 ranks on average."""
        gaps: list[float] = []
        for _, day_group in weekend_df.groupby("dow"):
            sorted_group = day_group.sort_values("rank")
            if len(sorted_group) >= 2 and str(sorted_group.iloc[0]["title"]) == winner:
                gap = int(sorted_group.iloc[1]["rank"]) - int(sorted_group.iloc[0]["rank"])
                gaps.append(gap)
        if gaps and np.mean(gaps) >= 2:
            return 0.05
        return 0.0


# ---------------------------------------------------------------------------
# Alpha Override — the TikTok ">2x Rule"
# ---------------------------------------------------------------------------

class AlphaOverrideStrategy:
    """Override the baseline prediction using TikTok hashtag volume.

    The rule
    --------
    Given the Top 3 US FlixPatrol contenders (rank order), if a trailing
    contender (#2 or #3) has **more than 2x** the TikTok views of the #1
    show, the trailing contender is the true cultural winner. The signal
    is overridden and stamped with a flat hyper-conviction confidence
    (``config.ALPHA_OVERRIDE_CONFIDENCE`` = 0.85).

    The oracle is injected as a callable (``title -> views | None``) so the
    strategy stays testable and never imports network code directly. Any
    ``None`` volume (API failure, no videos) disables the override for the
    affected comparison — fail-safe by construction.
    """

    def __init__(
        self,
        volume_fn: Optional[Callable[[str], Optional[int]]] = None,
        multiplier: float = cfg.ALPHA_OVERRIDE_MULTIPLIER,
        override_confidence: float = cfg.ALPHA_OVERRIDE_CONFIDENCE,
    ) -> None:
        if volume_fn is None:
            # Default to the fail-safe module-level oracle
            from data_collector import get_tiktok_volume
            volume_fn = get_tiktok_volume
        self._volume_fn = volume_fn
        self._multiplier = multiplier
        self._override_confidence = override_confidence
        #: TikTok volumes observed on the last apply() (for dashboards).
        self.last_volumes: dict[str, Optional[int]] = {}
        self.last_fired: bool = False

    def apply(
        self,
        baseline_signal: Signal,
        top_contenders: list[str],
    ) -> Signal:
        """Apply the >2x rule to a baseline signal.

        Parameters
        ----------
        baseline_signal : Signal
            The Weekend Effect (or other baseline) signal.
        top_contenders : list[str]
            Titles in current FlixPatrol rank order. Only the first 3 are
            ever queried (API supremacy).

        Returns
        -------
        Signal
            Either the untouched baseline signal, or a new overridden
            signal with confidence 0.85 and full reasoning.
        """
        self.last_volumes = {}
        self.last_fired = False
        contenders = top_contenders[: cfg.TIKTOK_MAX_CONTENDERS]
        if len(contenders) < 2:
            return baseline_signal

        volumes: dict[str, Optional[int]] = {}
        for title in contenders:
            volumes[title] = self._safe_volume(title)
        self.last_volumes = volumes

        leader = contenders[0]
        leader_views = volumes.get(leader)
        if leader_views is None:
            # Can't compare against an unknown leader — keep baseline.
            return baseline_signal

        # Find the trailing contender with the strongest override case
        best_challenger: Optional[str] = None
        best_views = 0
        for title in contenders[1:]:
            views = volumes.get(title)
            if views is None:
                continue
            if views > leader_views * self._multiplier and views > best_views:
                best_challenger = title
                best_views = views

        if best_challenger is None:
            return baseline_signal

        self.last_fired = True
        ratio = best_views / leader_views if leader_views > 0 else float("inf")
        reasoning = (
            f"ALPHA OVERRIDE (>{self._multiplier:g}x TikTok Rule): "
            f"'{best_challenger}' has {best_views:,} TikTok views vs "
            f"{leader_views:,} for FlixPatrol #1 '{leader}' "
            f"({ratio:.1f}x). Overriding baseline prediction "
            f"'{baseline_signal.predicted_winner}' — the trailing contender "
            f"is the true cultural winner.\n"
            f"--- Baseline reasoning ---\n{baseline_signal.reasoning}"
        )

        return Signal(
            predicted_winner=best_challenger,
            confidence=self._override_confidence,
            signal_strength="strong",
            reasoning=reasoning,
            week=baseline_signal.week,
            category=baseline_signal.category,
        )

    def _safe_volume(self, title: str) -> Optional[int]:
        """Query the oracle; any exception degrades to ``None``."""
        try:
            return self._volume_fn(title)
        except Exception:  # noqa: BLE001 — the Master Agent does not crash
            return None


# ---------------------------------------------------------------------------
# Oracle Ensemble — bounded multi-variable confidence adjustments
# ---------------------------------------------------------------------------

class OracleEnsemble:
    """Refine a signal's confidence using the full sensory suite.

    Runs *after* the baseline strategy and the TikTok Alpha Override.
    Each oracle contributes a small, bounded, transparent adjustment;
    the prediction itself is never changed here (only the Alpha Override
    may flip the pick). Any oracle failure simply skips its adjustment.

    Adjustments (per ``config``)
    ----------------------------
    * **Wikipedia pageviews** — winner has >2x every rival: +0.05;
      a rival has >2x the winner: −0.05.
    * **Google Trends (US)** — same dominance logic on mean 7-day interest.
    * **TMDB release velocity** — winner premiered within the current
      Netflix week's first days yet already tops the chart: +0.05
      (explosive views-per-day velocity). Runtime is recorded as
      bingeability context in the reasoning.
    * **YouTube trailer velocity** — winner's trailer views-per-day >2x
      every rival: +0.05; a rival >2x the winner: −0.05.

    Final confidence is clamped to
    [``CONFIDENCE_FLOOR``, ``CONFIDENCE_CEILING``].
    """

    def __init__(
        self,
        wiki_fn: Optional[Callable[[str, str], Optional[int]]] = None,
        trends_fn: Optional[Callable[[list[str]], Optional[dict[str, float]]]] = None,
        tmdb_fn: Optional[Callable[[str, str], Optional[dict]]] = None,
        youtube_fn: Optional[Callable[[str], Optional[dict]]] = None,
    ) -> None:
        self._wiki_fn = wiki_fn
        self._trends_fn = trends_fn
        self._tmdb_fn = tmdb_fn
        self._youtube_fn = youtube_fn
        #: Raw oracle magnitudes + per-oracle adjustments from the last
        #: apply() call (for dashboards/telemetry).
        self.last_diagnostics: dict = {}

    def apply(
        self,
        signal: Signal,
        contenders: list[str],
        week_start: Optional[pd.Timestamp] = None,
    ) -> Signal:
        """Return a new Signal with ensemble-adjusted confidence."""
        winner = signal.predicted_winner
        rivals = [t for t in contenders[: cfg.TIKTOK_MAX_CONTENDERS] if t != winner]
        self.last_diagnostics = {}
        if not rivals:
            return signal

        adjustment = 0.0
        notes: list[str] = []

        wiki_mags = self._wiki_magnitudes(winner, rivals, signal.category)
        wiki_adj = self._magnitude_adjustment(
            "Wikipedia pageviews", wiki_mags, winner, notes
        )
        trends_mags = self._trends_magnitudes([winner] + rivals)
        trends_adj = self._magnitude_adjustment(
            "Google Trends interest", trends_mags, winner, notes
        )
        tmdb_adj = self._release_velocity_adjustment(
            winner, signal.category, week_start, notes
        )
        youtube_mags = self._youtube_magnitudes(winner, rivals)
        youtube_adj = self._magnitude_adjustment(
            "YouTube trailer velocity", youtube_mags, winner, notes
        )
        adjustment = wiki_adj + trends_adj + tmdb_adj + youtube_adj

        self.last_diagnostics = {
            "wikipedia": {"magnitudes": wiki_mags, "adjustment": wiki_adj},
            "google_trends": {"magnitudes": trends_mags, "adjustment": trends_adj},
            "tmdb": {"adjustment": tmdb_adj},
            "youtube": {"magnitudes": youtube_mags, "adjustment": youtube_adj},
            "net_adjustment": round(adjustment, 4),
            "notes": list(notes),
        }

        if adjustment == 0.0 and not notes:
            return signal

        confidence = max(
            cfg.CONFIDENCE_FLOOR,
            min(cfg.CONFIDENCE_CEILING, signal.confidence + adjustment),
        )
        strength: Literal["strong", "medium", "weak"] = (
            "strong" if confidence > 0.8 else "medium" if confidence >= 0.5 else "weak"
        )
        reasoning = signal.reasoning
        if notes:
            reasoning += "\n--- Oracle Ensemble ---\n" + "\n".join(notes)
            reasoning += f"\nEnsemble net adjustment: {adjustment:+.2f}"

        return Signal(
            predicted_winner=winner,
            confidence=round(confidence, 4),
            signal_strength=strength,
            reasoning=reasoning,
            week=signal.week,
            category=signal.category,
        )

    # ------------------------------------------------------------------
    # Magnitude collectors (each fail-safe → None disables the oracle)
    # ------------------------------------------------------------------

    def _wiki_magnitudes(
        self, winner: str, rivals: list[str], category: str
    ) -> Optional[dict[str, float]]:
        if self._wiki_fn is None:
            return None
        result: dict[str, float] = {}
        for title in [winner] + rivals:
            try:
                views = self._wiki_fn(title, category)
            except Exception:  # noqa: BLE001
                views = None
            if views is None:
                return None  # can't compare with a blind spot
            result[title] = float(views)
        return result

    def _trends_magnitudes(self, titles: list[str]) -> Optional[dict[str, float]]:
        if self._trends_fn is None:
            return None
        try:
            return self._trends_fn(titles)
        except Exception:  # noqa: BLE001
            return None

    def _youtube_magnitudes(
        self, winner: str, rivals: list[str]
    ) -> Optional[dict[str, float]]:
        if self._youtube_fn is None:
            return None
        result: dict[str, float] = {}
        for title in [winner] + rivals:
            try:
                stats = self._youtube_fn(title)
            except Exception:  # noqa: BLE001
                stats = None
            velocity = (stats or {}).get("views_per_day")
            if velocity is None:
                return None
            result[title] = float(velocity)
        return result

    # ------------------------------------------------------------------
    # Adjustment rules
    # ------------------------------------------------------------------

    @staticmethod
    def _magnitude_adjustment(
        label: str,
        magnitudes: Optional[dict[str, float]],
        winner: str,
        notes: list[str],
    ) -> float:
        """+boost if winner >2x all rivals, −penalty if any rival >2x winner."""
        if not magnitudes or winner not in magnitudes or len(magnitudes) < 2:
            return 0.0
        winner_mag = magnitudes[winner]
        rival_mags = [v for k, v in magnitudes.items() if k != winner]
        best_rival = max(rival_mags)

        if best_rival > 0 and winner_mag > best_rival * cfg.ENSEMBLE_DOMINANCE_RATIO:
            notes.append(
                f"{label}: winner dominates ({winner_mag:,.0f} vs best rival "
                f"{best_rival:,.0f}): +{cfg.ENSEMBLE_BOOST:.2f}"
            )
            return cfg.ENSEMBLE_BOOST
        if winner_mag > 0 and best_rival > winner_mag * cfg.ENSEMBLE_DOMINANCE_RATIO:
            notes.append(
                f"{label}: a rival dominates the winner ({best_rival:,.0f} vs "
                f"{winner_mag:,.0f}): -{cfg.ENSEMBLE_PENALTY:.2f}"
            )
            return -cfg.ENSEMBLE_PENALTY
        return 0.0

    def _release_velocity_adjustment(
        self,
        winner: str,
        category: str,
        week_start: Optional[pd.Timestamp],
        notes: list[str],
    ) -> float:
        if self._tmdb_fn is None:
            return 0.0
        try:
            meta = self._tmdb_fn(winner, category)
        except Exception:  # noqa: BLE001
            return 0.0
        if not meta:
            return 0.0

        runtime = meta.get("runtime_min")
        if runtime:
            notes.append(f"TMDB bingeability context: runtime ≈ {runtime} min")

        release = meta.get("release_date")
        if not release or week_start is None:
            return 0.0
        try:
            release_ts = pd.Timestamp(release)
        except (ValueError, TypeError):
            return 0.0

        days_into_week = (release_ts - week_start).days
        if 0 <= days_into_week < cfg.FRESH_RELEASE_DAYS:
            notes.append(
                f"TMDB release velocity: premiered {release} "
                f"(day {days_into_week + 1} of the Netflix week) and already "
                f"tops the chart — explosive views-per-day: +{cfg.ENSEMBLE_BOOST:.2f}"
            )
            return cfg.ENSEMBLE_BOOST
        return 0.0


# ---------------------------------------------------------------------------
# Kalshi Market Simulator
# ---------------------------------------------------------------------------

class KalshiSimulator:
    """Simulate Kalshi binary-contract market mechanics.

    Kalshi contracts pay $1 if correct and $0 if incorrect.
    Buying a YES contract at price *p* costs *$p* and returns *$1 − p*
    profit on a win, or *−$p* loss on a miss.
    """

    def __init__(self, seed: Optional[int] = None) -> None:
        """Initialise the simulator.

        Parameters
        ----------
        seed : int, optional
            Random seed for reproducibility of market-price noise.
        """
        self._rng = random.Random(seed)

    def simulate_market_price(
        self, confidence: float, noise: float = 0.10
    ) -> float:
        """Simulate a plausible market price given a model confidence.

        The market price is modelled as the true confidence plus Gaussian
        noise, clipped to [0.05, 0.95].

        Parameters
        ----------
        confidence : float
            The strategy's estimated probability of the event occurring.
        noise : float
            Standard deviation of the Gaussian noise added to confidence.

        Returns
        -------
        float
            Simulated market price in [0.05, 0.95].
        """
        price = confidence + self._rng.gauss(0, noise)
        return round(max(0.05, min(0.95, price)), 4)

    @staticmethod
    def calculate_position_size(
        confidence: float,
        bankroll: float,
        kelly_fraction: float = cfg.KELLY_FRACTION,
        market_price: Optional[float] = None,
    ) -> int:
        """Compute position size (number of contracts) via fractional Kelly criterion.

        Parameters
        ----------
        confidence : float
            Estimated probability of the event (our edge).
        bankroll : float
            Current bankroll in dollars.
        kelly_fraction : float
            Fraction of the full Kelly bet to use (default 0.25 = quarter-Kelly).
        market_price : float, optional
            The price we'd pay per YES contract.  Defaults to ``confidence``
            (i.e. fair-value pricing) if not provided.

        Returns
        -------
        int
            Number of contracts to buy (≥ 0).
        """
        p = confidence
        price = market_price if market_price is not None else confidence

        if price <= 0 or price >= 1:
            return 0

        # Payoff odds: win → (1 - price)/price, lose → -1
        b = (1 - price) / price
        # Kelly fraction: f* = (bp - q) / b where q = 1 - p
        q = 1 - p
        kelly_f = (b * p - q) / b
        if kelly_f <= 0:
            return 0

        bet_dollars = bankroll * kelly_f * kelly_fraction
        contracts = int(bet_dollars / price)
        return max(contracts, 0)

    @staticmethod
    def calculate_pnl(
        entry_price: float, outcome: bool, contracts: int
    ) -> float:
        """Calculate profit / loss for a position.

        Parameters
        ----------
        entry_price : float
            Price paid per YES contract (in dollars, 0–1).
        outcome : bool
            ``True`` if the event occurred (contract pays $1), ``False`` otherwise.
        contracts : int
            Number of contracts held.

        Returns
        -------
        float
            Net P&L in dollars (positive = profit).
        """
        if outcome:
            return round((1.0 - entry_price) * contracts, 4)
        else:
            return round(-entry_price * contracts, 4)
