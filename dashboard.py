"""
Prediction dashboard generator.

Renders ``data/latest_cycle.json`` (written by ``master_agent.py run``) and
the paper-trading ledger into a single self-contained dark-theme HTML file:
``output/dashboard.html``. No external assets, no server — open it in any
browser.

Sections
--------
* Decision header — prediction, confidence, TRADE / NO TRADE badge.
* Stat tiles — confidence, Kalshi ask, edge, EV/contract, Kelly size, bankroll.
* Confidence waterfall — base → boosters → Alpha Override → each oracle → final.
* Daily rank heatmap — FlixPatrol Top 10 grid for the current week.
* Oracle readings table — every raw variable per contender.
* Kalshi market panel — all outcome quotes, matched titles, edge notes.
* Ledger — performance tiles, equity curve, full trade history.

Usage:  python master_agent.py dashboard   (or)   python dashboard.py
"""

from __future__ import annotations

import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), 'src')))

import html
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import config as cfg

OUTPUT_DIR = cfg.PROJECT_ROOT / "output"

# ── Palette (validated dark-surface steps; see dataviz reference) ──────
INK = "#ffffff"
INK_2 = "#c3c2b7"
MUTED = "#898781"
SURFACE = "#1a1a19"
PAGE = "#0d0d0d"
GRID = "#2c2c2a"
BORDER = "rgba(255,255,255,0.10)"
BLUE = "#3987e5"
AQUA = "#199e70"
RED = "#e66767"
GOOD = "#0ca30c"
WARN = "#fab219"
CRIT = "#d03b3b"

# Sequential blue ramp, brightest = rank 1 (salience on a dark surface)
RANK_RAMP = [
    "#b7d3f6", "#9ec5f4", "#86b6ef", "#6da7ec", "#5598e7",
    "#3987e5", "#256abf", "#1c5cab", "#184f95", "#104281",
]
# Ink color inside each ramp step (light steps take dark ink)
RANK_INK = ["#0b0b0b"] * 5 + ["#ffffff"] * 5


def esc(v: Any) -> str:
    return html.escape(str(v))


def fmt_num(v: Optional[float]) -> str:
    if v is None:
        return "—"
    v = float(v)
    if abs(v) >= 1_000_000:
        return f"{v / 1_000_000:.1f}M"
    if abs(v) >= 1_000:
        return f"{v / 1_000:.1f}K"
    return f"{v:,.0f}"


def fmt_price(v: Optional[float]) -> str:
    return "—" if v is None else f"{float(v):.2f}"


def fmt_signed(v: Optional[float], decimals: int = 2) -> str:
    return "—" if v is None else f"{float(v):+.{decimals}f}"


# ---------------------------------------------------------------------------
# Section renderers
# ---------------------------------------------------------------------------

def _tile(label: str, value: str, sub: str = "", value_color: str = INK) -> str:
    sub_html = f'<div class="tile-sub">{sub}</div>' if sub else ""
    return (
        f'<div class="tile"><div class="tile-label">{esc(label)}</div>'
        f'<div class="tile-value" style="color:{value_color}">{value}</div>'
        f"{sub_html}</div>"
    )


def _badge(text: str, color: str) -> str:
    return (
        f'<span class="badge" style="border-color:{color};color:{color}">'
        f"{esc(text)}</span>"
    )


def render_header(snap: dict) -> str:
    final = snap.get("final_signal") or {}
    decision = snap.get("decision") or {}
    action = decision.get("action", "—")
    badge = (
        _badge("TRADE", GOOD) if action == "TRADE" else _badge("NO TRADE", WARN)
    )
    gen = snap.get("generated_at", "")[:19].replace("T", " ")
    return f"""
    <header>
      <div>
        <h1>Kalshi Netflix Master Agent</h1>
        <div class="meta">{esc(snap.get('category', ''))} · week of
          {esc(snap.get('week_monday', ''))} · data through
          {esc(snap.get('as_of_date', ''))} · generated {esc(gen)} UTC</div>
      </div>
      <div class="hero-box">
        <div class="tile-label">Predicted weekly #1</div>
        <div class="hero">{esc(final.get('predicted_winner', '—'))}</div>
        <div class="meta">{badge}
          <span class="strength">{esc(final.get('signal_strength', ''))} signal</span>
        </div>
      </div>
    </header>"""


