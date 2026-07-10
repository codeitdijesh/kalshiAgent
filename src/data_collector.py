"""
Data collection module for the Kalshi Netflix Weekend Effect backtester.

Three main classes:
    FlixPatrolScraper   – scrapes daily Netflix Top 10 from FlixPatrol
    NetflixTop10Collector – downloads / parses the official weekly CSV/TSV
    DataMerger          – joins daily & weekly data, engineers weekend features

Plus:
    TikTokOracle        – Apify-backed TikTok hashtag volume collector
    get_tiktok_volume() – fail-safe module-level convenience wrapper
    normalize_title_to_hashtag() – Netflix title → TikTok hashtag cleaner

Plus a standalone ``generate_sample_data()`` function that creates realistic
synthetic data for offline testing.
"""

from __future__ import annotations

import csv
import logging
import random
import re
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

import numpy as np
import pandas as pd
import requests
from bs4 import BeautifulSoup

import config as cfg

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
)

# =====================================================================
# FlixPatrol scraper
# =====================================================================


class FlixPatrolScraper:
    """Scrape daily Netflix Top 10 rankings from FlixPatrol.

    URL pattern
    -----------
    ``https://flixpatrol.com/top10/netflix/{country}/{YYYY-MM-DD}/``

    Each page lists up to 10 titles in separate sections for
    TV (English), TV (Non-English), Films (English), Films (Non-English).
    """

    def __init__(self, session: Optional[requests.Session] = None) -> None:
        self.session = session or requests.Session()
        self.session.headers.update(cfg.REQUEST_HEADERS)

    # -----------------------------------------------------------------
    # Public API
    # -----------------------------------------------------------------

    def scrape_daily_top10(
        self,
        target_date: date,
        country: str = cfg.FLIXPATROL_DEFAULT_COUNTRY,
        category: str = "",
    ) -> list[dict[str, Any]]:
        """Scrape a single day's Top 10 page.

        Parameters
        ----------
        target_date : date
            Calendar date to scrape.
        country : str
            FlixPatrol country slug (e.g. ``'world'``, ``'united-states'``).
        category : str
            Optional filter — ``'TV'``, ``'Films'``, or ``''`` for both.

        Returns
        -------
        list[dict]
            Each dict has keys: ``date``, ``country``, ``category``,
            ``rank``, ``title``.
        """
        url = self._build_url(target_date, country)
        html = self._fetch(url)
        if html is None:
            logger.warning("No HTML returned for %s — %s", target_date, url)
            return []

        rows = self._parse_page(html, target_date, country)

        if category:
            rows = [r for r in rows if r["category"] == category]

        logger.info(
            "Scraped %d entries for %s (%s)", len(rows), target_date, country
        )
        return rows

    def scrape_date_range(
        self,
        start_date: date,
        end_date: date,
        country: str = cfg.FLIXPATROL_DEFAULT_COUNTRY,
    ) -> list[dict[str, Any]]:
        """Scrape every day in *[start_date, end_date]* inclusive.

        Respects ``config.REQUEST_DELAY_MIN / MAX`` between requests.

        Returns
        -------
        list[dict]
            Concatenated daily results.
        """
        all_rows: list[dict[str, Any]] = []
        current = start_date
        total_days = (end_date - start_date).days + 1

        logger.info(
            "Scraping %d days from %s to %s (%s)",
            total_days,
            start_date,
            end_date,
            country,
        )

        day_num = 0
        while current <= end_date:
            day_num += 1
            logger.info("  [%d/%d] %s", day_num, total_days, current)

            rows = self.scrape_daily_top10(current, country=country)
            all_rows.extend(rows)

            # Polite delay (skip after last request)
            if current < end_date:
                delay = random.uniform(cfg.REQUEST_DELAY_MIN, cfg.REQUEST_DELAY_MAX)
                time.sleep(delay)

            current += timedelta(days=1)

        logger.info("Finished scraping — %d total entries", len(all_rows))
        return all_rows

    # -----------------------------------------------------------------
    # Persistence helpers
    # -----------------------------------------------------------------

    @staticmethod
    def save_to_csv(data: list[dict[str, Any]], filename: str | Path) -> Path:
        """Write a list of row-dicts to a CSV file.

        Parameters
        ----------
        data : list[dict]
            Rows to save.
        filename : str | Path
            Destination path.  Relative paths are resolved under
            ``config.RAW_DIR``.

        Returns
        -------
        Path
            Absolute path of the written file.
        """
        filepath = Path(filename)
        if not filepath.is_absolute():
            filepath = cfg.RAW_DIR / filepath
        filepath.parent.mkdir(parents=True, exist_ok=True)

        if not data:
            logger.warning("No data to save — skipping %s", filepath)
            return filepath

        fieldnames = list(data[0].keys())
        with open(filepath, "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(data)

        logger.info("Saved %d rows → %s", len(data), filepath)
        return filepath

    @staticmethod
    def load_from_csv(filename: str | Path) -> pd.DataFrame:
        """Load a previously saved CSV into a DataFrame.

        Relative paths are resolved under ``config.RAW_DIR``.
        """
        filepath = Path(filename)
        if not filepath.is_absolute():
            filepath = cfg.RAW_DIR / filepath

        df = pd.read_csv(filepath, parse_dates=["date"])
        logger.info("Loaded %d rows from %s", len(df), filepath)
        return df

    # -----------------------------------------------------------------
    # Internals
    # -----------------------------------------------------------------

    @staticmethod
    def _build_url(target_date: date, country: str) -> str:
        return f"{cfg.FLIXPATROL_BASE_URL}/{country}/{target_date.isoformat()}/"

    def _fetch(self, url: str) -> Optional[str]:
        """GET *url* with retry logic.  Returns HTML text or ``None``."""
        for attempt in range(1, cfg.MAX_RETRIES + 1):
            try:
                resp = self.session.get(url, timeout=cfg.REQUEST_TIMEOUT)
                resp.raise_for_status()
                return resp.text
            except requests.RequestException as exc:
                logger.warning(
                    "Attempt %d/%d failed for %s: %s",
                    attempt,
                    cfg.MAX_RETRIES,
                    url,
                    exc,
                )
                if attempt < cfg.MAX_RETRIES:
                    time.sleep(cfg.RETRY_BACKOFF * attempt)
        return None

    @staticmethod
    def _parse_page(
        html: str, target_date: date, country: str
    ) -> list[dict[str, Any]]:
        """Extract titles and ranks from a FlixPatrol daily page.

        FlixPatrol organises the page into sections (h3 headings) for
        each category.  Within each section, an ordered table or list
        contains rank + title.  We try multiple CSS selector strategies
        so the scraper is reasonably resilient to minor layout changes.
        """
        soup = BeautifulSoup(html, "lxml")
        results: list[dict[str, Any]] = []

        # Strategy 1: look for known table structure
        # FlixPatrol typically uses <div class="tabular-data-table"> blocks
        # grouped under headings.
        sections = soup.select("div.tabular-data-table, div.scrolling-tablecell")

        if sections:
            current_category = "TV"  # default fallback
            for section in sections:
                # Try to find the nearest preceding heading to determine category
                heading = section.find_previous(["h2", "h3"])
                if heading:
                    heading_text = heading.get_text(strip=True)
                    heading_lower = heading_text.lower()
                    if "movie" in heading_lower or "film" in heading_lower:
                        current_category = "Films"
                    elif "tv" in heading_lower or "show" in heading_lower:
                        current_category = "TV"
                    else:
                        current_category = cfg.FLIXPATROL_SECTION_MAP.get(heading_text, current_category)

                rows = section.select("tr, div.table-row")
                for rank_idx, row in enumerate(rows, start=1):
                    title_el = row.select_one(
                        "td.table-td a, div.table-cell a, a.hover-underline"
                    )
                    if title_el:
                        title = title_el.get_text(strip=True)
                        results.append(
                            {
                                "date": target_date.isoformat(),
                                "country": country,
                                "category": current_category,
                                "rank": rank_idx,
                                "title": title,
                            }
                        )

        # Strategy 2: fallback — look for any numbered list of titles
        if not results:
            for link in soup.select("a[href*='/title/']"):
                title = link.get_text(strip=True)
                if not title:
                    continue
                # Try to infer category from parent section
                parent_heading = link.find_previous(["h2", "h3"])
                cat = "TV"
                if parent_heading:
                    heading_lower = parent_heading.get_text(strip=True).lower()
                    if "movie" in heading_lower or "film" in heading_lower:
                        cat = "Films"
                    elif "tv" in heading_lower or "show" in heading_lower:
                        cat = "TV"
                    else:
                        cat = cfg.FLIXPATROL_SECTION_MAP.get(parent_heading.get_text(strip=True), "TV")
                results.append(
                    {
                        "date": target_date.isoformat(),
                        "country": country,
                        "category": cat,
                        "rank": len(
                            [r for r in results if r["category"] == cat]
                        )
                        + 1,
                        "title": title,
                    }
                )

        return results


# =====================================================================
# TikTok Advanced Social Oracle (Apify)
# =====================================================================


def normalize_title_to_hashtag(title: str) -> str:
    """Convert a raw Netflix title into a standardized TikTok hashtag.

    Rules
    -----
    * Lowercase everything.
    * Strip a leading article ("the ", "a ", "an ").
    * Drop season/part suffixes ("Season 2", ": Part 1", "Limited Series").
    * Remove every non-alphanumeric character (apostrophes, colons,
      ampersands, spaces, accents are transliterated where possible).

    Examples
    --------
    >>> normalize_title_to_hashtag("The Queen's Gambit")
    'queensgambit'
    >>> normalize_title_to_hashtag("Enola Holmes 3")
    'enolaholmes3'
    >>> normalize_title_to_hashtag("Ginny & Georgia: Season 3")
    'ginnygeorgia'
    """
    tag = title.strip().lower()
    # Drop season / part / series suffixes
    tag = re.sub(
        r"[:\-–—]?\s*(season|part|volume|vol\.?|chapter|limited series)\s*\d*\s*$",
        "",
        tag,
    )
    # Drop a leading article
    tag = re.sub(r"^(the|a|an)\s+", "", tag)
    # Strip everything that isn't a letter or digit (apostrophes, colons,
    # ampersands, spaces, punctuation)
    tag = re.sub(r"[^a-z0-9]", "", tag)
    return tag


class TikTokOracle:
    """Fail-safe TikTok hashtag volume collector backed by Apify.

    Uses the ``clockworks/tiktok-scraper`` actor with a US proxy to sum
    play counts across the top recent videos for a hashtag. Every failure
    mode (missing dependency, bad token, timeout, API limit, schema drift)
    degrades to ``None`` — the Master Agent never crashes on a sensory
    failure, it simply falls back to the baseline signal.

    An in-memory cache ensures each hashtag is queried at most once per
    process, respecting API limits.
    """

    def __init__(self, api_token: str = cfg.APIFY_API_TOKEN) -> None:
        self._api_token = api_token
        self._client = None  # lazy — apify_client may not be installed
        self._cache: dict[str, Optional[int]] = {}

    # -----------------------------------------------------------------
    # Public API
    # -----------------------------------------------------------------

    def get_tiktok_volume(self, title: str) -> Optional[int]:
        """Return this week's TikTok view volume for a Netflix title.

        The title is normalized to a hashtag first. When
        ``config.TIKTOK_WEEKLY_WINDOW`` is on (default), only views on
        videos POSTED during the current Netflix week (Mon–Sun) count —
        raw hashtag totals are cumulative all-time and measure fame, not
        momentum. Returns ``None`` on any failure or when too few
        in-window videos exist for a reliable reading.
        """
        tag = normalize_title_to_hashtag(title)
        if not tag:
            logger.warning("TikTokOracle: title %r normalized to empty tag", title)
            return None

        if tag in self._cache:
            logger.info("TikTokOracle: cache hit for #%s", tag)
            return self._cache[tag]

        since_ts: Optional[float] = None
        if cfg.TIKTOK_WEEKLY_WINDOW:
            today = date.today()
            week_monday = today - timedelta(days=today.weekday())
            since_ts = datetime(
                week_monday.year, week_monday.month, week_monday.day,
                tzinfo=timezone.utc,
            ).timestamp()

        views = self._fetch_hashtag_views(tag, since_ts=since_ts)
        self._cache[tag] = views
        return views

    def get_volumes_for_contenders(
        self, titles: list[str], max_contenders: int = cfg.TIKTOK_MAX_CONTENDERS
    ) -> dict[str, Optional[int]]:
        """Query volumes for at most *max_contenders* titles (API supremacy).

        Returns ``{title: views_or_None}`` preserving input order.
        """
        results: dict[str, Optional[int]] = {}
        for title in titles[:max_contenders]:
            results[title] = self.get_tiktok_volume(title)
        return results

    # -----------------------------------------------------------------
    # Internals
    # -----------------------------------------------------------------

    def _get_client(self):
        """Lazily construct the Apify client; return ``None`` if unavailable."""
        if self._client is not None:
            return self._client
        try:
            from apify_client import ApifyClient  # noqa: PLC0415
        except ImportError:
            logger.error(
                "TikTokOracle: apify-client not installed — "
                "run `pip install apify-client`"
            )
            return None
        if not self._api_token:
            logger.error("TikTokOracle: no APIFY_API_TOKEN configured")
            return None
        self._client = ApifyClient(self._api_token)
        return self._client

    def _fetch_hashtag_views(
        self, tag: str, since_ts: Optional[float] = None
    ) -> Optional[int]:
        """Run the Apify actor for one hashtag. Fail-safe: returns ``None``.

        When *since_ts* (unix seconds) is given, only videos created at or
        after that moment are counted, and a minimum in-window video count
        (``config.TIKTOK_MIN_WEEKLY_VIDEOS``) is required for a valid reading.
        """
        client = self._get_client()
        if client is None:
            return None

        run_input = {
            "hashtags": [tag],
            "resultsPerPage": cfg.TIKTOK_RESULTS_PER_PAGE,
            "shouldDownloadVideos": False,
            "proxyCountryCode": cfg.TIKTOK_PROXY_COUNTRY,
        }

        logger.info("TikTokOracle: scraping #%s via Apify (US proxy)…", tag)
        try:
            run = client.actor(cfg.APIFY_TIKTOK_ACTOR).call(
                run_input=run_input
            )
        except Exception as exc:  # noqa: BLE001 — sensory failures must not crash
            logger.error("TikTokOracle: actor call failed for #%s: %s", tag, exc)
            return None

        # Handle different apify-client versions / response shapes
        dataset_id = None
        try:
            dataset_id = run["defaultDatasetId"]
        except (TypeError, KeyError):
            dataset_id = getattr(
                run, "defaultDatasetId", getattr(run, "default_dataset_id", None)
            )
        if not dataset_id:
            logger.error("TikTokOracle: no dataset id in actor run for #%s", tag)
            return None

        total_views = 0
        video_count = 0
        skipped_old = 0
        try:
            for item in client.dataset(dataset_id).iterate_items():
                if since_ts is not None and self._create_ts(item) < since_ts:
                    skipped_old += 1
                    continue
                views = item.get("playCount", 0)
                if not views and isinstance(item.get("stats"), dict):
                    views = item["stats"].get("playCount", 0)
                total_views += int(views or 0)
                video_count += 1
        except Exception as exc:  # noqa: BLE001
            logger.error("TikTokOracle: dataset read failed for #%s: %s", tag, exc)
            return None

        if video_count == 0:
            logger.warning(
                "TikTokOracle: 0 in-window videos for #%s (%d older skipped)",
                tag, skipped_old,
            )
            return None
        if since_ts is not None and video_count < cfg.TIKTOK_MIN_WEEKLY_VIDEOS:
            logger.warning(
                "TikTokOracle: only %d in-window videos for #%s "
                "(< %d minimum) — reading unreliable, returning None",
                video_count, tag, cfg.TIKTOK_MIN_WEEKLY_VIDEOS,
            )
            return None

        logger.info(
            "TikTokOracle: #%s → %d views across %d this-week videos (%d older skipped)",
            tag, total_views, video_count, skipped_old,
        )
        return total_views

    @staticmethod
    def _create_ts(item: dict) -> float:
        """Video creation time as unix seconds; 0.0 when unparseable (= filtered out)."""
        ts = item.get("createTime")
        if ts:
            try:
                return float(ts)
            except (TypeError, ValueError):
                pass
        iso = item.get("createTimeISO")
        if iso:
            try:
                return datetime.fromisoformat(str(iso).replace("Z", "+00:00")).timestamp()
            except ValueError:
                pass
        return 0.0


# Module-level convenience wrapper with a shared oracle instance so casual
# callers get caching + fail-safety without managing an object.
_default_oracle: Optional[TikTokOracle] = None


def get_tiktok_volume(title: str) -> Optional[int]:
    """Fail-safe module-level TikTok volume lookup for a Netflix title."""
    global _default_oracle
    if _default_oracle is None:
        _default_oracle = TikTokOracle()
    return _default_oracle.get_tiktok_volume(title)


# =====================================================================
# Wikipedia Pageviews Oracle (keyless)
# =====================================================================


class WikipediaOracle:
    """Wikipedia pageview magnitude for a Netflix title. Keyless, fail-safe.

    Resolves the title to a canonical article via the MediaWiki search API
    (using the category as a disambiguation hint, e.g. "film" / "TV series"),
    then sums daily pageviews over the last ``config.ORACLE_LOOKBACK_DAYS``
    days from the Wikimedia REST API.
    """

    SEARCH_URL = "https://en.wikipedia.org/w/api.php"
    PAGEVIEWS_URL = (
        "https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/"
        "en.wikipedia/all-access/all-agents/{article}/daily/{start}/{end}"
    )

    def __init__(self, session: Optional[requests.Session] = None) -> None:
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": cfg.WIKI_API_USER_AGENT})
        self._cache: dict[tuple[str, str], Optional[int]] = {}

    def get_pageviews(self, title: str, category: str = "") -> Optional[int]:
        """Total pageviews for *title* over the lookback window, or ``None``."""
        key = (title.lower(), category)
        if key in self._cache:
            return self._cache[key]
        views = self._fetch(title, category)
        self._cache[key] = views
        return views

    # -----------------------------------------------------------------

    def _resolve_article(self, title: str, category: str) -> Optional[str]:
        hint = {"TV": "TV series", "Films": "film"}.get(category, "")
        query = f"{title} {hint}".strip()
        try:
            resp = self.session.get(
                self.SEARCH_URL,
                params={
                    "action": "query",
                    "list": "search",
                    "srsearch": query,
                    "srlimit": 1,
                    "format": "json",
                },
                timeout=cfg.REQUEST_TIMEOUT,
            )
            resp.raise_for_status()
            hits = resp.json().get("query", {}).get("search", [])
        except Exception as exc:  # noqa: BLE001
            logger.error("WikipediaOracle: search failed for %r: %s", title, exc)
            return None
        if not hits:
            logger.warning("WikipediaOracle: no article found for %r", title)
            return None
        return hits[0]["title"].replace(" ", "_")

    def _fetch(self, title: str, category: str) -> Optional[int]:
        article = self._resolve_article(title, category)
        if not article:
            return None
        end = date.today() - timedelta(days=1)  # yesterday (today is incomplete)
        start = end - timedelta(days=cfg.ORACLE_LOOKBACK_DAYS - 1)
        url = self.PAGEVIEWS_URL.format(
            article=requests.utils.quote(article, safe=""),
            start=start.strftime("%Y%m%d"),
            end=end.strftime("%Y%m%d"),
        )
        try:
            resp = self.session.get(url, timeout=cfg.REQUEST_TIMEOUT)
            resp.raise_for_status()
            items = resp.json().get("items", [])
        except Exception as exc:  # noqa: BLE001
            logger.error("WikipediaOracle: pageviews failed for %r: %s", article, exc)
            return None
        total = sum(int(i.get("views", 0)) for i in items)
        logger.info(
            "WikipediaOracle: %r → article %r → %d views (%s–%s)",
            title, article, total, start, end,
        )
        return total


# =====================================================================
# Google Trends Oracle (keyless, via pytrends)
# =====================================================================


class GoogleTrendsOracle:
    """Relative US search-interest magnitude via Google Trends. Fail-safe.

    Google Trends scores are relative (0–100) *within one request*, so all
    contenders must be queried together — ``get_interest`` takes the full
    list (max 5 terms) and returns a mean-interest score per title.
    """

    def __init__(self) -> None:
        self._pytrends = None

    def get_interest(self, titles: list[str]) -> Optional[dict[str, float]]:
        """Mean 7-day US search interest per title, or ``None`` on failure."""
        titles = [t for t in titles if t][:5]
        if len(titles) < 2:
            return None
        client = self._get_client()
        if client is None:
            return None
        try:
            client.build_payload(kw_list=titles, timeframe="now 7-d", geo="US")
            df = client.interest_over_time()
        except Exception as exc:  # noqa: BLE001
            logger.error("GoogleTrendsOracle: query failed for %s: %s", titles, exc)
            return None
        if df is None or df.empty:
            logger.warning("GoogleTrendsOracle: empty response for %s", titles)
            return None
        result = {t: float(df[t].mean()) for t in titles if t in df.columns}
        logger.info("GoogleTrendsOracle: %s", {k: round(v, 1) for k, v in result.items()})
        return result or None

    def _get_client(self):
        if self._pytrends is not None:
            return self._pytrends
        try:
            from pytrends.request import TrendReq  # noqa: PLC0415
        except ImportError:
            logger.error("GoogleTrendsOracle: pytrends not installed — `pip install pytrends`")
            return None
        try:
            self._pytrends = TrendReq(hl="en-US", tz=300)
        except Exception as exc:  # noqa: BLE001
            logger.error("GoogleTrendsOracle: client init failed: %s", exc)
            return None
        return self._pytrends


# =====================================================================
# TMDB Oracle — runtime / bingeability + release-day velocity (keyed)
# =====================================================================


class TMDBOracle:
    """Title metadata from The Movie Database. Requires ``TMDB_API_KEY``.

    Returns runtime (bingeability), release / first-air date (days-available
    velocity), and TMDB popularity. Disables itself silently when no key is
    configured — the ensemble simply skips its adjustment.
    """

    BASE_URL = "https://api.themoviedb.org/3"

    def __init__(
        self,
        api_key: str = cfg.TMDB_API_KEY,
        session: Optional[requests.Session] = None,
    ) -> None:
        self._api_key = api_key
        self.session = session or requests.Session()
        self._cache: dict[tuple[str, str], Optional[dict[str, Any]]] = {}

    @property
    def enabled(self) -> bool:
        return bool(self._api_key)

    def get_metadata(self, title: str, category: str = "") -> Optional[dict[str, Any]]:
        """Return ``{runtime_min, release_date, popularity, media_type}`` or ``None``."""
        if not self.enabled:
            return None
        key = (title.lower(), category)
        if key in self._cache:
            return self._cache[key]
        meta = self._fetch(title, category)
        self._cache[key] = meta
        return meta

    # -----------------------------------------------------------------

    def _get(self, path: str, **params: Any) -> Optional[dict]:
        params["api_key"] = self._api_key
        try:
            resp = self.session.get(
                f"{self.BASE_URL}{path}", params=params, timeout=cfg.REQUEST_TIMEOUT
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:  # noqa: BLE001
            logger.error("TMDBOracle: request %s failed: %s", path, exc)
            return None

    def _fetch(self, title: str, category: str = "") -> Optional[dict[str, Any]]:
        media = "movie"
        search = self._get(f"/search/movie", query=title)
        if not search or not search.get("results"):
            logger.warning("TMDBOracle: no movie match for %r", title)
            return None
        hit = search["results"][0]
        details = self._get(f"/movie/{hit['id']}", append_to_response="credits") or {}

        runtime = details.get("runtime")
        release = details.get("release_date") or hit.get("release_date")

        # Extract cast and director
        credits = details.get("credits", {})
        cast = [c.get("name") for c in credits.get("cast", [])[:5] if c.get("name")]
        director = [c.get("name") for c in credits.get("crew", []) if c.get("job") == "Director"]

        meta = {
            "runtime_min": runtime,
            "release_date": release,
            "popularity": hit.get("popularity"),
            "media_type": media,
            "cast": cast,
            "director": director,
        }
        logger.info("TMDBOracle: %r → %s", title, meta)
        return meta


# =====================================================================
# YouTube Oracle — trailer view velocity (keyed)
# =====================================================================


class YouTubeOracle:
    """Official-trailer view velocity via the YouTube Data API v3.

    Requires ``YOUTUBE_API_KEY``; disables itself silently when absent.
    Searches "<title> official trailer", takes the most-viewed of the top
    results, and computes views-per-day since publication.
    """

    SEARCH_URL = "https://www.googleapis.com/youtube/v3/search"
    VIDEOS_URL = "https://www.googleapis.com/youtube/v3/videos"

    def __init__(
        self,
        api_key: str = cfg.YOUTUBE_API_KEY,
        session: Optional[requests.Session] = None,
    ) -> None:
        self._api_key = api_key
        self.session = session or requests.Session()
        self._cache: dict[str, Optional[dict[str, Any]]] = {}

    @property
    def enabled(self) -> bool:
        return bool(self._api_key)

    def get_trailer_velocity(self, title: str) -> Optional[dict[str, Any]]:
        """Return ``{views, views_per_day, published_at, video_id}`` or ``None``."""
        if not self.enabled:
            return None
        key = title.lower()
        if key in self._cache:
            return self._cache[key]
        stats = self._fetch(title)
        self._cache[key] = stats
        return stats

    # -----------------------------------------------------------------

    def _fetch(self, title: str) -> Optional[dict[str, Any]]:
        try:
            resp = self.session.get(
                self.SEARCH_URL,
                params={
                    "key": self._api_key,
                    "q": f"{title} trailer",
                    "channelId": "UCWOA1ZGywLbqmigxE4Qlvuw",  # Restrict to Official Netflix US Channel
                    "part": "snippet",
                    "type": "video",
                    "maxResults": 3,
                    "regionCode": "US",
                },
                timeout=cfg.REQUEST_TIMEOUT,
            )
            resp.raise_for_status()
            items = resp.json().get("items", [])
        except Exception as exc:  # noqa: BLE001
            logger.error("YouTubeOracle: search failed for %r: %s", title, exc)
            return None
        video_ids = [i["id"]["videoId"] for i in items if i.get("id", {}).get("videoId")]
        if not video_ids:
            logger.warning("YouTubeOracle: no official Netflix trailer found for %r", title)
            return None

        try:
            resp = self.session.get(
                self.VIDEOS_URL,
                params={
                    "key": self._api_key,
                    "id": ",".join(video_ids),
                    "part": "statistics,snippet",
                },
                timeout=cfg.REQUEST_TIMEOUT,
            )
            resp.raise_for_status()
            videos = resp.json().get("items", [])
        except Exception as exc:  # noqa: BLE001
            logger.error("YouTubeOracle: stats failed for %r: %s", title, exc)
            return None
        if not videos:
            return None

        best = max(
            videos, key=lambda v: int(v.get("statistics", {}).get("viewCount", 0))
        )
        stats_data = best.get("statistics", {})
        views = int(stats_data.get("viewCount", 0))
        likes = int(stats_data.get("likeCount", 0))
        comments = int(stats_data.get("commentCount", 0))
        
        published = best.get("snippet", {}).get("publishedAt", "")
        try:
            pub_date = datetime.fromisoformat(published.replace("Z", "+00:00")).date()
            days_live = max((date.today() - pub_date).days, 1)
        except ValueError:
            days_live = None

        stats = {
            "views": views,
            "likes": likes,
            "comments": comments,
            "views_per_day": round(views / days_live, 1) if days_live else None,
            "published_at": published,
            "video_id": best.get("id"),
            "url": f"https://www.youtube.com/watch?v={best.get('id')}",
        }
        logger.info("YouTubeOracle: %r → %s", title, stats)
        return stats


# =====================================================================
# Netflix official Top 10 collector
# =====================================================================


class NetflixTop10Collector:
    """Download and parse the official Netflix Top 10 weekly data.

    Netflix publishes a TSV at ``top10.netflix.com/data/all-weeks-countries.tsv``
    with columns defined in ``config.NETFLIX_CSV_COLUMNS``.
    """

    def __init__(self, session: Optional[requests.Session] = None) -> None:
        self.session = session or requests.Session()
        self.session.headers.update(cfg.REQUEST_HEADERS)

    # -----------------------------------------------------------------
    # Download
    # -----------------------------------------------------------------

    def download_weekly_csv(
        self, dest: Optional[Path] = None
    ) -> Path:
        """Download the official Netflix Top 10 TSV.

        Parameters
        ----------
        dest : Path, optional
            Where to save.  Defaults to ``config.RAW_DIR / 'netflix_top10.tsv'``.

        Returns
        -------
        Path
            Absolute path of the saved file.
        """
        dest = dest or (cfg.RAW_DIR / "netflix_top10.tsv")
        dest.parent.mkdir(parents=True, exist_ok=True)

        logger.info("Downloading Netflix Top 10 from %s", cfg.NETFLIX_CSV_URL)
        try:
            resp = self.session.get(
                cfg.NETFLIX_CSV_URL, timeout=cfg.REQUEST_TIMEOUT
            )
            resp.raise_for_status()
        except requests.RequestException as exc:
            logger.error("Failed to download Netflix Top 10: %s", exc)
            raise

        dest.write_bytes(resp.content)
        logger.info("Saved Netflix Top 10 → %s (%d bytes)", dest, len(resp.content))
        return dest

    # -----------------------------------------------------------------
    # Load / clean
    # -----------------------------------------------------------------

    @staticmethod
    def load_weekly_data(filepath: str | Path) -> pd.DataFrame:
        """Load and clean the official Netflix Top 10 TSV / CSV.

        Returns
        -------
        pd.DataFrame
            Cleaned DataFrame with parsed ``week`` column as ``datetime``.
        """
        filepath = Path(filepath)
        sep = "\t" if filepath.suffix.lower() == ".tsv" else ","

        df = pd.read_csv(filepath, sep=sep, encoding="utf-8")

        # Normalise column names
        df.columns = [c.strip().lower().replace(" ", "_") for c in df.columns]

        # Parse the week column (format: YYYY-MM-DD)
        if "week" in df.columns:
            df["week"] = pd.to_datetime(df["week"], errors="coerce")

        # Ensure rank is numeric
        if "weekly_rank" in df.columns:
            df["weekly_rank"] = pd.to_numeric(
                df["weekly_rank"], errors="coerce"
            )

        logger.info(
            "Loaded %d rows × %d cols from %s", len(df), len(df.columns), filepath
        )
        return df

    # -----------------------------------------------------------------
    # Filtering helpers
    # -----------------------------------------------------------------

    @staticmethod
    def filter_us_data(df: pd.DataFrame) -> pd.DataFrame:
        """Return only US rows.

        Checks both ``country_iso2 == 'US'`` and
        ``country_name == 'United States'`` for robustness.
        """
        mask = pd.Series(False, index=df.index)
        if "country_iso2" in df.columns:
            mask |= df["country_iso2"].str.upper() == "US"
        if "country_name" in df.columns:
            mask |= df["country_name"].str.lower() == "united states"
        filtered = df.loc[mask].copy()
        logger.info("Filtered to %d US rows (from %d)", len(filtered), len(df))
        return filtered

    @staticmethod
    def get_weekly_number_one(
        df: pd.DataFrame, category: str = cfg.CATEGORY_TV
    ) -> pd.DataFrame:
        """Return the #1 title per week for the given category.

        Parameters
        ----------
        df : pd.DataFrame
            Must contain ``week``, ``category``, ``weekly_rank``,
            and ``show_title`` columns.
        category : str
            ``'TV'`` or ``'Films'``.

        Returns
        -------
        pd.DataFrame
            One row per week with columns ``week`` and ``show_title``.
        """
        cat_df = df.loc[df["category"].str.contains(category, case=False, na=False)]
        ones = cat_df.loc[cat_df["weekly_rank"] == 1, ["week", "show_title"]].copy()
        ones = ones.drop_duplicates(subset="week").sort_values("week").reset_index(drop=True)
        logger.info(
            "Found %d weeks with a #1 %s title", len(ones), category
        )
        return ones


# =====================================================================
# Data merger + feature engineering
# =====================================================================


class DataMerger:
    """Merge FlixPatrol daily data with Netflix weekly outcomes and
    engineer weekend-effect features for the backtest.

    Netflix weeks run **Monday → Sunday**.  The official chart is
    published on Tuesday reflecting the prior Mon-Sun period.
    """

    @staticmethod
    def merge_daily_and_weekly(
        daily_df: pd.DataFrame, weekly_df: pd.DataFrame
    ) -> pd.DataFrame:
        """Align FlixPatrol daily rankings with Netflix weekly outcomes.

        Parameters
        ----------
        daily_df : pd.DataFrame
            FlixPatrol daily data with columns ``date``, ``category``,
            ``rank``, ``title``.
        weekly_df : pd.DataFrame
            Netflix official data with columns ``week``, ``category``,
            ``weekly_rank``, ``show_title``.

        Returns
        -------
        pd.DataFrame
            Combined DataFrame keyed on (``netflix_week``, ``title``).
        """
        daily = daily_df.copy()
        weekly = weekly_df.copy()

        # Ensure date types
        daily["date"] = pd.to_datetime(daily["date"])
        weekly["week"] = pd.to_datetime(weekly["week"])

        # Compute the Netflix week (Monday) each daily date belongs to.
        # Python weekday: Monday=0, Sunday=6.
        daily["day_of_week"] = daily["date"].dt.dayofweek
        daily["netflix_week"] = daily["date"] - pd.to_timedelta(
            daily["day_of_week"], unit="D"
        )

        # Normalise title strings for joining
        daily["title_norm"] = daily["title"].str.strip().str.lower()
        weekly["title_norm"] = weekly["show_title"].str.strip().str.lower()

        # Find weekly winner per week+category
        winners = (
            weekly.loc[weekly["weekly_rank"] == 1]
            .rename(columns={"week": "netflix_week"})
            [["netflix_week", "category", "title_norm"]]
            .drop_duplicates()
        )
        winners["is_weekly_winner"] = True

        # Merge winner flag back onto daily data
        merged = daily.merge(
            winners,
            on=["netflix_week", "category", "title_norm"],
            how="left",
        )
        merged["is_weekly_winner"] = merged["is_weekly_winner"].fillna(False)

        logger.info(
            "Merged daily (%d rows) + weekly (%d rows) → %d rows",
            len(daily),
            len(weekly),
            len(merged),
        )
        return merged

    @staticmethod
    def compute_weekend_features(merged_df: pd.DataFrame) -> pd.DataFrame:
        """Compute weekend-effect features per (week, title, category).

        Adds columns:
            weekend_rank_avg        – mean rank on Sat + Sun (lower = better)
            weekday_rank_avg        – mean rank Mon–Fri
            weekend_dominance_score – weekday_avg / weekend_avg (>1 ⇒ better on weekends)
            days_at_number_one      – days the title held rank 1 (out of 7)
            weekend_days_at_one     – Sat/Sun days at rank 1 (0, 1, or 2)
            is_weekly_winner        – did this title win the official chart?

        Returns
        -------
        pd.DataFrame
            One row per (netflix_week, title, category).
        """
        df = merged_df.copy()
        df["date"] = pd.to_datetime(df["date"])
        df["day_of_week"] = df["date"].dt.dayofweek  # Mon=0 … Sun=6

        df["is_weekend"] = df["day_of_week"].isin([5, 6])  # Sat=5, Sun=6
        df["is_number_one"] = df["rank"] == 1

        group_keys = ["netflix_week", "title", "category"]

        # Weekend stats
        weekend = df.loc[df["is_weekend"]].groupby(group_keys).agg(
            weekend_rank_avg=("rank", "mean"),
            weekend_days_at_one=("is_number_one", "sum"),
        )

        # Weekday stats
        weekday = df.loc[~df["is_weekend"]].groupby(group_keys).agg(
            weekday_rank_avg=("rank", "mean"),
        )

        # Overall stats
        overall = df.groupby(group_keys).agg(
            days_at_number_one=("is_number_one", "sum"),
            is_weekly_winner=("is_weekly_winner", "max"),
            total_days_seen=("date", "nunique"),
        )

        features = (
            overall.join(weekend, how="left")
            .join(weekday, how="left")
            .reset_index()
        )

        # Dominance score: weekday_avg / weekend_avg (>1 means stronger on weekends)
        features["weekend_dominance_score"] = (
            features["weekday_rank_avg"] / features["weekend_rank_avg"]
        )
        # If weekend_rank_avg is NaN (title not seen on weekends), score = 0
        features["weekend_dominance_score"] = features[
            "weekend_dominance_score"
        ].fillna(0.0)

        # Cast booleans
        features["is_weekly_winner"] = features["is_weekly_winner"].astype(bool)
        features["weekend_days_at_one"] = (
            features["weekend_days_at_one"].fillna(0).astype(int)
        )
        features["days_at_number_one"] = features["days_at_number_one"].astype(int)

        logger.info(
            "Computed weekend features → %d title-week rows", len(features)
        )
        return features


# =====================================================================
# Sample / synthetic data generator
# =====================================================================

# Realistic pool of show titles for sample data
_SAMPLE_TV_TITLES: list[str] = [
    "Stranger Things",
    "Wednesday",
    "Squid Game",
    "The Night Agent",
    "Ginny & Georgia",
    "You",
    "Outer Banks",
    "Love Is Blind",
    "The Diplomat",
    "Queen Charlotte",
    "Black Mirror",
    "Bridgerton",
    "The Watcher",
    "XO, Kitty",
    "Manifest",
    "All the Light We Cannot See",
    "Berlin",
    "Lupin",
]

_SAMPLE_FILM_TITLES: list[str] = [
    "Glass Onion",
    "Luther: The Fallen Sun",
    "Extraction 2",
    "Heart of Stone",
    "The Mother",
    "Murder Mystery 2",
    "Leave the World Behind",
    "Rebel Moon",
    "Lift",
    "Society of the Snow",
    "The Adam Project",
    "Red Notice",
    "Pain Hustlers",
    "Damsel",
    "Spaceman",
    "Irish Wish",
    "Chicken Run: Dawn of the Nugget",
    "Old Dads",
]


def generate_sample_data(
    num_weeks: int = cfg.SAMPLE_NUM_WEEKS,
    weekend_effect_prob: float = cfg.WEEKEND_EFFECT_PROBABILITY,
    seed: int = 42,
    save: bool = True,
) -> dict[str, pd.DataFrame]:
    """Generate realistic synthetic daily + weekly data.

    Simulates the weekend effect: with probability ``weekend_effect_prob``
    the show that is #1 on Saturday & Sunday also ends up as the official
    weekly #1.

    Parameters
    ----------
    num_weeks : int
        Number of weeks of data to generate.
    weekend_effect_prob : float
        Probability that the weekend dominant show wins the week.
    seed : int
        Random seed for reproducibility.
    save : bool
        If ``True``, write CSVs to ``config.SAMPLE_DIR``.

    Returns
    -------
    dict[str, pd.DataFrame]
        ``{"daily": …, "weekly": …, "features": …}``
    """
    rng = np.random.default_rng(seed)
    random.seed(seed)

    # Pick a start Monday
    base_monday = date(2024, 1, 1)  # a known Monday
    # Shift to the nearest Monday if not already
    while base_monday.weekday() != 0:
        base_monday += timedelta(days=1)

    daily_rows: list[dict[str, Any]] = []
    weekly_rows: list[dict[str, Any]] = []

    for week_idx in range(num_weeks):
        week_start = base_monday + timedelta(weeks=week_idx)

        for cat, title_pool in [
            (cfg.CATEGORY_TV, _SAMPLE_TV_TITLES),
            (cfg.CATEGORY_FILMS, _SAMPLE_FILM_TITLES),
        ]:
            # Pick 10 active titles for this week
            active = list(rng.choice(title_pool, size=min(10, len(title_pool)), replace=False))

            # Decide the weekend #1 and the weekly winner
            weekend_champion_idx = 0  # index into `active`
            if rng.random() < weekend_effect_prob:
                # Weekend effect holds: weekend #1 wins the week
                weekly_winner_idx = weekend_champion_idx
            else:
                # Weekend effect fails: a different show wins
                weekly_winner_idx = int(rng.integers(1, len(active)))

            # Generate daily rankings for 7 days (Mon=0 … Sun=6)
            for day_offset in range(7):
                current_date = week_start + timedelta(days=day_offset)
                day_of_week = current_date.weekday()
                is_weekend = day_of_week in (5, 6)

                # Build ranking for the day
                # On weekends, the weekend champion is always #1
                # On weekdays, the weekly winner tends to be #1 (with noise)
                ranking = list(range(len(active)))

                if is_weekend:
                    # Weekend champion at #1
                    ranking.remove(weekend_champion_idx)
                    ranking = [weekend_champion_idx] + ranking
                else:
                    # On weekdays, weekly winner is #1 ~60% of the time
                    if rng.random() < 0.6:
                        ranking.remove(weekly_winner_idx)
                        ranking = [weekly_winner_idx] + ranking
                    else:
                        rng.shuffle(ranking)

                # Add some noise to ranks 2-10
                rest = list(ranking[1:])
                rng.shuffle(rest)
                ranking = [ranking[0]] + rest

                for rank_pos, title_idx in enumerate(ranking):
                    daily_rows.append(
                        {
                            "date": current_date.isoformat(),
                            "country": "world",
                            "category": cat,
                            "rank": rank_pos + 1,
                            "title": active[title_idx],
                        }
                    )

            # Weekly outcome
            weekly_rows.append(
                {
                    "country_name": "United States",
                    "country_iso2": "US",
                    "week": week_start.isoformat(),
                    "category": f"{cat} (English)",
                    "weekly_rank": 1,
                    "show_title": active[weekly_winner_idx],
                    "season_title": f"{active[weekly_winner_idx]}: Season 1",
                    "cumulative_weeks_in_top_10": int(
                        rng.integers(1, 12)
                    ),
                }
            )

            # Fill in ranks 2-10 for weekly data
            other_indices = [i for i in range(len(active)) if i != weekly_winner_idx]
            rng.shuffle(other_indices)
            for weekly_rank, idx in enumerate(other_indices, start=2):
                weekly_rows.append(
                    {
                        "country_name": "United States",
                        "country_iso2": "US",
                        "week": week_start.isoformat(),
                        "category": f"{cat} (English)",
                        "weekly_rank": weekly_rank,
                        "show_title": active[idx],
                        "season_title": f"{active[idx]}: Season 1",
                        "cumulative_weeks_in_top_10": int(
                            rng.integers(1, 8)
                        ),
                    }
                )

    daily_df = pd.DataFrame(daily_rows)
    weekly_df = pd.DataFrame(weekly_rows)

    # Compute features via DataMerger
    merger = DataMerger()
    merged = merger.merge_daily_and_weekly(daily_df, weekly_df)
    features_df = merger.compute_weekend_features(merged)

    if save:
        daily_path = cfg.SAMPLE_DIR / "sample_daily.csv"
        weekly_path = cfg.SAMPLE_DIR / "sample_weekly.csv"
        features_path = cfg.SAMPLE_DIR / "sample_features.csv"

        daily_df.to_csv(daily_path, index=False)
        weekly_df.to_csv(weekly_path, index=False)
        features_df.to_csv(features_path, index=False)

        logger.info("Sample data saved to %s", cfg.SAMPLE_DIR)
        logger.info("  daily  : %d rows → %s", len(daily_df), daily_path)
        logger.info("  weekly : %d rows → %s", len(weekly_df), weekly_path)
        logger.info("  features: %d rows → %s", len(features_df), features_path)

    return {"daily": daily_df, "weekly": weekly_df, "features": features_df}



# =====================================================================
# CLI convenience
# =====================================================================

if __name__ == "__main__":
    from rich.console import Console
    from rich.table import Table

    console = Console()
    console.rule("[bold green]Generating sample data")

    result = generate_sample_data(num_weeks=52, save=True)

    for name, df in result.items():
        console.print(f"\n[bold]{name}[/bold]: {df.shape[0]} rows × {df.shape[1]} cols")
        tbl = Table(show_lines=True, title=name)
        for col in df.columns:
            tbl.add_column(col)
        for _, row in df.head(8).iterrows():
            tbl.add_row(*[str(v) for v in row])
        console.print(tbl)

    console.rule("[bold green]Done")
