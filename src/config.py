"""
Configuration constants for the Kalshi Netflix Weekend Effect backtester.

Centralises all tuneable parameters — paths, URLs, rate-limit settings,
backtest date ranges, and Kalshi market mechanics — so nothing is
hard-coded in the scraping or analysis modules.
"""

from __future__ import annotations

import os
from datetime import date, timedelta
from pathlib import Path
from typing import Final

# Load API keys from the project-root .env file (see .env.example).
try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ImportError:
  # dotenv not installed — fall back to real env vars
    pass

# ──────────────────────────────────────────────────────────────────────
# Paths
# ──────────────────────────────────────────────────────────────────────
PROJECT_ROOT: Final[Path] = Path(__file__).resolve().parent
DATA_DIR: Final[Path] = PROJECT_ROOT / "data"
RAW_DIR: Final[Path] = DATA_DIR / "raw"
PROCESSED_DIR: Final[Path] = DATA_DIR / "processed"
SAMPLE_DIR: Final[Path] = DATA_DIR / "sample"

# Ensure directories exist on import
for _d in (DATA_DIR, RAW_DIR, PROCESSED_DIR, SAMPLE_DIR):
    _d.mkdir(parents=True, exist_ok=True)

# ──────────────────────────────────────────────────────────────────────
# FlixPatrol settings
# ──────────────────────────────────────────────────────────────────────
FLIXPATROL_BASE_URL: Final[str] = "https://flixpatrol.com/top10/netflix"
# URL pattern: {FLIXPATROL_BASE_URL}/{country}/{YYYY-MM-DD}/

FLIXPATROL_COUNTRIES: Final[list[str]] = ["world", "united-states"]
FLIXPATROL_DEFAULT_COUNTRY: Final[str] = "world"

# Rate-limiting / politeness
REQUEST_DELAY_MIN: Final[float] = 2.0   # seconds between requests (min)
REQUEST_DELAY_MAX: Final[float] = 3.5   # seconds between requests (max)
REQUEST_TIMEOUT: Final[int] = 30        # seconds per HTTP request
MAX_RETRIES: Final[int] = 3             # retries on transient failures
RETRY_BACKOFF: Final[float] = 5.0       # seconds to wait after a failed attempt

USER_AGENT: Final[str] = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)

REQUEST_HEADERS: Final[dict[str, str]] = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    # Never advertise br/zstd: requests can't decode them without extra
    # packages, and FlixPatrol's Brotli responses arrive as binary garbage
    # (0 entries scraped). gzip is always decodable.
    "Accept-Encoding": "gzip, deflate",
    "Connection": "keep-alive",
}

# ──────────────────────────────────────────────────────────────────────
# Netflix official Top 10 settings
# ──────────────────────────────────────────────────────────────────────
NETFLIX_TOP10_URL: Final[str] = "https://top10.netflix.com"
NETFLIX_CSV_URL: Final[str] = (
    "https://top10.netflix.com/data/all-weeks-countries.tsv"
)

NETFLIX_CSV_COLUMNS: Final[list[str]] = [
    "country_name",
    "country_iso2",
    "week",
    "category",
    "weekly_rank",
    "show_title",
    "season_title",
    "cumulative_weeks_in_top_10",
]

# ──────────────────────────────────────────────────────────────────────
# Categories
# ──────────────────────────────────────────────────────────────────────
CATEGORY_TV: Final[str] = "TV"
CATEGORY_FILMS: Final[str] = "Films"
CATEGORIES: Final[list[str]] = [CATEGORY_TV, CATEGORY_FILMS]

# FlixPatrol page sections use slightly different labels
FLIXPATROL_SECTION_MAP: Final[dict[str, str]] = {
    "TV (English)": CATEGORY_TV,
    "TV (Non-English)": CATEGORY_TV,
    "Films (English)": CATEGORY_FILMS,
    "Films (Non-English)": CATEGORY_FILMS,
    # Fallback single-language labels
    "TV": CATEGORY_TV,
    "Films": CATEGORY_FILMS,
}

