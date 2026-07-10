# Kalshi Master Prediction Agent — Handover Document

*Last updated: 2026-07-09. This is the single source of truth for continuing the project on any machine. Every significant decision is recorded in the Decision Log (§8) — keep it updated.*

---

## 1. What This Project Is

An autonomous prediction agent for **Kalshi Netflix Top-10 markets** ("Will [show] be the #1 Netflix show/film this week?"). The core edge is the **Weekend Effect**: the official Netflix weekly chart (published Tuesdays, covering Mon–Sun) is dominated by weekend viewing hours, while the market anchors on the mid-week FlixPatrol leader. On top of that baseline sit several "oracles" that refine or override the signal.

**Current status: Phase 2 — paper trading.** The full live pipeline works end-to-end (scrape → signal → live Kalshi quotes → Kelly-sized paper trade → dashboard). No real money is being traded yet; the paper ledger in `data/paper_trades.json` is the track record that decides whether to go live.

### The pipeline (`master_agent.py run`)

1. **SENSE** — scrape this week's daily FlixPatrol US Top 10 (`FlixPatrolScraper`).
2. **THINK** — baseline Weekend Effect signal (`WeekendEffectStrategy`).
3. **OVERRIDE** — TikTok **this-week** hashtag view volume via Apify for the Top 3 contenders (only videos posted during the current Netflix week count); if a trailing contender has **>2× the weekly views** of #1, override the prediction at 0.85 confidence (`AlphaOverrideStrategy`). Only runs Sat–Mon (`config.TIKTOK_DAYS`) to save Apify credit.
4. **REFINE** — Oracle Ensemble (Wikipedia pageviews, Google Trends, TMDB release velocity, YouTube trailer velocity) applies bounded ±0.05 confidence nudges, then **ledger calibration** shrinks the confidence toward the empirical win rate of similar past signals once ≥ 8 resolved trades exist in the bucket.
5. **ANALYZE** — pull live Kalshi bid/ask (`kalshi_client.py`) and evaluate **both sides**: YES on our predicted winner at the ask, and NO on the market favorite (cost = 1 − yes_bid) when the favorite isn't our pick. Edge, fee (7%·P·(1−P)), EV, and quarter-Kelly sizing are computed per side; the higher-EV tradeable side wins (`best_side`).
6. **ACT** — record a quarter-Kelly-sized paper trade only on decision days (Sun/Mon, `config.TRADE_DAYS`) when the best side clears edge ≥ 5% with EV > 0; other days are CAPTURE_ONLY.
7. **SNAPSHOT** — write `data/latest_cycle.json` and regenerate `output/dashboard.html`.

### File map

| File | Role |
|---|---|
| `master_agent.py` | ★ Live entry point — orchestrates the whole cycle |
| `config.py` | Every tuneable parameter; loads API keys from `.env` |
| `data_collector.py` | FlixPatrol scraper + ALL oracles (TikTok, Wikipedia, Trends, TMDB, YouTube) |
| `strategy.py` | WeekendEffectStrategy, AlphaOverrideStrategy (>2× rule), OracleEnsemble |
| `kalshi_client.py` | Kalshi Trade API v2 — event discovery, quotes, edge/EV, optional auth |
| `paper_trader.py` | JSON ledger, Kelly sizing, win/loss resolution |
| `run_backtest.py` | Phase-1 backtest CLI (`sample`/`backtest`/`analyze`/`paper`) |
| `backtester.py`, `analyzer.py`, `visualizer.py` | Backtest engine, stats/Monte-Carlo, plots |
| `dashboard.py` | Self-contained HTML dashboard from `latest_cycle.json` |
| `data/paper_trades.json` | **The track record — do not lose this file** |
| `.github/workflows/daily_agent.yml` | Scheduled daily run (see §5) |

---

## 2. Setup on a New Laptop

```bash
git clone <your-repo-url> && cd kalshiAgent
python -m venv .venv
.venv\Scripts\activate            # Windows   (source .venv/bin/activate on mac/linux)
pip install -r requirements.txt
copy .env.example .env            # then fill in keys (next section)
python master_agent.py status     # smoke test — prints ledger summary
python master_agent.py markets    # smoke test — live Kalshi quotes (no key needed)
```

If the repo isn't on git yet: copy the whole folder, **including `.env` and `data/paper_trades.json`** (both are git-ignored, so a bare `git clone` will NOT carry them — move them by hand or a password manager).

