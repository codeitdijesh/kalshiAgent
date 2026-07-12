#!/usr/bin/env python
"""
Shipwrecked Market Monitor — standalone Kalshi watcher.

Continuously polls the Kalshi Netflix movie-ranking market for the movie
"Shipwrecked". When detected, sends an email (and optionally SMS) alert
and — if the YES price is below $0.20 — places a YES bet automatically.

USAGE
-----
    python shipwrecked_monitor.py                  # dry-run: alerts only, no real orders
    python shipwrecked_monitor.py --bet            # alerts + live YES bets
    python shipwrecked_monitor.py --interval 30    # poll every 30 s (default: 60 s)
    python shipwrecked_monitor.py --once           # single check, then exit

NOTIFICATIONS
-------------
Email  : set EMAIL_FROM + EMAIL_PASSWORD (Gmail app password) + EMAIL_TO in .env
SMS    : set PHONE_NUMBER + PHONE_CARRIER in .env
         (carrier = one of: verizon, att, tmobile, sprint, googlefi, cricket,
          metro, boost, uscellular, virgin, tracfone)

Kalshi credentials are read from the project .env like the rest of the app.

This script is INTENTIONALLY standalone — it does not touch the dashboard,
the paper trader, Supabase, or the agent pipeline.  Run it, let it watch,
and stop it with Ctrl-C when you're done.
"""

from __future__ import annotations

import argparse
import base64
import json
import logging
import os
import smtplib
import sys
import time
import uuid
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path
from typing import Optional

# -- project imports (uses the existing Kalshi client + config) -----------
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env")

import config as cfg
from kalshi_client import KalshiClient, normalize_title

# ---------------------------------------------------------------------------
# logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("shipwrecked")

# ---------------------------------------------------------------------------
# config (from env — add these to your .env)
# ---------------------------------------------------------------------------

TARGET_MOVIE: str = os.environ.get("SHIPWRECKED_TARGET", "Shipwrecked")
POLL_INTERVAL: int = int(os.environ.get("SHIPWRECKED_POLL_INTERVAL", "60"))
MAX_PRICE_CENTS: int = int(os.environ.get("SHIPWRECKED_MAX_PRICE_CENTS", "20"))
BET_CONTRACTS: int = int(os.environ.get("SHIPWRECKED_BET_CONTRACTS", "50"))
BET_MAX_COST_CENTS: int = int(
    os.environ.get("SHIPWRECKED_BET_MAX_COST_CENTS", "1000")
)  # $10.00 total

