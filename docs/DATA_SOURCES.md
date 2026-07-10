# Data Sources: Kalshi Netflix Movies Agent

This document details all data sources, oracles, and APIs used by the Kalshi Netflix MOVIES prediction agent. The agent is focused primarily on the **MOVIES** (Films) category.

---

## 1. FlixPatrol (Primary — Daily Rankings)
- **URL pattern**: `https://flixpatrol.com/top10/netflix/{country}/{YYYY-MM-DD}/`
- **Countries**: `world`, `united-states`
- **Provides**: Daily top 10 rankings (ordinal rank + title)
- **Frequency**: Daily
- **Method**: HTML scraping with BeautifulSoup (lxml parser)
- **Code**: `FlixPatrolScraper` class in [`src/data_collector.py`](file:///C:/Users/DSU%20Student/Desktop/kalshiAgent-main/src/data_collector.py) (lines 47-300)
- **Rate limits**: 2-3.5s between requests, max 3 retries, 5s backoff
- **Headers**: Custom User-Agent, Accept-Encoding limited to gzip/deflate (NOT Brotli — causes binary garbage)
- **Risk**: No API contract, page redesign can break it. Agent degrades to no-trade (safe).
- **Note**: FlixPatrol also offers a paid API as a backup. The user will be adding a FlixPatrol API key later for additional data.

## 2. Netflix Official Top 10 (Weekly Resolution)
- **URL**: `https://top10.netflix.com/data/all-weeks-countries.tsv`
- **Provides**: Weekly #1 per country with rank, title, season_title, cumulative_weeks_in_top_10
- **MISSING from current data**: `hours_viewed`, `views` columns (these are in `all-weeks-global.tsv` which is NOT currently downloaded)
- **Frequency**: Updated every Tuesday (covers Mon-Sun)
- **Code**: `NetflixTop10Collector` class in [`src/data_collector.py`](file:///C:/Users/DSU%20Student/Desktop/kalshiAgent-main/src/data_collector.py) (lines 882-1010)
- **Used for**: Resolution ground truth — determining who actually won the week
- **Stored as**: `data/netflix_weekly.csv` (27MB)

## 3. TikTok Oracle (Alpha Override)
- **Provider**: Apify (clockworks/tiktok-scraper actor)
- **Requires**: `APIFY_API_TOKEN` in `.env`
- **Provides**: Hashtag view volume for Netflix movie titles
- **Key feature**: Weekly velocity filter — only counts videos posted during current Netflix week (Mon-Sun), not all-time cumulative views
- **Minimum**: 5 in-window videos for a valid reading
- **Max contenders queried**: 3 (API supremacy — saves cost)
- **Results per page**: 100 videos per hashtag
- **Proxy**: US
- **Timeout**: 300s per actor run
- **Only runs**: Sat-Mon (`TIKTOK_DAYS`) to save Apify credit
- **Cost**: ~$1.70 per 1000 results, ~$3.10/week
- **Code**: `TikTokOracle` class in [`src/data_collector.py`](file:///C:/Users/DSU%20Student/Desktop/kalshiAgent-main/src/data_collector.py) (lines 346-527)
- **Used by**: `AlphaOverrideStrategy` — if trailing contender has >2x views of #1, override prediction

## 4. Wikipedia Pageviews (Ensemble Oracle — Keyless)
- **API**: Wikimedia REST API (pageviews per article)
- **Requires**: No key needed
- **Resolves**: Title to canonical article via MediaWiki search API (with category hint: 'film')
- **Provides**: Sums daily pageviews over last 7 days (`ORACLE_LOOKBACK_DAYS`)
- **Code**: `WikipediaOracle` class in [`src/data_collector.py`](file:///C:/Users/DSU%20Student/Desktop/kalshiAgent-main/src/data_collector.py) (lines 548-627)
- **Fail-safe**: returns None on any failure, in-memory cache

## 5. Google Trends (Ensemble Oracle — Keyless)
- **Library**: `pytrends`
- **Provides**: Relative US search interest (0-100) over last 7 days
- **Method**: All contenders queried together (max 5) for relative comparison
- **Code**: `GoogleTrendsOracle` class in [`src/data_collector.py`](file:///C:/Users/DSU%20Student/Desktop/kalshiAgent-main/src/data_collector.py) (lines 635-680)
- **Known issue**: Rate-limited and flaky
- **Fail-safe**: returns None

## 6. TMDB (Ensemble Oracle — Keyed)
- **API**: The Movie Database API v3
- **Requires**: `TMDB_API_KEY` in `.env` (free non-commercial)
- **Provides for MOVIES**: `runtime_min`, `release_date`, `popularity`, `cast` (top 5), `director`
- **Method**: Always searches as movie (`media_type='movie'`) since we focus on Films
- **Used for**: Release velocity detection (premiered within first 3 days of Netflix week = explosive)
- **Code**: `TMDBOracle` class in [`src/data_collector.py`](file:///C:/Users/DSU%20Student/Desktop/kalshiAgent-main/src/data_collector.py) (lines 688-762)
- **Self-disabling**: Silently disables when no key is configured

## 7. YouTube Data API (Ensemble Oracle — Keyed)
- **API**: YouTube Data API v3
- **Requires**: `YOUTUBE_API_KEY` in `.env` (free 10k units/day)
- **Searches**: "<title> trailer" restricted to `channelId="UCWOA1ZGywLbqmigxE4Qlvuw"` (Official Netflix US Channel)
- **Returns**: `views`, `views_per_day`, `likes`, `comments`, `published_at`, `video_id`, `url`
- **Code**: `YouTubeOracle` class in [`src/data_collector.py`](file:///C:/Users/DSU%20Student/Desktop/kalshiAgent-main/src/data_collector.py) (lines 770-874)
- **Self-disabling**: Silently disables when no key is configured

## 8. Netflix Promo Scout (ADK Agent)
- **API**: Google Agent Development Kit (Gemini 3.1 Pro)
- **Requires**: `google.adk` package
- **Method**: Searches Google and URLs for "Netflix Tudum movies" and weekend release news to gauge marketing push intensity.
- **Provides**: JSON array of heavily promoted movies with `promo_score` (1-100) and `evidence`.
- **Code**: `fetch_promoted_movies()` in [`src/promo_agent.py`](file:///C:/Users/DSU%20Student/Desktop/kalshiAgent-main/src/promo_agent.py)

## 9. Kalshi Trade API v2 (Market Data)
- **Base URL**: `https://api.elections.kalshi.com/trade-api/v2`
- **Public endpoints (no key)**: `events`, `markets`, `orderbook`, `trades`
- **Auth endpoints (RSA-PSS signing)**: `balance`, `positions`
- **Known series tickers for movies**: `KXNETFLIXMOVIE`, `KXNETFLIXRANKMOVIE`, `KXTOPNETFLIXMOVIE`
- **Event discovery**: 3-stage (known series → cache → paginated scan for 'netflix' + 'movie'/'film' in title)
- **Provides**: bid/ask quotes, implied probability, spread, volume, open interest, top 5 bets
- **Code**: `KalshiClient` + `KalshiMarketAnalyzer` in [`src/kalshi_client.py`](file:///C:/Users/DSU%20Student/Desktop/kalshiAgent-main/src/kalshi_client.py) (629 lines)

---

## Summary Table

| Source | Type | Key Req? | Cost | Update Freq | Code Location |
|---|---|---|---|---|---|
| FlixPatrol | HTML Scraping | No | Free (for now) | Daily | `src/data_collector.py` |
| Netflix Top 10 | TSV Download | No | Free | Weekly (Tue) | `src/data_collector.py` |
| TikTok (Apify) | API | Yes | ~$3.10/week | On Demand | `src/data_collector.py` |
| Wikipedia | REST API | No | Free | Daily | `src/data_collector.py` |
| Google Trends | `pytrends` | No | Free | Realtime | `src/data_collector.py` |
| TMDB | REST API | Yes | Free (non-com) | Realtime | `src/data_collector.py` |
| YouTube | REST API | Yes | Free (10k units) | Realtime | `src/data_collector.py` |
| Kalshi API v2 | REST API | Yes (for trades) | Free | Realtime | `src/kalshi_client.py` |

## Oracle Confidence Adjustments

- **TikTok Override**: Overrides baseline signal completely. Sets confidence to `0.85` if trailing contender has >2x views of #1.
- **Wikipedia Ensemble**: Winner >2x all rivals pageviews → `+0.05`; Rival >2x winner → `-0.05`
- **Google Trends Ensemble**: Winner >2x all rivals interest → `+0.05`; Rival >2x winner → `-0.05`
- **TMDB Ensemble**: Premiered within first 3 days of Netflix week and already tops chart → `+0.05` (explosive velocity)
- **YouTube Ensemble**: Winner's trailer views_per_day >2x rivals → `+0.05`; Rival >2x winner → `-0.05`

## Planned Future Enhancements

- **Netflix Global Top 10 TSV** (`all-weeks-global.tsv`): This data source has `hours_viewed` and `views` columns, providing actual metrics rather than just rankings.
- **Netflix Release Calendar**: For tracking Friday movie drops ahead of time.
- **FlixPatrol API Integration**: Utilizing a paid API key for enhanced, non-brittle data collection.