def render_tiles(snap: dict) -> str:
    final = snap.get("final_signal") or {}
    decision = snap.get("decision") or {}
    kalshi = snap.get("kalshi") or {}
    ledger = snap.get("ledger_summary") or {}

    conf = final.get("confidence")
    edge = decision.get("edge")
    ev = kalshi.get("ev_per_contract")
    edge_color = GOOD if (edge or 0) >= (decision.get("min_edge") or 0) else CRIT
    ask = kalshi.get("winner_ask")
    implied = kalshi.get("winner_implied_prob")

    tiles = [
        _tile("Our probability", f"{conf:.0%}" if conf is not None else "—"),
        _tile(
            "Kalshi ask",
            fmt_price(ask),
            sub=f"implied {implied:.0%}" if implied is not None else "no live quote",
        ),
        _tile(
            "Edge vs ask",
            fmt_signed(edge),
            sub=f"min {decision.get('min_edge', 0):.2f}",
            value_color=edge_color if edge is not None else INK,
        ),
        _tile(
            "EV / contract",
            f"${ev:+.3f}" if ev is not None else "—",
            sub=(
                f"fee ${kalshi.get('fee_per_contract'):.3f}"
                if kalshi.get("fee_per_contract") is not None
                else ""
            ),
            value_color=(GOOD if (ev or 0) > 0 else CRIT) if ev is not None else INK,
        ),
        _tile(
            "Position (¼-Kelly)",
            f"{decision.get('contracts', 0) or 0}",
            sub=(
                f"${decision.get('bet_size'):.2f} at "
                f"{fmt_price(decision.get('entry_price'))} "
                f"({esc(decision.get('entry_source', ''))})"
                if decision.get("bet_size") is not None
                else f"entry {fmt_price(decision.get('entry_price'))}"
            ),
        ),
        _tile(
            "Bankroll",
            f"${ledger.get('bankroll', 0):,.2f}",
            sub=f"ROI {ledger.get('roi', 0):+.1f}%",
        ),
    ]
    return '<div class="tiles">' + "".join(tiles) + "</div>"


_BOOST_VAL_RE = re.compile(r"([+-]\d+(?:\.\d+)?)\s*$")


def _waterfall_steps(snap: dict) -> list[tuple[str, float, str]]:
    """Return (label, delta_or_abs, kind) steps; kind in {base, delta, final}."""
    baseline = snap.get("baseline") or {}
    breakdown = baseline.get("breakdown") or {}
    override = snap.get("alpha_override") or {}
    ensemble = snap.get("ensemble") or {}
    final = snap.get("final_signal") or {}

    steps: list[tuple[str, float, str]] = []
    base = breakdown.get("base_confidence")
    if base is None:
        base = baseline.get("confidence", 0.0)
    steps.append(("Weekend Effect base", float(base), "base"))

    for boost in breakdown.get("boosts") or []:
        m = _BOOST_VAL_RE.search(boost)
        val = float(m.group(1)) if m else 0.0
        label = boost.split(":")[0][:44]
        steps.append((label, val, "delta"))

    if override.get("fired"):
        delta = float(override.get("confidence", 0)) - float(
            baseline.get("confidence", 0)
        )
        steps.append(("TikTok Alpha Override (>2x)", delta, "delta"))

    for key, label in (
        ("wikipedia", "Wikipedia pageviews"),
        ("google_trends", "Google Trends"),
        ("tmdb", "TMDB release velocity"),
        ("youtube", "YouTube trailer velocity"),
    ):
        adj = ((ensemble.get(key) or {}).get("adjustment")) or 0.0
        if adj:
            steps.append((label, float(adj), "delta"))

    steps.append(("Final confidence", float(final.get("confidence", 0)), "final"))
    return steps