# -- email ------------------------------------------------------------------
EMAIL_FROM: str = os.environ.get("EMAIL_FROM", "")
EMAIL_PASSWORD: str = os.environ.get("EMAIL_PASSWORD", "")
EMAIL_TO: str = os.environ.get("EMAIL_TO", "")
SMTP_SERVER: str = os.environ.get("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT: int = int(os.environ.get("SMTP_PORT", "587"))

# -- SMS (via carrier email→SMS gateways) -----------------------------------
PHONE_NUMBER: str = os.environ.get("PHONE_NUMBER", "")  # e.g. "2125551234"
PHONE_CARRIER: str = os.environ.get("PHONE_CARRIER", "").lower()

CARRIER_GATEWAYS: dict[str, str] = {
    "verizon": "{num}@vtext.com",
    "att": "{num}@txt.att.net",
    "tmobile": "{num}@tmomail.net",
    "sprint": "{num}@messaging.sprintpcs.com",
    "googlefi": "{num}@msg.fi.google.com",
    "cricket": "{num}@sms.cricketwireless.net",
    "metro": "{num}@mymetropcs.com",
    "boost": "{num}@sms.myboostmobile.com",
    "uscellular": "{num}@email.uscc.net",
    "virgin": "{num}@vmobl.com",
    "tracfone": "{num}@mmst5.tracfone.com",
}

# ---------------------------------------------------------------------------
# KalshiClient with order-placement (extends existing client)
# ---------------------------------------------------------------------------


class TradingKalshiClient(KalshiClient):
    """Adds POST-based order placement to the standard KalshiClient."""

    def create_order(
        self,
        ticker: str,
        side: str,
        count: int,
        buy_max_cost: Optional[int] = None,
        yes_price: Optional[int] = None,
        client_order_id: Optional[str] = None,
    ) -> Optional[dict]:
        """Place a market or limit order. Prices/costs in *cents*.

        Returns the full API response dict or None on failure.
        """
        body: dict = {
            "ticker": ticker,
            "side": side,
            "count": count,
            "client_order_id": client_order_id or str(uuid.uuid4()),
        }
        if buy_max_cost is not None:
            body["buy_max_cost"] = buy_max_cost
            body["type"] = "market"
        elif yes_price is not None:
            body["yes_price"] = yes_price
            body["type"] = "limit"
        else:
            logger.error("create_order: must provide buy_max_cost or yes_price")
            return None

        return self._post("/portfolio/orders", body, auth=True)

    def _post(
        self, path: str, body: dict, auth: bool = False
    ) -> Optional[dict]:
        """Authenticated POST to the Kalshi API."""
        url = self.base_url + path
        headers = {"Content-Type": "application/json"}
        if auth:
            if not self.authenticated:
                logger.error("Cannot POST %s — not authenticated", path)
                return None
            headers.update(self._auth_headers("POST", path))
        for attempt in range(1, cfg.MAX_RETRIES + 1):
            try:
                resp = self._session.post(
                    url, json=body, headers=headers, timeout=self.timeout
                )
                if resp.status_code == 429:
                    time.sleep(1.5 * attempt)
                    continue
                resp.raise_for_status()
                return resp.json()
            except Exception as exc:
                logger.warning(
                    "Kalshi POST %s failed (attempt %d/%d): %s",
                    path,
                    attempt,
                    cfg.MAX_RETRIES,
                    exc,
                )
                time.sleep(1.0 * attempt)
        return None


# ---------------------------------------------------------------------------
# notification
# ---------------------------------------------------------------------------


def send_email(subject: str, body: str) -> bool:
    """Send an email alert. Returns True on success."""
    if not EMAIL_FROM or not EMAIL_PASSWORD or not EMAIL_TO:
        logger.warning("Email not configured — skipping notification.")
        return False

    try:
        msg = EmailMessage()
        msg["From"] = EMAIL_FROM
        msg["To"] = EMAIL_TO
        msg["Subject"] = subject
        msg.set_content(body)

        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT, timeout=15) as server:
            server.starttls()
            server.login(EMAIL_FROM, EMAIL_PASSWORD)
            server.send_message(msg)

        logger.info("✅ Email sent to %s", EMAIL_TO)
        return True
    except Exception as exc:
        logger.error("Failed to send email: %s", exc)
        return False


def send_sms(body: str) -> bool:
    """Send an SMS via carrier email gateway. Returns True on success."""
    if not PHONE_NUMBER or not PHONE_CARRIER:
        return False

    gateway = CARRIER_GATEWAYS.get(PHONE_CARRIER)
    if not gateway:
        logger.warning(
            "Unknown carrier '%s'. Valid: %s",
            PHONE_CARRIER,
            ", ".join(CARRIER_GATEWAYS),
        )
        return False

    to_addr = gateway.format(num=PHONE_NUMBER)
    try:
        msg = EmailMessage()
        msg["From"] = EMAIL_FROM
        msg["To"] = to_addr
        msg["Subject"] = ""  # SMS doesn't need a subject
        msg.set_content(body[:160])  # SMS character limit

        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT, timeout=15) as server:
            server.starttls()
            server.login(EMAIL_FROM, EMAIL_PASSWORD)
            server.send_message(msg)

        logger.info("📱 SMS sent to %s (%s)", PHONE_NUMBER, PHONE_CARRIER)
        return True
    except Exception as exc:
        logger.error("Failed to send SMS: %s", exc)
        return False


def notify(title: str, price_cents: float, ticker: str, bet_placed: bool) -> None:
    """Send email + SMS alert that Shipwrecked was detected."""
    price_dollars = price_cents / 100.0
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    subject = f"🚨 SHIPWRECKED DETECTED on Kalshi @ ${price_dollars:.2f}"
    body = (
        f"Shipwrecked has appeared on the Kalshi Netflix movie-ranking market!\n\n"
        f"  Detected at : {now}\n"
        f"  Market      : {ticker}\n"
        f"  YES price   : {price_cents:.0f}¢ (${price_dollars:.2f})\n"
        f"  Auto-bet    : {'YES — placed!' if bet_placed else 'NO (dry-run or above threshold)'}\n"
        f"  Max threshold: {MAX_PRICE_CENTS}¢\n\n"
        f"  Kalshi link : https://kalshi.com/markets/kxnetflixrankmovie/netflix-movie-ranking\n"
    )

    email_ok = send_email(subject, body)
    sms_ok = send_sms(body)
    if not email_ok and not sms_ok:
        logger.warning("No notification channels configured — printed alert only.")