### 2.1 API keys (`.env`)

| Variable | Needed for | Where to get it | Cost |
|---|---|---|---|
| `APIFY_API_TOKEN` | TikTok oracle / Alpha Override | console.apify.com → Settings → Integrations | Free $5/mo credit, then paid |
| `KALSHI_API_KEY_ID` + `KALSHI_PRIVATE_KEY_PATH` | Live balance/orders only (quotes are keyless) | kalshi.com → Account → API (download the `.pem`) | Free |
| `TMDB_API_KEY` | Runtime/release-date oracle (optional) | themoviedb.org → Settings → API | Free (non-commercial) |
| `YOUTUBE_API_KEY` | Trailer-velocity oracle (optional) | Google Cloud Console → YouTube Data API v3 | Free (10k units/day) |

Wikipedia and Google Trends oracles are keyless. Missing keys never crash the agent — each oracle disables itself.

> ⚠️ **Rotate the Apify token.** The current token was committed to source in earlier versions of this repo. Anyone with old copies has it. Generate a fresh token at the Apify console and update `.env`.

---

## 3. Weekly Operating Rhythm

The trading calendar is **enforced in code** (`config.TRADE_DAYS` / `TIKTOK_DAYS`) — the daily automated run does the right thing by itself each day. `--force` bypasses all gates for manual experiments.

| Day | What the daily run does automatically | Manual action |
|---|---|---|
| Tue–Fri | CAPTURE_ONLY: scrape + baseline signal + snapshot; no Apify spend, no trade | — |
| Saturday | + TikTok oracle warms up (Apify spend starts) | — |
| **Sunday / Monday** | ★ Decision days: full pipeline, both market sides analyzed, trade recorded if the best side clears edge ≥ 5% with EV > 0 (market closes Mon 11:59 PM ET) | Review `output/dashboard.html` |
| **Tuesday** | Netflix publishes the official chart | Settle: `python master_agent.py resolve "<actual winner>"` |
| Any time | — | `python master_agent.py status` · `python master_agent.py markets` |

---

## 4. Weekly Cost Estimate (verified July 2026 pricing)

The only real cost is **Apify** (TikTok). `clockworks/tiktok-scraper` is pay-per-result at **~$1.70 per 1,000 results**; each agent run scrapes up to 3 hashtags × 100 videos × 2 categories = 600 results.

| Service | Usage pattern | Weekly cost |
|---|---|---|
| **Apify / TikTok** — TikTok runs Sat–Mon only (**enforced in code** via `TIKTOK_DAYS`) | 3 days × 600 = 1,800 results | **~$3.10/wk** (≈$13/mo → free $5 credit covers ~⅓; Starter $29/mo has huge headroom) |
| FlixPatrol | Free page scraping (current approach; ToS/blocking risk) | $0 — optional API plan is $9.99/mo (≈$2.30/wk) if scraping breaks |
| TMDB | Free non-commercial tier | $0 |
| YouTube Data API | ~1,000 of 10,000 free daily units | $0 |
| Wikipedia pageviews / Google Trends | Keyless, free | $0 |
| Kalshi API | Free; fees only on real trades: 7%·P·(1−P) per contract (max 1.75¢) | $0 while paper trading |
| Hosting (GitHub Actions) | ~150 of 2,000 free min/mo | $0 |
| **Total — lean setup** | | **≈ $3–4 / week (~$13/mo)** |
| **Total — max setup** (daily TikTok + FlixPatrol API + VPS) | | ≈ $11–12 / week (~$48/mo) |

Cost control already built in: `TIKTOK_MAX_CONTENDERS = 3` and `TIKTOK_RESULTS_PER_PAGE = 100` in `config.py`. Dropping to 50 results/page halves the Apify bill with little signal loss.

---

## 5. Hosting — Running It Live Every Day

**Recommended: GitHub Actions (already set up, $0).** `.github/workflows/daily_agent.yml` runs both categories daily at 21:00 UTC and **commits the captured data back to the repo** — so every day's rankings, signals, and ledger updates are permanently archived even when your laptop is off.

