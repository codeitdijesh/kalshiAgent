

A quantitative backtesting system and predictive agent for [Kalshi](https://kalshi.com) prediction markets, designed to exploit the **Weekend Effect** in Netflix Top 10 viewership data.

---

## 📑 Table of Contents
- [Strategy Overview](#-strategy-overview)
- [Installation](#-installation)
- [Quick Start](#-quick-start)
- [Project Phases](#-project-phases)
- [File Structure](#-file-structure)
- [CLI Reference](#-cli-reference)
- [Data Sources](#-data-sources)
- [Risk Disclaimers](#-risk-disclaimers)

---

## 🧠 Strategy Overview

### The Weekend Effect
Shows that dominate Netflix viewership on **weekends** (Saturday & Sunday) tend to top the **weekly** chart — even when a different show held the #1 spot for more weekdays. This creates a predictable signal for Kalshi markets: *"Will [show] be #1 on Netflix this week?"*

### Why It Works
*   🍿 **Viewer Behaviour:** Casual viewers binge on weekends, amplifying total hours for mass-appeal shows.
*   📊 **Netflix Methodology:** The weekly Top 10 aggregates viewing hours Mon–Sun. Weekend surges easily outweigh moderate weekday performance.
*   📉 **Market Mispricing:** Traders anchor on mid-week FlixPatrol rankings, systematically underweighting the late-week viewership surge.

### Kalshi Market Mechanics
*   **Resolution:** Based on the official [Netflix US Top 10](https://top10.netflix.com), published every Tuesday.
*   **Deadline:** Contracts close at 11:59 PM ET on the Monday prior.
*   **Pricing:** Contracts trade between $0.00–$1.00 (price = implied probability).

---

## ⚙️ Installation

**Prerequisites:** Python 3.10+, pip

```bash
# Clone the repository
git clone [https://github.com/codeitdijesh/kalshiAgent.git](https://github.com/codeitdijesh/kalshiAgent.git)
cd kalshiAgent

# Create and activate a virtual environment
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