# ---------------------------------------------------------------------------
# main monitor loop
# ---------------------------------------------------------------------------


def find_market_for_movie(
    client: KalshiClient, movie_name: str, category: str = cfg.CATEGORY_FILMS
) -> Optional[tuple[dict, dict]]:
    """Search the Netflix movie event for *movie_name*.

    Returns ``(event_dict, matched_market_dict)`` or ``None``.
    """
    # Discover the Netflix movie event
    event = None
    for series in cfg.KALSHI_NETFLIX_SERIES.get(category, []):
        data = client.get_events(series_ticker=series)
        if data and data.get("events"):
            # Prefer active event
            evts = data["events"]
            evts.sort(
                key=lambda e: any(
                    (m.get("yes_bid") or 0) > 0 or (m.get("volume") or 0) > 0
                    for m in e.get("markets", [])
                ),
                reverse=True,
            )
            event = evts[0]
            break

    if not event:
        # fallback: scan all open events
        cursor = None
        for _ in range(10):
            data = client.get_events(cursor=cursor)
            if not data:
                break
            for e in data.get("events", []):
                title = (e.get("title") or "").lower()
                if "netflix" in title and "movie" in title:
                    event = e
                    break
            if event:
                break
            cursor = data.get("cursor")
            if not cursor:
                break

    if not event:
        logger.debug("No Netflix movie event found on Kalshi.")
        return None

    # Get markets (may be nested)
    markets = event.get("markets") or []
    if not markets:
        data = client.get_markets(event.get("event_ticker", ""))
        markets = (data or {}).get("markets") or []

    # Fuzzy-match our target movie
    target_norm = normalize_title(movie_name)
    for m in markets:
        label = (
            m.get("yes_sub_title")
            or m.get("subtitle")
            or m.get("title")
            or ""
        )
        market_norm = normalize_title(label)
        if target_norm == market_norm or target_norm in market_norm or market_norm in target_norm:
            return (event, m)

    # Relaxed match
    for m in markets:
        label = (
            m.get("yes_sub_title")
            or m.get("subtitle")
            or m.get("title")
            or ""
        )
        from difflib import SequenceMatcher

        if SequenceMatcher(None, normalize_title(label), target_norm).ratio() > 0.85:
            return (event, m)

    return None


def place_yes_bet(client: TradingKalshiClient, ticker: str) -> Optional[dict]:
    """Place a market-order YES bet up to BET_MAX_COST_CENTS total."""
    logger.info(
        "🎲 Placing YES bet: %d contracts, max cost %d¢ ($%.2f) on %s",
        BET_CONTRACTS,
        BET_MAX_COST_CENTS,
        BET_MAX_COST_CENTS / 100.0,
        ticker,
    )
    result = client.create_order(
        ticker=ticker,
        side="yes",
        count=BET_CONTRACTS,
        buy_max_cost=BET_MAX_COST_CENTS,
    )
    if result:
        logger.info("✅ Order response: %s", json.dumps(result, indent=2))
    else:
        logger.error("❌ Order failed — check API credentials.")
    return result