def render_waterfall(snap: dict) -> str:
    steps = _waterfall_steps(snap)
    rows = []
    running = 0.0
    for label, val, kind in steps:
        if kind == "base":
            left, width, color, running = 0.0, val, BLUE, val
            text = f"{val:.2f}"
        elif kind == "final":
            left, width, color = 0.0, val, BLUE
            text = f"{val:.2f}"
        else:
            new = max(0.0, min(1.0, running + val))
            left, width = min(running, new), abs(new - running)
            color = AQUA if val >= 0 else RED
            running = new
            text = f"{val:+.2f}"
        bar_cls = "wf-bar wf-final" if kind == "final" else "wf-bar"
        rows.append(
            f'<div class="wf-row"><div class="wf-label">{esc(label)}</div>'
            f'<div class="wf-track">'
            f'<div class="{bar_cls}" style="left:{left * 100:.1f}%;'
            f'width:{max(width * 100, 0.6):.1f}%;background:{color}"></div>'
            f'<span class="wf-val" style="left:{min((left + width) * 100 + 1.5, 88):.1f}%">'
            f"{text}</span></div></div>"
        )
    return (
        '<section><h2>Confidence scoring — how the number was built</h2>'
        '<div class="waterfall">' + "".join(rows) + "</div>"
        f'<div class="axis-note">scale 0.00 – 1.00 · '
        f'<span style="color:{AQUA}">▮</span> boost · '
        f'<span style="color:{RED}">▮</span> penalty · '
        f'<span style="color:{BLUE}">▮</span> level</div></section>'
    )


def render_rank_grid(snap: dict) -> str:
    rows = snap.get("daily_rankings") or []
    if not rows:
        return ""
    dates = sorted({r["date"] for r in rows})
    by_title: dict[str, dict[str, int]] = {}
    for r in rows:
        by_title.setdefault(r["title"], {})[r["date"]] = int(r["rank"])
    # Order titles by best (lowest) average rank; keep top 10
    titles = sorted(
        by_title,
        key=lambda t: sum(by_title[t].values()) / len(by_title[t]),
    )[:10]

    day_names = [
        datetime.strptime(d, "%Y-%m-%d").strftime("%a %d") for d in dates
    ]
    head = "<tr><th></th>" + "".join(f"<th>{esc(d)}</th>" for d in day_names) + "</tr>"
    body = []
    winner = (snap.get("final_signal") or {}).get("predicted_winner")
    for title in titles:
        cells = []
        for d in dates:
            rank = by_title[title].get(d)
            if rank is None:
                cells.append('<td><span class="cell cell-empty">·</span></td>')
            else:
                i = min(rank, 10) - 1
                cells.append(
                    f'<td><span class="cell" style="background:{RANK_RAMP[i]};'
                    f'color:{RANK_INK[i]}">{rank}</span></td>'
                )
        marker = " ★" if title == winner else ""
        body.append(
            f'<tr><td class="row-title">{esc(title)}{marker}</td>{"".join(cells)}</tr>'
        )
    return (
        "<section><h2>FlixPatrol daily Top 10 — current week</h2>"
        '<div class="scroll"><table class="grid-table">'
        f"{head}{''.join(body)}</table></div>"
        '<div class="axis-note">cell = daily rank (1 = brightest) · ★ = predicted winner</div>'
        "</section>"
    )