# ──────────────────────────────────────────────────────────────────────
# Backtest date range
# ──────────────────────────────────────────────────────────────────────
BACKTEST_WEEKS: Final[int] = 60  # default look-back window

# Netflix weeks run Monday-Sunday; the chart is published on Tuesday.
# We default the end date to the most recent completed Sunday.
_today = date.today()
_days_since_sunday = (_today.weekday() + 1) % 7  # Monday=0 … Sunday=6
BACKTEST_END_DATE: Final[date] = _today - timedelta(days=_days_since_sunday)
BACKTEST_START_DATE: Final[date] = BACKTEST_END_DATE - timedelta(
    weeks=BACKTEST_WEEKS
)

# ──────────────────────────────────────────────────────────────────────
# Kalshi market settings
# ──────────────────────────────────────────────────────────────────────
KALSHI_CONTRACT_SIZE: Final[float] = 1.00   # $1 per contract
KALSHI_MAX_POSITION: Final[int] = 100       # max contracts per market
KALSHI_BANKROLL: Final[float] = 100.00      # starting bankroll ($)
KALSHI_MARKET_CLOSE_DAY: Final[str] = "Monday"   # 11:59 PM ET
KALSHI_RESOLUTION_DAY: Final[str] = "Tuesday"     # Netflix publishes

# ──────────────────────────────────────────────────────────────────────
# Kalshi API (Trade API v2)
# ──────────────────────────────────────────────────────────────────────
# Public market-data endpoints (events, markets, orderbooks) need NO key.
# Authenticated endpoints (balance, positions, orders) activate when both
# KALSHI_API_KEY_ID and KALSHI_PRIVATE_KEY_PATH are set in the environment.
KALSHI_API_BASE: Final[str] = os.environ.get(
    "KALSHI_API_BASE", "https://api.elections.kalshi.com/trade-api/v2"
)
KALSHI_API_KEY_ID: Final[str] = os.environ.get("KALSHI_API_KEY_ID", "")
KALSHI_PRIVATE_KEY_PATH: Final[str] = os.environ.get("KALSHI_PRIVATE_KEY_PATH", "")

# Known/likely series tickers for the Netflix weekly-#1 markets (fast path).
# If none respond, the analyzer scans open events for "netflix" in the title
# and caches whatever series it discovers in data/kalshi_event_cache.json.
KALSHI_NETFLIX_SERIES: Final[dict[str, list[str]]] = {
    CATEGORY_TV: ["KXNETFLIXSHOW", "KXNETFLIXRANKSHOW", "KXTOPNETFLIXSHOW"],
    CATEGORY_FILMS: ["KXNETFLIXMOVIE", "KXNETFLIXRANKMOVIE", "KXTOPNETFLIXMOVIE"],
}

KALSHI_MIN_EDGE: Final[float] = 0.05     # min (our_prob − ask) to place a trade
KALSHI_FEE_RATE: Final[float] = 0.07     # Kalshi taker fee ≈ 0.07·P·(1−P) per contract
KALSHI_TITLE_MATCH_THRESHOLD: Final[float] = 0.60  # fuzzy title-match cutoff
KELLY_FRACTION: Final[float] = 0.25      # quarter-Kelly everywhere — never full Kelly

# ──────────────────────────────────────────────────────────────────────
# Trading calendar — when to spend money and when to record trades
# ──────────────────────────────────────────────────────────────────────
# Weekday ints: Mon=0 … Sun=6. Netflix week = Mon–Sun, market closes
# Mon 11:59 PM ET. The mispricing window is Sunday night → Monday, once
# weekend data exists. Earlier runs are data capture only.
TRADE_DAYS: Final[set[int]] = {6, 0}        # record trades Sun & Mon only
TIKTOK_DAYS: Final[set[int]] = {5, 6, 0}    # spend Apify credit Sat–Mon only