def run_monitor(
    interval: int = POLL_INTERVAL,
    auto_bet: bool = False,
    once: bool = False,
) -> None:
    """Main polling loop."""
    logger.info("=" * 64)
    logger.info("  SHIPWRECKED MARKET MONITOR")
    logger.info("  Target  : %s", TARGET_MOVIE)
    logger.info("  Interval: %d s", interval)
    logger.info("  Auto-bet: %s (max %d¢)", "ON" if auto_bet else "OFF (dry-run)", MAX_PRICE_CENTS)
    logger.info("  Email   : %s", "configured" if EMAIL_FROM else "NOT CONFIGURED")
    logger.info("  SMS     : %s", "configured" if (PHONE_NUMBER and PHONE_CARRIER) else "NOT CONFIGURED")
    logger.info("=" * 64)

    client = TradingKalshiClient()
    if auto_bet and not client.authenticated:
        logger.error(
            "Auto-bet requires KALSHI_API_KEY_ID and KALSHI_PRIVATE_KEY_PATH in .env"
        )
        sys.exit(1)

    already_detected: set[str] = set()  # tickers we already alerted on
    checks = 0

    while True:
        checks += 1
        ts = datetime.now().strftime("%H:%M:%S")
        try:
            found = find_market_for_movie(client, TARGET_MOVIE)
        except Exception as exc:
            logger.warning("[%s] Check #%d — API error: %s", ts, checks, exc)
            found = None

        if found:
            event, market = found
            ticker = market.get("ticker", "")
            yes_bid = market.get("yes_bid")  # in cents
            yes_ask = market.get("yes_ask")  # in cents
            last_price = market.get("last_price")  # in cents
            volume = market.get("volume", 0)
            label = (
                market.get("yes_sub_title")
                or market.get("subtitle")
                or market.get("title")
                or ticker
            )

            if ticker not in already_detected:
                already_detected.add(ticker)
                logger.info("")
                logger.info("🚨🚨🚨  SHIPWRECKED DETECTED!  🚨🚨🚨")
                logger.info("  Market    : %s (%s)", label, ticker)
                logger.info("  YES bid   : %s¢", _fmt(yes_bid))
                logger.info("  YES ask   : %s¢", _fmt(yes_ask))
                logger.info("  Last price: %s¢", _fmt(last_price))
                logger.info("  Volume    : %d", volume)

                effective_price = yes_ask if yes_ask else last_price
                under_threshold = (
                    effective_price is not None
                    and effective_price <= MAX_PRICE_CENTS
                )

                bet_placed = False
                if auto_bet and under_threshold:
                    result = place_yes_bet(client, ticker)
                    bet_placed = result is not None
                elif auto_bet and not under_threshold:
                    logger.info(
                        "  ⚠️  Price %s¢ > %d¢ threshold — skipping bet",
                        _fmt(effective_price),
                        MAX_PRICE_CENTS,
                    )

                notify(
                    title=str(label),
                    price_cents=float(effective_price or 0),
                    ticker=ticker,
                    bet_placed=bet_placed,
                )
                logger.info("")
            else:
                # Already detected, but log current price
                eff = yes_ask if yes_ask else last_price
                logger.info(
                    "[%s] Check #%d — still listed @ %s¢ (vol: %d)",
                    ts,
                    checks,
                    _fmt(eff),
                    volume,
                )
        else:
            logger.info("[%s] Check #%d — %s not on the board yet", ts, checks, TARGET_MOVIE)

        if once:
            if not found:
                logger.info("Single check complete — %s not found.", TARGET_MOVIE)
            break

        time.sleep(interval)


def _fmt(val) -> str:
    """Format a cents value, or '—' if None."""
    if val is None:
        return "—"
    return f"{float(val):.0f}"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> int:
    global MAX_PRICE_CENTS, BET_CONTRACTS, BET_MAX_COST_CENTS

    parser = argparse.ArgumentParser(
        description="Shipwrecked Kalshi Market Monitor",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--bet",
        action="store_true",
        help="Enable auto-betting (YES market order if price < threshold)",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=POLL_INTERVAL,
        help=f"Poll interval in seconds (default: {POLL_INTERVAL})",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Check once and exit — no polling loop",
    )
    parser.add_argument(
        "--threshold",
        type=int,
        default=MAX_PRICE_CENTS,
        help=f"Max YES price in cents for auto-bet (default: {MAX_PRICE_CENTS})",
    )
    parser.add_argument(
        "--contracts",
        type=int,
        default=BET_CONTRACTS,
        help=f"Number of contracts to buy (default: {BET_CONTRACTS})",
    )
    parser.add_argument(
        "--max-cost",
        type=int,
        default=BET_MAX_COST_CENTS,
        help=f"Max total cost in cents for market order (default: {BET_MAX_COST_CENTS})",
    )
    args = parser.parse_args()

    # Allow CLI overrides of module-level constants
    MAX_PRICE_CENTS = args.threshold
    BET_CONTRACTS = args.contracts
    BET_MAX_COST_CENTS = args.max_cost

    run_monitor(interval=args.interval, auto_bet=args.bet, once=args.once)
    return 0


if __name__ == "__main__":
    sys.exit(main())