def render_oracles(snap: dict) -> str:
    contenders = snap.get("contenders") or []
    if not contenders:
        return ""
    override = snap.get("alpha_override") or {}
    ensemble = snap.get("ensemble") or {}
    tiktok = override.get("tiktok_volumes") or {}
    wiki = (ensemble.get("wikipedia") or {}).get("magnitudes") or {}
    trends = (ensemble.get("google_trends") or {}).get("magnitudes") or {}
    youtube = (ensemble.get("youtube") or {}).get("magnitudes") or {}

    latest_rank: dict[str, int] = {}
    rows_data = snap.get("daily_rankings") or []
    if rows_data:
        last_day = max(r["date"] for r in rows_data)
        for r in rows_data:
            if r["date"] == last_day:
                latest_rank[r["title"]] = int(r["rank"])

    winner = (snap.get("final_signal") or {}).get("predicted_winner")
    body = []
    for title in contenders:
        cls = ' class="winner-row"' if title == winner else ""
        body.append(
            f"<tr{cls}><td>{esc(title)}</td>"
            f"<td>{latest_rank.get(title, '—')}</td>"
            f"<td>{fmt_num(tiktok.get(title))}</td>"
            f"<td>{fmt_num(wiki.get(title))}</td>"
            f"<td>{fmt_num(trends.get(title))}</td>"
            f"<td>{fmt_num(youtube.get(title))}</td></tr>"
        )
    fired = override.get("fired")
    override_note = (
        f'{_badge("OVERRIDE FIRED", CRIT)} a trailing contender beat the #1 by '
        f">{override.get('multiplier', 2):g}x on TikTok"
        if fired
        else f'{_badge("no override", MUTED)} no contender cleared the '
        f">{override.get('multiplier', 2):g}x TikTok rule"
    )
    return f"""
    <section><h2>Oracle readings — every variable, per contender</h2>
    <div class="scroll"><table>
      <tr><th>Title</th><th>FlixPatrol rank (latest)</th><th>TikTok views</th>
      <th>Wikipedia 7-day views</th><th>Google Trends</th><th>YouTube views/day</th></tr>
      {''.join(body)}
    </table></div>
    <div class="axis-note">{override_note} · “—” = oracle offline or not queried</div>
    </section>"""


def render_kalshi(snap: dict) -> str:
    kalshi = snap.get("kalshi")
    if not kalshi:
        return (
            "<section><h2>Kalshi market</h2>"
            '<div class="empty">No live Kalshi market data this cycle — trade was '
            "priced at the assumed fallback entry price.</div></section>"
        )
    quotes = kalshi.get("quotes") or []
    body = []
    for q in quotes:
        is_winner = q.get("ticker") and q.get("ticker") == kalshi.get("winner_ticker")
        cls = ' class="winner-row"' if is_winner else ""
        matched = q.get("matched_contender")
        matched_txt = (
            f"{esc(matched)} <span class='muted'>({q.get('match_score', 0):.0%})</span>"
            if matched
            else "<span class='muted'>—</span>"
        )
        implied = q.get("implied_prob")
        implied_txt = f"{implied:.0%}" if implied is not None else "—"
        top_bets = q.get("top_5_bets", [])
        if top_bets:
            bets_txt = "<br>".join(f"${b:,.2f}" for b in top_bets)
        else:
            bets_txt = "—"
        body.append(
            f"<tr{cls}><td>{esc(q.get('market_title', ''))}</td>"
            f"<td>{matched_txt}</td>"
            f"<td>{fmt_price(q.get('yes_bid'))}</td>"
            f"<td>{fmt_price(q.get('yes_ask'))}</td>"
            f"<td>{implied_txt}</td>"
            f"<td>{fmt_price(q.get('spread'))}</td>"
            f"<td>{q.get('volume', 0):,}</td>"
            f"<td>{q.get('open_interest', 0):,}</td>"
            f"<td>{bets_txt}</td></tr>"
        )
    notes = "<br>".join(esc(n) for n in kalshi.get("notes") or [])
    return f"""
    <section><h2>Kalshi market — {esc(kalshi.get('event_title', ''))}
      <span class="muted">[{esc(kalshi.get('event_ticker', ''))}]</span></h2>
    <div class="scroll"><table>
      <tr><th>Outcome</th><th>Matched contender</th><th>Bid</th><th>Ask</th>
      <th>Implied</th><th>Spread</th><th>Volume</th><th>Open interest</th><th>Top 5 Bets</th></tr>
      {''.join(body)}
    </table></div>
    <div class="axis-note">{notes}</div>
    </section>"""


