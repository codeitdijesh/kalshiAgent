# Kalshi Netflix Weekend Effect Backtester

A quantitative backtesting system for a prediction-market trading strategy on [Kalshi](https://kalshi.com), exploiting the **Weekend Effect** in Netflix Top 10 **MOVIES** viewership data.

---

## Strategy Overview

### The Weekend Effect

Shows that dominate Netflix viewership on **weekends** (Saturday & Sunday) tend to top the **weekly** chart — even when a different show held the #1 spot for more weekdays (Monday–Friday). This creates a predictable, exploitable signal for Kalshi markets of the form:

> *"Will [show] be #1 on Netflix this week?"*

### Why It Works

| Factor | Detail |
|---|---|
| **Viewer behaviour** | Casual viewers binge on weekends, amplifying viewership hours for shows with mass appeal. |
| **Netflix methodology** | The official weekly Top 10 aggregates total viewing hours Mon–Sun. Two high-volume weekend days can outweigh five moderate weekdays. |
| **Market mispricing** | Most traders anchor on mid-week rankings (visible on FlixPatrol), underweighting the weekend surge that ultimately determines the official chart. |

### Kalshi Market Mechanics

- Markets resolve based on the [Netflix official US Top 10](https://top10.netflix.com), published every **Tuesday**.
- Contracts close at **11:59 PM ET on Monday** before the Tuesday release.
- Each contract is priced **$0.00–$1.00**, where the price equals the implied probability.
- Separate markets exist for **TV shows** and **Films**.

---

## Installation

### Prerequisites

- Python 3.10+
- pip

### Setup

```bash
# Clone the repository
git clone https://github.com/your-username/KalshiAgent.git
cd KalshiAgent

# Create a virtual environment (recommended)
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # macOS / Linux

# Install dependencies
pip install -r requirements.txt
```

### Dependencies

| Package | Purpose |
|---|---|
| `pandas` | Data manipulation & analysis |
| `numpy` | Numerical computation |
| `matplotlib` | Plotting engine |
| `seaborn` | Statistical visualisations |
| `rich` | Terminal formatting & tables |
| `requests` | HTTP client for data collection |
| `beautifulsoup4` | HTML parsing (FlixPatrol scraping) |

---

## Quick Start

```bash
# 1. Generate sample data (52 weeks by default)
python run_backtest.py --mode sample

# 2. Run the backtest
python run_backtest.py --mode backtest

# 3. Run statistical analysis + Monte Carlo
python run_backtest.py --mode analyze

# 4. Start paper trading
python run_backtest.py --mode paper
```

All plots are saved to the `output/` directory. Results are saved as JSON.

---

## Project Phases

### Phase 1 — Historical Backtest *(current)*

Validate the weekend-effect hypothesis against historical data:

1. **Collect data** — Scrape daily rankings from FlixPatrol and weekly charts from Netflix.
2. **Identify signals** — Detect shows that rank higher on weekends than weekdays.
3. **Simulate trades** — For each signal, simulate buying/selling the corresponding Kalshi contract.
4. **Evaluate** — Measure win rate, P&L, Sharpe ratio, and max drawdown.
5. **Visualise** — Generate equity curves, heatmaps, and Monte Carlo fan charts.

### Phase 2 — Paper Trading

Forward-test the strategy in real time *without* risking capital:

1. Each weekend, the system generates a trading signal.
2. Record the signal via `python run_backtest.py --mode paper` → `signal`.
3. On Tuesday, record the actual Netflix #1 via → `outcome`.
4. Track running performance to confirm the edge holds out-of-sample.

### Phase 3 — Live Trading *(future)*

Connect to the Kalshi API for automated order placement once the edge is validated through paper trading.

---

## File Structure

```
KalshiAgent/
├── master_agent.py        # ★ LIVE entry point — sense→think→override→act
├── run_backtest.py        # Backtest CLI entry point (sample/backtest/analyze/paper)
├── dashboard.py           # Self-contained HTML dashboard generator
├── whale_tracker.py       # CLI script to track market whales and largest trades
├── src/                   # Core library modules
│   ├── analyzer.py        # Statistical analysis & Monte Carlo
│   ├── backtester.py      # Core backtesting engine
│   ├── config.py          # ALL tuneable parameters + .env loading
│   ├── data_collector.py  # FlixPatrol scraper + all oracles (TikTok, Wiki, Trends, TMDB, YouTube)
│   ├── kalshi_client.py   # Kalshi Trade API v2 — live quotes, edge, EV, top 5 bets
│   ├── llm_agent.py       # Kalshi_Predictor ADK agent for final qualitative override
│   ├── paper_trader.py    # Paper-trading ledger (Kelly-sized) with JSON persistence
│   ├── promo_agent.py     # Netflix_Scout ADK agent for fetching natively promoted movies
│   ├── strategy.py        # Weekend Effect, Alpha Override (>2x rule), Oracle Ensemble
│   └── visualizer.py      # matplotlib/seaborn plots
├── docs/                  # Documentation
│   ├── ARCHITECTURE.md    # Complete system architecture and file map
│   ├── PIPELINE.md        # Signal generation, trading logic, and market mechanics
│   ├── DATA_SOURCES.md    # Details on all data sources and oracles
│   ├── AI_AGENT_INSTRUCTIONS.md # Instructions and prompt for the final AI decision agent
│   ├── BACKTEST_2025_RESULTS.md # Official results and metrics from the 2025-2026 backtest
│   └── HANDOVER.md        # ★ Full handover: setup, ops runbook, costs, hosting
├── .env                   # API keys (NEVER commit — see .env.example)
├── .env.example           # Template for a new machine
├── .github/workflows/     # daily_agent.yml — scheduled daily runs
├── requirements.txt       # Python dependencies
├── data/                  # Raw & processed data (mostly git-ignored)
│   ├── daily_rankings.csv
│   ├── netflix_weekly.csv
│   ├── latest_cycle.json  # Snapshot of the latest agent cycle (dashboard source)
│   └── paper_trades.json  # The live track record
└── output/                # Generated plots, dashboard.html & results
```

---

## CLI Reference

```
python run_backtest.py --mode MODE [--weeks N] [--bankroll N]
```

| Argument | Default | Description |
|---|---|---|
| `--mode` | `sample` | One of: `sample`, `backtest`, `analyze`, `paper` |
| `--weeks` | `52` | Weeks of sample data to generate (sample mode only) |
| `--bankroll` | `100.0` | Starting bankroll in USD |

---

## Output Plots

| Plot | File | Description |
|---|---|---|
| Equity Curve | `equity_curve.png` | Bankroll over time with win/loss shading and max drawdown |
| Win Rate × Confidence | `win_rate_by_confidence.png` | Actual vs. predicted win rate by confidence bucket |
| Weekend Heatmap | `weekend_heatmap.png` | Average rank per title per day-of-week |
| Monthly P&L | `monthly_pnl.png` | Bar chart of profit/loss by calendar month |
| Rolling Hit Rate | `hit_rate_over_time.png` | 10-week rolling win rate to assess edge stability |
| Monte Carlo | `monte_carlo.png` | 10,000-path simulation with percentile fan and P(Ruin) |

All plots use a dark theme with a consistent colour palette for a professional look.

---

## Data Sources

1. **FlixPatrol** ([flixpatrol.com](https://flixpatrol.com/top10/netflix/)) — Daily Top 10 rankings by country.
2. **Netflix Top 10** ([top10.netflix.com](https://top10.netflix.com)) — Official weekly CSV with viewing hours.
3. **Apify TikTok Scraper** — Captures true binge-watch hype via `#hashtag` views to filter out dummy "rankings".
4. **TMDB / OMDB API** — Fetches exact movie runtimes to calculate real Netflix "Views" (Hours / Runtime).

---

## Risk Disclaimers

> [!CAUTION]
> **This software is for educational and research purposes only.**

- **No financial advice.** Nothing in this project constitutes financial, investment, or trading advice.
- **Past performance ≠ future results.** A profitable backtest does not guarantee future profitability.
- **You can lose money.** Prediction-market contracts can lose 100% of their value.
- **Model risk.** The weekend effect may weaken, disappear, or reverse at any time.
- **Data risk.** Scraped data may be inaccurate, delayed, or incomplete.
- **Regulatory risk.** Ensure prediction-market trading is legal in your jurisdiction.
- **Do your own research.** Always validate signals independently before risking capital.

---

## License

This project is provided as-is for educational purposes. See [LICENSE](LICENSE) for details.
#   k a l s h i A g e n t  
 