To activate:
1. Push this repo to GitHub (private repo is fine — free tier gives 2,000 min/mo; this uses ~150).
2. Repo → Settings → Secrets and variables → Actions → add `APIFY_API_TOKEN` (and optionally `TMDB_API_KEY`, `YOUTUBE_API_KEY`).
3. Actions tab → enable workflows → optionally trigger "Run workflow" manually to test.

Caveats: scheduled crons can start 5–30 min late, and GitHub disables schedules after **60 days of repo inactivity** — the daily data commit itself keeps the repo active, but if runs ever stop, re-enable from the Actions tab.

**Upgrade path: Hetzner CX22 VPS (~€4.60/mo)** when you want exact timing, a live-served dashboard, or eventually real-money order placement (don't run live trading from ephemeral CI). DigitalOcean $6/mo is the simpler-UI alternative. Set a cron: `0 21 * * * cd ~/kalshiAgent && .venv/bin/python master_agent.py run --category TV`.

**Laptop-only fallback (Windows Task Scheduler):**
```powershell
schtasks /Create /TN "KalshiAgent" /SC DAILY /ST 17:00 /TR "C:\...\kalshiAgent\.venv\Scripts\python.exe C:\...\kalshiAgent\master_agent.py run"
```
Only fires when the laptop is on — fine as a backup, not as the primary.

---

## 6. Edge Recommendations — Implementation Status

All code-level recommendations were implemented on 2026-07-09 (details in the Decision Log, §8):

| # | Recommendation | Status |
|---|---|---|
| 1 | TikTok weekly velocity (count only videos posted this Netflix week, not all-time views) | ✅ `TikTokOracle` filters by `createTime` ≥ week Monday; needs ≥ 5 in-window videos or returns no reading (`TIKTOK_WEEKLY_WINDOW`, `TIKTOK_MIN_WEEKLY_VIDEOS`) |
| 2 | Trade the NO side of the overpriced market favorite | ✅ `kalshi_client` analyzes both sides each cycle; `best_side` picks the higher EV; ledger resolves NO trades correctly (`side`, `target_title`) |
| 3 | Trade only in the Sun→Mon mispricing window | ✅ `TRADE_DAYS = {Sun, Mon}` gates trade recording; other days are CAPTURE_ONLY; `TIKTOK_DAYS = {Sat, Sun, Mon}` gates Apify spend; `--force` bypasses |
| 6 | Calibrate confidence from the ledger, not constants | ✅ `PaperTrader.get_calibrated_confidence` — shrinkage toward empirical win rate of similar past signals once ≥ 8 resolved (`CALIBRATION_*` in config) |
| 7 | Quarter-Kelly everywhere + fee-aware edge gate | ✅ `KELLY_FRACTION = 0.25` used in all three sizing paths; EV computed after the 7%·P·(1−P) fee on both sides |

Still open (need data we don't collect yet — next up on the roadmap):

- **Views model for Films (hours ÷ runtime)** — needs viewing-hours data; FlixPatrol gives ordinal ranks only. Runtime currently informs the ensemble as context. Netflix's own weekly TSV has hours — backfitting a rank→hours model is the path.
- **Days-available velocity as a first-class baseline feature** — currently a +0.05 ensemble nudge via `FRESH_RELEASE_DAYS`; promoting it needs premiere dates for all contenders (TMDB key required).
- **Go-live gate (process, not code):** don't fund the account until the ledger shows **≥ 12 weeks, ≥ 60% win rate, positive P&L after fees**. Once live, prefer resting maker orders (maker fees ≈ ¼ of taker).
- Longer-term ideas: global-vs-US chart alignment, IMDb/RT decay rates.

---

## 7. Known Issues / Risks

- **FlixPatrol scraping is the single point of failure.** No API contract; a page redesign silently kills SENSE. The agent degrades to no-trade (safe), but add an alert (the GitHub Action fails visibly) and consider their $9.99/mo API if breakage becomes frequent.
- **Apify token was previously committed** — rotate it (see §2.1).
- **`DEFAULT_ENTRY_PRICE = 0.55`** is used when live quotes fail; trades recorded at assumed prices inflate/deflate the paper track record. Treat `entry_source: "assumed"` trades as lower-quality evidence.
- **Google Trends (pytrends) is rate-limited and flaky** — it's already fail-safe, but don't be surprised by empty Trends diagnostics.
- **Resolution is manual** (`resolve "<winner>"`). Automating it from the official Netflix Tuesday TSV (`data_collector` already knows the URL) is a small, high-value task.
- `data/netflix_weekly.csv` is 27 MB of historical data — regenerable from the Netflix TSV; git-ignored on purpose.

---

## 8. Decision Log

*Every significant decision, dated, with the why. Append here whenever the design changes — this section is what makes handovers painless.*

**2026-07-09 — Secrets moved to `.env`.** The Apify token was hardcoded in source. All keys now load from a git-ignored `.env` via `python-dotenv` in `config.py`; `.env.example` is the template. The old token must be rotated (it lives in old copies of the repo).

**2026-07-09 — Repo cleaned for handover.** Deleted one-off prototype scripts (`tiktok_check.py`, `wiki_check.py`, `quick_scrape.py`, `analyze_week.py` — all superseded by `master_agent.py` and the oracles in `data_collector.py`), the old motivational handover (`FABLE5_HANDOVER.md`), the brainstorm doc (`FUTURE_FEATURES.md` — surviving ideas folded into §6), `.agents/` skills, and generated preview files. If a quick manual check is needed, the oracles are importable directly.

**2026-07-09 — TikTok measures weekly velocity, not fame.** Raw hashtag view totals are cumulative all-time, so an old viral title always outscored a title exploding this week. The oracle now counts only videos posted during the current Netflix week (Mon–Sun) and refuses to give a reading (returns None → override disabled) with fewer than 5 in-window videos. Config: `TIKTOK_WEEKLY_WINDOW`, `TIKTOK_MIN_WEEKLY_VIDEOS`.

**2026-07-09 — Both market sides analyzed; NO on the favorite is a first-class trade.** The Weekend Effect thesis is really "the mid-week leader is overpriced." Buying NO on the market favorite (cost = 1 − yes_bid) pays whenever *anyone else* wins and uses our confidence as a conservative lower bound on P(favorite loses). Each cycle computes edge/fee/EV/Kelly for both sides and trades `best_side`. The ledger stores `side` and `target_title` and resolves NO trades as wins when the faded title did NOT top the chart.

**2026-07-09 — Trading calendar enforced in code.** Trades are only recorded on Sunday/Monday (`TRADE_DAYS`) when weekend data exists and the market is still open; Apify credit is only spent Sat–Mon (`TIKTOK_DAYS`). All other days run CAPTURE_ONLY (scrape + signal + snapshot, $0). Rationale: the mispricing window is Sunday night → Monday close, and daily TikTok scraping would cost 2.3× more for signal we can't act on. `--force` bypasses both gates.

**2026-07-09 — Confidence is calibrated from the ledger.** Hand-tuned constants (0.85 override, ±0.05 nudges) are shrunk toward the empirical win rate of resolved trades with similar recorded confidence (±0.15), once ≥ 8 such trades exist; the raw confidence counts as 10 pseudo-trades in the blend. No-op until the ledger has history. Config: `CALIBRATION_*`.

**2026-07-09 — Quarter-Kelly standardized.** `KELLY_FRACTION = 0.25` is the single source of truth used by `paper_trader`, `kalshi_client`, and the backtest simulator. Never full Kelly: our probabilities are estimates, and full Kelly on overestimated edge is ruinous.

**2026-07-09 — FlixPatrol scraper fixed: never advertise Brotli.** The scraper silently returned 0 entries because FlixPatrol serves `content-encoding: br` when the request advertises it, and `requests` can't decode Brotli without an extra package — the HTML arrived as binary garbage. `Accept-Encoding` in `config.REQUEST_HEADERS` is now `gzip, deflate` only. Verified live: 40 entries/day scraped. If it ever returns 0 entries again, check this first.

**2026-07-09 — Every run archives its scrape.** Scraped rankings used to be discarded when no signal was possible (e.g. mid-week). `MasterAgent._persist_rankings` now appends every scrape to `data/daily_rankings.csv` (deduplicated on date/category/rank/country), and the daily workflow commits that file — the historical daily record grows even on capture-only days.

**2026-07-09 — Hosting: GitHub Actions daily at 21:00 UTC.** `.github/workflows/daily_agent.yml` runs both categories daily and commits captured data back to the repo (zero-cost persistence). Upgrade to a small VPS only when placing real orders.

**Not done (deliberately):** the Films views model (hours ÷ runtime) and first-class days-available velocity need data sources we don't have wired yet — see §6 "Still open".