def render_ledger(ledger: dict) -> str:
    trades = ledger.get("trades") or []
    resolved = [t for t in trades if t.get("status") == "resolved"]
    initial = float(ledger.get("initial_bankroll", cfg.KALSHI_BANKROLL))
    bankroll = float(ledger.get("bankroll", initial))

    wins = sum(1 for t in resolved if t.get("won"))
    win_rate = wins / len(resolved) if resolved else 0.0
    total_pnl = sum(float(t.get("pnl") or 0) for t in resolved)

    tiles = "".join(
        [
            _tile("Bankroll", f"${bankroll:,.2f}", sub=f"start ${initial:,.0f}"),
            _tile(
                "Total P&L",
                f"${total_pnl:+,.2f}",
                value_color=GOOD if total_pnl >= 0 else CRIT,
            ),
            _tile("Win rate", f"{win_rate:.0%}", sub=f"{wins}/{len(resolved)} resolved"),
            _tile("Open positions", str(sum(1 for t in trades if t.get("status") == "open"))),
        ]
    )

    spark = _equity_svg([initial] + _equity_series(initial, resolved))

    rows = []
    for t in reversed(trades[-25:]):
        status = t.get("status")
        if status == "resolved":
            badge = _badge("WON", GOOD) if t.get("won") else _badge("LOST", CRIT)
            pnl = f"{float(t.get('pnl') or 0):+,.2f}"
            pnl_color = GOOD if t.get("won") else CRIT
            actual = esc(t.get("actual_winner") or "—")
        else:
            badge = _badge("OPEN", WARN)
            pnl, pnl_color, actual = "—", INK_2, "—"
        conf_txt = f"{float(t.get('confidence') or 0):.0%}"
        rows.append(
            f"<tr><td>{esc(t.get('signal_date', ''))}</td>"
            f"<td>{esc(t.get('predicted_winner', ''))}</td>"
            f"<td>{actual}</td>"
            f"<td>{fmt_price(t.get('entry_price'))}</td>"
            f"<td>{t.get('contracts', 0)}</td>"
            f"<td>{conf_txt}</td>"
            f"<td style='color:{pnl_color}'>{pnl}</td><td>{badge}</td></tr>"
        )
    table = (
        '<div class="scroll"><table><tr><th>Signal date</th><th>Predicted</th>'
        "<th>Actual</th><th>Entry</th><th>Contracts</th><th>Confidence</th>"
        f"<th>P&L $</th><th>Status</th></tr>{''.join(rows)}</table></div>"
        if rows
        else '<div class="empty">No trades recorded yet.</div>'
    )
    return (
        "<section><h2>Paper-trading ledger</h2>"
        f'<div class="tiles">{tiles}</div>'
        f'<div class="spark-wrap"><div class="tile-label">Equity curve</div>{spark}</div>'
        f"{table}</section>"
    )


def _equity_series(initial: float, resolved: list[dict]) -> list[float]:
    resolved = sorted(resolved, key=lambda t: str(t.get("resolved_at") or ""))
    series, running = [], initial
    for t in resolved:
        running += float(t.get("pnl") or 0)
        series.append(running)
    return series


def _equity_svg(points: list[float], width: int = 640, height: int = 120) -> str:
    if len(points) < 2:
        return '<div class="empty">Equity curve appears after the first resolved trade.</div>'
    pad = 8
    lo, hi = min(points), max(points)
    span = (hi - lo) or 1.0
    n = len(points)
    coords = [
        (
            pad + i * (width - 2 * pad) / (n - 1),
            height - pad - (p - lo) * (height - 2 * pad) / span,
        )
        for i, p in enumerate(points)
    ]
    pts = " ".join(f"{x:.1f},{y:.1f}" for x, y in coords)
    area = f"{pad},{height - pad} {pts} {coords[-1][0]:.1f},{height - pad}"
    ex, ey = coords[-1]
    return f"""
    <svg viewBox="0 0 {width} {height}" class="spark" role="img"
         aria-label="Equity curve from ${points[0]:,.0f} to ${points[-1]:,.0f}">
      <polygon points="{area}" fill="{BLUE}" opacity="0.10"/>
      <polyline points="{pts}" fill="none" stroke="{BLUE}" stroke-width="2"
        stroke-linejoin="round" stroke-linecap="round"/>
      <circle cx="{ex:.1f}" cy="{ey:.1f}" r="4" fill="{BLUE}"
        stroke="{SURFACE}" stroke-width="2"/>
      <text x="{ex - 6:.1f}" y="{max(ey - 8, 12):.1f}" text-anchor="end"
        fill="{INK_2}" font-size="12">${points[-1]:,.2f}</text>
    </svg>"""