# ──────────────────────────────────────────────────────────────────────
# Confidence calibration from the paper ledger
# ──────────────────────────────────────────────────────────────────────
# Once enough trades have resolved, hand-tuned confidences are shrunk
# toward the empirical win rate of similar past signals.
CALIBRATION_MIN_SAMPLES: Final[int] = 8      # resolved trades needed in the bucket
CALIBRATION_BUCKET_WIDTH: Final[float] = 0.15  # |past_conf − raw_conf| ≤ width
CALIBRATION_PRIOR_WEIGHT: Final[float] = 10.0  # shrinkage: raw conf counts as N pseudo-trades

# ──────────────────────────────────────────────────────────────────────
# TikTok / Apify — Advanced Social Oracle
# ──────────────────────────────────────────────────────────────────────
# Token lives in .env (never in source). The TikTok oracle disables
# itself gracefully when the token is missing.
APIFY_API_TOKEN: Final[str] = os.environ.get("APIFY_API_TOKEN", "")
APIFY_TIKTOK_ACTOR: Final[str] = "clockworks/tiktok-scraper"
TIKTOK_RESULTS_PER_PAGE: Final[int] = 100   # top N recent videos per hashtag
TIKTOK_PROXY_COUNTRY: Final[str] = "US"     # hyper-local US market signal
TIKTOK_CALL_TIMEOUT: Final[int] = 300       # seconds before an actor run is abandoned
TIKTOK_MAX_CONTENDERS: Final[int] = 3       # never query more than Top 3 (API supremacy)

# Weekly-velocity filter: only count views on videos POSTED during the
# current Netflix week (Mon-Sun). Raw hashtag views are cumulative
# all-time and measure fame, not this week's momentum.
TIKTOK_WEEKLY_WINDOW: Final[bool] = True
TIKTOK_MIN_WEEKLY_VIDEOS: Final[int] = 5    # fewer in-window videos → unreliable reading → None

# Alpha Override ("The >2x Rule")
ALPHA_OVERRIDE_MULTIPLIER: Final[float] = 2.0   # trailing contender needs >2x views of #1
ALPHA_OVERRIDE_CONFIDENCE: Final[float] = 0.85  # flat hyper-conviction confidence

# ──────────────────────────────────────────────────────────────────────
# Advanced Oracles — Wikipedia, Google Trends, TMDB, YouTube
# ──────────────────────────────────────────────────────────────────────
# Keyless oracles
WIKI_API_USER_AGENT: Final[str] = os.environ.get(
    "WIKI_API_USER_AGENT", "KalshiMasterAgent/1.0 (research project)"
)
ORACLE_LOOKBACK_DAYS: Final[int] = 7          # window for pageviews / trends / velocity

# Keyed oracles — read from environment; oracle disables itself when empty
TMDB_API_KEY: Final[str] = os.environ.get("TMDB_API_KEY", "")
YOUTUBE_API_KEY: Final[str] = os.environ.get("YOUTUBE_API_KEY", "")

# Ensemble confidence adjustments (bounded, transparent)
ENSEMBLE_DOMINANCE_RATIO: Final[float] = 2.0   # winner needs >2x a rival's magnitude
ENSEMBLE_BOOST: Final[float] = 0.05            # per-oracle boost when winner dominates
ENSEMBLE_PENALTY: Final[float] = 0.05          # per-oracle penalty when a rival dominates winner
FRESH_RELEASE_DAYS: Final[int] = 3             # premiered ≤N days into the week = explosive velocity
CONFIDENCE_FLOOR: Final[float] = 0.05
CONFIDENCE_CEILING: Final[float] = 0.95

# ──────────────────────────────────────────────────────────────────────
# Sample data generation
# ──────────────────────────────────────────────────────────────────────
SAMPLE_NUM_WEEKS: Final[int] = 52
SAMPLE_NUM_TITLES: Final[int] = 18  # realistic pool of shows
WEEKEND_EFFECT_PROBABILITY: Final[float] = 0.65  # P(weekend #1 = weekly winner)