def render_reasoning(snap: dict) -> str:
    reasoning = (snap.get("final_signal") or {}).get("reasoning") or ""
    if not reasoning:
        return ""
    return (
        "<section><h2>Agent reasoning</h2>"
        f'<pre class="reasoning">{esc(reasoning)}</pre></section>'
    )


# ---------------------------------------------------------------------------
# Page assembly
# ---------------------------------------------------------------------------

_CSS = f"""
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap');

* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{
  background: radial-gradient(circle at top right, #1a1a2e 0%, #0d0d14 100%);
  color: {INK};
  font-family: 'Inter', system-ui, -apple-system, sans-serif;
  padding: 32px 24px; max-width: 1200px; margin: 0 auto; line-height: 1.5;
  min-height: 100vh;
}}
header {{ display: flex; justify-content: space-between; gap: 24px;
  flex-wrap: wrap; align-items: flex-start; margin-bottom: 32px;
  animation: fadeIn 0.8s ease-out; }}
h1 {{ font-size: 28px; font-weight: 700; letter-spacing: -0.02em; background: linear-gradient(135deg, #fff 0%, #94a3b8 100%); -webkit-background-clip: text; -webkit-text-fill-color: transparent; }}
h2 {{ font-size: 18px; font-weight: 600; margin-bottom: 16px; color: {INK}; letter-spacing: -0.01em; display: flex; align-items: center; gap: 8px;}}
h2::before {{ content: ''; display: inline-block; width: 4px; height: 16px; background: {BLUE}; border-radius: 4px; }}
.meta {{ color: {MUTED}; font-size: 14px; margin-top: 6px; }}

.hero-box {{ background: linear-gradient(145deg, rgba(30,30,46,0.7) 0%, rgba(20,20,32,0.6) 100%); backdrop-filter: blur(16px); -webkit-backdrop-filter: blur(16px);
  border: 1px solid rgba(255, 255, 255, 0.08); border-radius: 16px; padding: 20px 24px; min-width: 320px; box-shadow: 0 10px 30px rgba(0,0,0,0.3); }}
.hero {{ font-size: 36px; font-weight: 700; margin: 4px 0 8px; letter-spacing: -0.03em;
  background: linear-gradient(to right, #60a5fa, #c084fc); -webkit-background-clip: text; -webkit-text-fill-color: transparent; }}
.strength {{ color: {INK_2}; font-size: 13px; margin-left: 10px; font-weight: 500; text-transform: uppercase; letter-spacing: 0.05em; }}
.badge {{ border: 1px solid; border-radius: 999px; padding: 4px 12px;
  font-size: 11px; font-weight: 700; letter-spacing: 0.05em; text-transform: uppercase; box-shadow: 0 0 12px currentColor inset; }}
section {{ background: rgba(22, 22, 30, 0.5); backdrop-filter: blur(12px); border: 1px solid rgba(255, 255, 255, 0.04);
  border-radius: 16px; padding: 24px; margin-bottom: 24px; transition: transform 0.3s ease, box-shadow 0.3s ease; animation: slideUp 0.6s ease-out backwards; }}
section:hover {{ transform: translateY(-2px); box-shadow: 0 12px 40px rgba(0,0,0,0.25); border-color: rgba(255,255,255,0.08); }}

@keyframes slideUp {{ from {{ opacity: 0; transform: translateY(20px); }} to {{ opacity: 1; transform: translateY(0); }} }}
@keyframes fadeIn {{ from {{ opacity: 0; }} to {{ opacity: 1; }} }}
section:nth-child(2) {{ animation-delay: 0.1s; }}
section:nth-child(3) {{ animation-delay: 0.2s; }}
section:nth-child(4) {{ animation-delay: 0.3s; }}
section:nth-child(5) {{ animation-delay: 0.4s; }}

.tiles {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
  gap: 16px; margin-bottom: 20px; }}
.tile {{ background: rgba(13, 13, 20, 0.4); border: 1px solid rgba(255,255,255,0.03); border-radius: 12px;
  padding: 16px; transition: all 0.3s ease; position: relative; overflow: hidden; }}
.tile::before {{ content: ''; position: absolute; top: 0; left: 0; width: 100%; height: 2px; background: linear-gradient(90deg, transparent, rgba(255,255,255,0.1), transparent); opacity: 0; transition: opacity 0.3s ease; }}
.tile:hover {{ background: rgba(255,255,255,0.03); transform: translateY(-2px); box-shadow: 0 8px 24px rgba(0,0,0,0.2); }}
.tile:hover::before {{ opacity: 1; }}
.tile-label {{ color: {MUTED}; font-size: 13px; font-weight: 500; margin-bottom: 6px; letter-spacing: 0.01em; }}
.tile-value {{ font-size: 26px; font-weight: 700; letter-spacing: -0.02em; }}
.tile-sub {{ color: {INK_2}; font-size: 12px; margin-top: 6px; font-weight: 500; }}
.scroll {{ overflow-x: auto; padding-bottom: 8px; }}
.scroll::-webkit-scrollbar {{ height: 6px; }}
.scroll::-webkit-scrollbar-track {{ background: rgba(255,255,255,0.02); border-radius: 3px; }}
.scroll::-webkit-scrollbar-thumb {{ background: rgba(255,255,255,0.1); border-radius: 3px; }}

table {{ border-collapse: separate; border-spacing: 0; width: 100%; font-size: 14px; }}
th {{ text-align: left; color: {MUTED}; font-weight: 600; padding: 12px 14px; font-size: 12px; text-transform: uppercase; letter-spacing: 0.05em;
  border-bottom: 1px solid rgba(255,255,255,0.06); white-space: nowrap; }}
td {{ padding: 12px 14px; border-bottom: 1px solid rgba(255,255,255,0.03); font-variant-numeric: tabular-nums; color: {INK_2}; transition: background 0.2s ease; }}
tr:last-child td {{ border-bottom: none; }}
tr:hover td {{ background: rgba(255,255,255,0.02); color: {INK}; }}
.winner-row td {{ background: rgba(57, 135, 229, 0.10); font-weight: 500; color: #e2e8f0; }}
.winner-row:hover td {{ background: rgba(57, 135, 229, 0.15); }}
.muted {{ color: {MUTED}; }}

.grid-table td {{ padding: 6px; border-bottom: none; text-align: center; }}
.grid-table .row-title {{ text-align: left; font-size: 13px; color: {INK_2}; font-weight: 500;
  max-width: 220px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; padding-right: 16px; }}
.cell {{ display: inline-flex; align-items: center; justify-content: center;
  width: 36px; height: 30px; border-radius: 8px; font-size: 13px; font-weight: 600; box-shadow: 0 2px 8px rgba(0,0,0,0.2); transition: transform 0.2s; }}
.cell:hover {{ transform: scale(1.1); z-index: 10; position: relative; }}
.cell-empty {{ color: {GRID}; box-shadow: none; background: rgba(255,255,255,0.02); }}

.waterfall {{ display: grid; gap: 8px; margin-top: 16px; }}
.wf-row {{ display: grid; grid-template-columns: 260px 1fr; gap: 16px; align-items: center; padding: 4px 0; }}
.wf-label {{ color: {INK_2}; font-size: 13.5px; font-weight: 500; text-align: right;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
.wf-track {{ position: relative; height: 24px; background: rgba(0,0,0,0.2);
  border-radius: 6px; border: 1px solid rgba(255,255,255,0.05); overflow: hidden; box-shadow: inset 0 2px 4px rgba(0,0,0,0.2); }}
.wf-bar {{ position: absolute; top: 4px; bottom: 4px; border-radius: 4px; transition: width 1s cubic-bezier(0.4, 0, 0.2, 1); }}
.wf-final {{ outline: 1px solid rgba(255,255,255,0.2); outline-offset: 1px; background: linear-gradient(90deg, #3b82f6, #8b5cf6) !important; }}
.wf-val {{ position: absolute; top: 3px; font-size: 12px; font-weight: 600; color: #fff; text-shadow: 0 1px 3px rgba(0,0,0,0.8);
  font-variant-numeric: tabular-nums; transition: left 1s cubic-bezier(0.4, 0, 0.2, 1); }}

.axis-note {{ color: {MUTED}; font-size: 13px; margin-top: 16px; padding-top: 16px; border-top: 1px dashed rgba(255,255,255,0.06); }}
.empty {{ color: {MUTED}; font-size: 14px; padding: 24px 0; text-align: center; font-style: italic; }}
.reasoning {{ white-space: pre-wrap; font-family: 'JetBrains Mono', Consolas, monospace; line-height: 1.6;
  font-size: 13px; color: #cbd5e1; background: rgba(0,0,0,0.25); padding: 20px;
  border-radius: 12px; border: 1px solid rgba(255,255,255,0.05); box-shadow: inset 0 2px 12px rgba(0,0,0,0.15); }}
.spark-wrap {{ margin-bottom: 24px; padding: 16px; background: rgba(0,0,0,0.15); border-radius: 12px; border: 1px solid rgba(255,255,255,0.02); }}
.spark {{ width: 100%; max-width: 640px; height: auto; display: block; margin-top: 12px; overflow: visible; }}
.spark polyline {{ filter: drop-shadow(0 4px 6px rgba(57, 135, 229, 0.3)); }}

@media (max-width: 768px) {{
  .wf-row {{ grid-template-columns: 140px 1fr; }}
  body {{ padding: 16px; }}
  h1 {{ font-size: 24px; }}
  .tiles {{ grid-template-columns: 1fr 1fr; }}
}}
"""


def generate_dashboard(
    snapshot_file: Path | str = cfg.DATA_DIR / "latest_cycle.json",
    ledger_file: Path | str = cfg.DATA_DIR / "paper_trades.json",
    output_file: Path | str = OUTPUT_DIR / "dashboard.html",
) -> Path:
    """Render the dashboard HTML and return the output path."""
    snapshot_file, ledger_file = Path(snapshot_file), Path(ledger_file)
    output_file = Path(output_file)
    output_file.parent.mkdir(parents=True, exist_ok=True)

    snap: dict = {}
    if snapshot_file.exists():
        snap = json.loads(snapshot_file.read_text(encoding="utf-8"))
    ledger: dict = {}
    if ledger_file.exists():
        try:
            ledger = json.loads(ledger_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            ledger = {}

    if snap:
        body = (
            render_header(snap)
            + render_tiles(snap)
            + render_waterfall(snap)
            + render_rank_grid(snap)
            + render_oracles(snap)
            + render_kalshi(snap)
            + render_ledger(ledger)
            + render_reasoning(snap)
        )
    else:
        body = (
            "<header><div><h1>Kalshi Netflix Master Agent</h1>"
            '<div class="meta">No cycle snapshot found — run '
            "<code>python master_agent.py run</code> first.</div></div></header>"
            + render_ledger(ledger)
        )

    page = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Kalshi Netflix Agent — Dashboard</title>
<style>{_CSS}</style>
</head>
<body>
{body}
<footer class="axis-note" style="text-align:center;padding:10px 0 20px">
Kalshi Netflix Master Agent · regenerate with <code>python master_agent.py dashboard</code>
</footer>
</body>
</html>"""
    output_file.write_text(page, encoding="utf-8")
    return output_file


if __name__ == "__main__":
    print(f"Dashboard written to {generate_dashboard()}")
