"""
Kalshi market-data client + market analysis for the Master Agent.

Public market-data endpoints (markets, events, orderbooks) require **no
API key**, so live quotes work immediately. Authenticated endpoints
(balance, positions, orders) activate automatically once
``KALSHI_API_KEY_ID`` and ``KALSHI_PRIVATE_KEY_PATH`` are set in the
environment (RSA-PSS request signing, per Kalshi API v2).

Two layers:

* :class:`KalshiClient` — thin fail-safe HTTP wrapper over the Kalshi
  Trade API v2 (``/events``, ``/markets``, ``/markets/{t}/orderbook``).
* :class:`KalshiMarketAnalyzer` — finds the live Netflix weekly-#1 event,
  fuzzy-matches our FlixPatrol contender titles to market outcomes, and
  computes the full market analysis: bid/ask, implied probability,
  spread, volume/open-interest, our edge vs the ask, estimated fees,
  expected value per contract, and fractional-Kelly sizing at the real
  ask price.

Everything is fail-safe: any network/parse failure degrades to ``None``
so the agent falls back to its assumed entry price rather than crashing.
"""

from __future__ import annotations

import base64
import difflib
import json
import logging
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

import requests

import config as cfg

logger = logging.getLogger("kalshi_client")


# ---------------------------------------------------------------------------
# Title normalisation / fuzzy matching
# ---------------------------------------------------------------------------

_SEASON_RE = re.compile(
    r"\b(season|series|part|volume|vol\.?|chapter)\s*\d+\b|\bs\d{1,2}\b", re.I
)
_PUNCT_RE = re.compile(r"[^a-z0-9 ]+")


def normalize_title(title: str) -> str:
    """Lowercase, strip season/part suffixes and punctuation for matching."""
    t = title.lower()
    t = _SEASON_RE.sub(" ", t)
    t = _PUNCT_RE.sub(" ", t)
    return " ".join(t.split())


def title_similarity(a: str, b: str) -> float:
    """Similarity in [0, 1] between two titles (normalised)."""
    na, nb = normalize_title(a), normalize_title(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    # Containment (e.g. "Wednesday" vs "Wednesday: Season 2") counts high
    if na in nb or nb in na:
        return 0.95
    return difflib.SequenceMatcher(None, na, nb).ratio()


# ---------------------------------------------------------------------------
# Data containers
# ---------------------------------------------------------------------------

@dataclass
class MarketQuote:
    """Live quote for one Kalshi market (one candidate title)."""

    ticker: str
    market_title: str          # Kalshi's outcome label (yes_sub_title/title)
    matched_contender: Optional[str]  # our FlixPatrol title, if matched
    match_score: float
    yes_bid: Optional[float]   # dollars, 0-1
    yes_ask: Optional[float]
    last_price: Optional[float]
    implied_prob: Optional[float]  # mid of bid/ask
    spread: Optional[float]
    volume: int = 0
    open_interest: int = 0
    liquidity: Optional[float] = None
    close_time: str = ""
    status: str = ""
    top_5_bets: list[float] = field(default_factory=list)

@dataclass
class MarketAnalysis:
    """Full market analysis for one prediction cycle."""

    event_ticker: str
    event_title: str
    category: str
    fetched_at: str
    quotes: list[MarketQuote] = field(default_factory=list)
    # Winner-specific analysis (None when winner has no matched market)
    winner_ticker: Optional[str] = None
    winner_ask: Optional[float] = None
    winner_bid: Optional[float] = None
    winner_implied_prob: Optional[float] = None
    our_prob: Optional[float] = None
    edge: Optional[float] = None            # our_prob - ask
    fee_per_contract: Optional[float] = None
    ev_per_contract: Optional[float] = None  # after fees
    kelly_contracts: Optional[int] = None
    kelly_bet_dollars: Optional[float] = None
    # NO-side: fade the market favorite when it isn't our predicted winner.
    # P(favorite loses) ≥ our_prob is a conservative lower bound, since our
    # winner winning is only one of the ways the favorite loses.
    fade_ticker: Optional[str] = None
    fade_title: Optional[str] = None         # the overpriced favorite we'd buy NO on
    fade_no_price: Optional[float] = None    # cost of one NO contract = 1 − yes_bid
    fade_edge: Optional[float] = None
    fade_fee: Optional[float] = None
    fade_ev: Optional[float] = None
    fade_contracts: Optional[int] = None
    fade_bet_dollars: Optional[float] = None
    best_side: Optional[str] = None          # "yes" | "no" — higher-EV tradeable side
    tradeable: bool = False                  # best side clears KALSHI_MIN_EDGE and EV > 0
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        return d


# ---------------------------------------------------------------------------
# HTTP client
# ---------------------------------------------------------------------------

class KalshiClient:
    """Fail-safe wrapper over the Kalshi Trade API v2."""

    def __init__(
        self,
        base_url: str = cfg.KALSHI_API_BASE,
        api_key_id: str = cfg.KALSHI_API_KEY_ID,
        private_key_path: str = cfg.KALSHI_PRIVATE_KEY_PATH,
        timeout: int = 20,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._session = requests.Session()
        self._session.headers.update({"Accept": "application/json"})
        self._api_key_id = api_key_id
        self._private_key = None
        if api_key_id and private_key_path:
            self._private_key = self._load_private_key(private_key_path)
            if self._private_key:
                logger.info("Kalshi authenticated mode enabled (key %s…)", api_key_id[:8])

    # -- public market data (no auth required) --------------------------

    def get_events(
        self,
        status: str = "open",
        series_ticker: Optional[str] = None,
        with_nested_markets: bool = True,
        limit: int = 200,
        cursor: Optional[str] = None,
    ) -> Optional[dict]:
        params: dict[str, Any] = {
            "status": status,
            "limit": limit,
            "with_nested_markets": str(with_nested_markets).lower(),
        }
        if series_ticker:
            params["series_ticker"] = series_ticker
        if cursor:
            params["cursor"] = cursor
        return self._get("/events", params)

    def get_event(self, event_ticker: str) -> Optional[dict]:
        return self._get(f"/events/{event_ticker}", {"with_nested_markets": "true"})

    def get_markets(self, event_ticker: str) -> Optional[dict]:
        return self._get("/markets", {"event_ticker": event_ticker, "limit": 100})

    def get_market(self, ticker: str) -> Optional[dict]:
        return self._get(f"/markets/{ticker}")

    def get_orderbook(self, ticker: str, depth: int = 10) -> Optional[dict]:
        return self._get(f"/markets/{ticker}/orderbook", {"depth": depth})

    def get_trades(self, ticker: str, limit: int = 100, cursor: Optional[str] = None) -> Optional[dict]:
        """Get completed trades for a specific market to track whale volume."""
        params: dict[str, Any] = {"ticker": ticker, "limit": limit}
        if cursor:
            params["cursor"] = cursor
        return self._get(f"/markets/trades", params)

    # -- authenticated endpoints (activate when key is configured) ------

    @property
    def authenticated(self) -> bool:
        return self._private_key is not None

    def get_balance(self) -> Optional[dict]:
        """Portfolio balance in cents. Requires API key."""
        return self._get("/portfolio/balance", auth=True)

    def get_positions(self) -> Optional[dict]:
        """Open positions. Requires API key."""
        return self._get("/portfolio/positions", auth=True)

    # -- internals -------------------------------------------------------

    def _get(
        self, path: str, params: Optional[dict] = None, auth: bool = False
    ) -> Optional[dict]:
        url = self.base_url + path
        headers = {}
        if auth:
            if not self.authenticated:
                logger.warning("Kalshi auth endpoint %s requested without API key", path)
                return None
            headers = self._auth_headers("GET", path)
        for attempt in range(1, cfg.MAX_RETRIES + 1):
            try:
                resp = self._session.get(
                    url, params=params, headers=headers, timeout=self.timeout
                )
                if resp.status_code == 429:
                    time.sleep(1.5 * attempt)
                    continue
                resp.raise_for_status()
                return resp.json()
            except Exception as exc:  # noqa: BLE001 — fail-safe by design
                logger.warning(
                    "Kalshi GET %s failed (attempt %d/%d): %s",
                    path, attempt, cfg.MAX_RETRIES, exc,
                )
                time.sleep(1.0 * attempt)
        return None

    def _load_private_key(self, path: str):
        try:
            from cryptography.hazmat.primitives import serialization

            key_bytes = Path(path).read_bytes()
            return serialization.load_pem_private_key(key_bytes, password=None)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not load Kalshi private key from %s: %s", path, exc)
            return None

    def _auth_headers(self, method: str, path: str) -> dict[str, str]:
        """Kalshi API v2 request signing: RSA-PSS over ts + method + path."""
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding

        ts = str(int(time.time() * 1000))
        msg = (ts + method + "/trade-api/v2" + path).encode()
        signature = self._private_key.sign(
            msg,
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.DIGEST_LENGTH,
            ),
            hashes.SHA256(),
        )
        return {
            "KALSHI-ACCESS-KEY": self._api_key_id,
            "KALSHI-ACCESS-SIGNATURE": base64.b64encode(signature).decode(),
            "KALSHI-ACCESS-TIMESTAMP": ts,
        }


# ---------------------------------------------------------------------------
# Market analyzer — Netflix weekly-#1 event discovery + edge analysis
# ---------------------------------------------------------------------------

_CATEGORY_KEYWORDS = {
    cfg.CATEGORY_TV: ("show", "series", "tv"),
    cfg.CATEGORY_FILMS: ("movie", "film"),
}

_CACHE_FILE = cfg.DATA_DIR / "kalshi_event_cache.json"


class KalshiMarketAnalyzer:
    """Find the live Netflix weekly market and analyse it against our signal."""

    def __init__(self, client: Optional[KalshiClient] = None) -> None:
        self.client = client or KalshiClient()

    # -- event discovery -------------------------------------------------

    def find_netflix_event(self, category: str) -> Optional[dict]:
        """Return the open Netflix weekly event (with nested markets).

        Order of attack:
        1. Known series tickers from config (fast path).
        2. Cached event/series ticker from a previous discovery.
        3. Full paginated scan of open events for 'netflix' in the title.
        """
        for series in cfg.KALSHI_NETFLIX_SERIES.get(category, []):
            event = self._event_from_series(series, category)
            if event:
                self._save_cache(category, series)
                return event

        cached = self._load_cache().get(category)
        if cached:
            event = self._event_from_series(cached, category)
            if event:
                return event

        return self._scan_for_event(category)

    def _event_from_series(self, series_ticker: str, category: str) -> Optional[dict]:
        data = self.client.get_events(series_ticker=series_ticker)
        events = (data or {}).get("events") or []
        if not events:
            return None
        # Kalshi lists next week's event before the current one closes, so
        # several may be open at once. Prefer the one that is actually
        # trading (quotes/volume on its markets); tie-break on the earliest
        # market close time (= the currently active week).
        def sort_key(event: dict) -> tuple:
            markets = event.get("markets") or []
            has_activity = any(
                (m.get("yes_bid") or 0) > 0
                or (m.get("volume") or 0) > 0
                or (m.get("open_interest") or 0) > 0
                for m in markets
            )
            close = min(
                (str(m.get("close_time") or "9999") for m in markets),
                default="9999",
            )
            return (not has_activity, close)

        return sorted(events, key=sort_key)[0]

    def _scan_for_event(self, category: str, max_pages: int = 15) -> Optional[dict]:
        keywords = _CATEGORY_KEYWORDS.get(category, ())
        cursor = None
        for _ in range(max_pages):
            data = self.client.get_events(cursor=cursor)
            if not data:
                return None
            for event in data.get("events", []):
                title = (event.get("title") or "").lower()
                if "netflix" in title and any(k in title for k in keywords):
                    series = event.get("series_ticker", "")
                    if series:
                        self._save_cache(category, series)
                    logger.info("Discovered Netflix event: %s (%s)",
                                event.get("event_ticker"), event.get("title"))
                    return event
            cursor = data.get("cursor")
            if not cursor:
                return None
        return None

    @staticmethod
    def _matches_category(title: str, category: str) -> bool:
        title = title.lower()
        return "netflix" in title and any(
            k in title for k in _CATEGORY_KEYWORDS.get(category, ())
        )

    # -- analysis ---------------------------------------------------------

    def analyze(
        self,
        contenders: list[str],
        predicted_winner: str,
        our_prob: float,
        bankroll: float,
        category: str = cfg.CATEGORY_TV,
    ) -> Optional[MarketAnalysis]:
        """Full market analysis: quotes for all contenders + winner edge/EV."""
        event = self.find_netflix_event(category)
        if not event:
            logger.warning("No open Kalshi Netflix event found for %s", category)
            return None

        markets = event.get("markets")
        if not markets:
            data = self.client.get_markets(event.get("event_ticker", ""))
            markets = (data or {}).get("markets") or []
        if not markets:
            logger.warning("Kalshi event %s has no markets", event.get("event_ticker"))
            return None

        analysis = MarketAnalysis(
            event_ticker=event.get("event_ticker", ""),
            event_title=event.get("title", ""),
            category=category,
            fetched_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        )

        titles_of_interest = list(dict.fromkeys([predicted_winner] + contenders))
        for market in markets:
            quote = self._quote_from_market(market, titles_of_interest)
            analysis.quotes.append(quote)

        # Sort: matched contenders first, then by implied probability
        analysis.quotes.sort(
            key=lambda q: (q.matched_contender is None, -(q.implied_prob or 0)),
        )

        self._analyze_winner(analysis, predicted_winner, our_prob, bankroll)
        return analysis

    def _quote_from_market(
        self, market: dict, titles_of_interest: list[str]
    ) -> MarketQuote:
        label = (
            market.get("yes_sub_title")
            or market.get("subtitle")
            or market.get("title")
            or market.get("ticker", "")
        )
        best_match, best_score = None, 0.0
        for title in titles_of_interest:
            score = title_similarity(title, label)
            if score > best_score:
                best_match, best_score = title, score
        if best_score < cfg.KALSHI_TITLE_MATCH_THRESHOLD:
            best_match = None

        yes_bid = _cents(market.get("yes_bid"))
        yes_ask = _cents(market.get("yes_ask"))
        implied = None
        if yes_bid is not None and yes_ask is not None and (yes_bid or yes_ask):
            implied = round((yes_bid + yes_ask) / 2, 4)

        ticker = market.get("ticker", "")
        
        top_bets = []
        try:
            cursor = None
            dollar_bets = []
            while True:
                trades_resp = self.client.get_trades(ticker, limit=1000, cursor=cursor)
                if not trades_resp or "trades" not in trades_resp:
                    break
                batch = trades_resp.get("trades", [])
                if not batch:
                    break
                for t in batch:
                    count = float(t.get("count_fp") or t.get("count") or 0)
                    price_cents = t.get("price")
                    if price_cents is None:
                        price_dollars = float(t.get("yes_price_dollars") or 0)
                        price_cents = price_dollars * 100
                    money = (count * price_cents) / 100.0
                    dollar_bets.append(money)
                cursor = trades_resp.get("cursor")
                if not cursor:
                    break
            dollar_bets.sort(reverse=True)
            top_bets = dollar_bets[:5]
        except Exception:
            pass

        return MarketQuote(
            ticker=ticker,
            market_title=str(label),
            matched_contender=best_match,
            match_score=round(best_score, 3),
            yes_bid=yes_bid,
            yes_ask=yes_ask,
            last_price=_cents(market.get("last_price")),
            implied_prob=implied,
            spread=(
                round(yes_ask - yes_bid, 4)
                if yes_bid is not None and yes_ask is not None
                else None
            ),
            volume=int(market.get("volume") or 0),
            open_interest=int(market.get("open_interest") or 0),
            liquidity=_cents(market.get("liquidity")),
            close_time=str(market.get("close_time") or ""),
            status=str(market.get("status") or ""),
            top_5_bets=top_bets,
        )

    def _analyze_winner(
        self,
        analysis: MarketAnalysis,
        predicted_winner: str,
        our_prob: float,
        bankroll: float,
    ) -> None:
        analysis.our_prob = round(our_prob, 4)

        # --- YES side: buy YES on our predicted winner at the ask ---
        yes_ok = False
        winner_quote = next(
            (q for q in analysis.quotes if q.matched_contender == predicted_winner),
            None,
        )
        if winner_quote is None:
            analysis.notes.append(
                f"Predicted winner '{predicted_winner}' has no matching Kalshi "
                f"market — no YES-side edge."
            )
        else:
            analysis.winner_ticker = winner_quote.ticker
            analysis.winner_bid = winner_quote.yes_bid
            analysis.winner_ask = winner_quote.yes_ask
            analysis.winner_implied_prob = winner_quote.implied_prob
            ask = winner_quote.yes_ask
            if ask is None or ask <= 0 or ask >= 1:
                analysis.notes.append("No executable YES ask price.")
            else:
                edge, fee, ev, contracts, bet = self._side_metrics(
                    our_prob, ask, bankroll
                )
                analysis.edge = edge
                analysis.fee_per_contract = fee
                analysis.ev_per_contract = ev
                analysis.kelly_contracts = contracts
                analysis.kelly_bet_dollars = bet
                yes_ok = edge >= cfg.KALSHI_MIN_EDGE and ev > 0
                analysis.notes.append(
                    f"YES '{predicted_winner}': P={our_prob:.2f} vs ask {ask:.2f} "
                    f"→ edge {edge:+.2f}, EV ${ev:+.3f}/contract."
                )

        # --- NO side: fade the market favorite when it isn't our pick ---
        # The Weekend Effect thesis says the mid-week leader is overpriced;
        # NO on the favorite wins whenever ANYONE else wins.
        no_ok = False
        favorite = max(
            (
                q for q in analysis.quotes
                if q.implied_prob is not None and q.yes_bid
            ),
            key=lambda q: q.implied_prob,
            default=None,
        )
        if favorite is not None and favorite.matched_contender != predicted_winner:
            no_price = round(1.0 - favorite.yes_bid, 4)  # cost to buy NO
            if 0 < no_price < 1:
                edge, fee, ev, contracts, bet = self._side_metrics(
                    our_prob, no_price, bankroll
                )
                analysis.fade_ticker = favorite.ticker
                analysis.fade_title = favorite.market_title
                analysis.fade_no_price = no_price
                analysis.fade_edge = edge
                analysis.fade_fee = fee
                analysis.fade_ev = ev
                analysis.fade_contracts = contracts
                analysis.fade_bet_dollars = bet
                no_ok = edge >= cfg.KALSHI_MIN_EDGE and ev > 0
                analysis.notes.append(
                    f"NO '{favorite.market_title}' (market favorite): "
                    f"P(loses)≥{our_prob:.2f} vs NO cost {no_price:.2f} "
                    f"→ edge {edge:+.2f}, EV ${ev:+.3f}/contract."
                )

        # --- Pick the better tradeable side by EV ---
        if yes_ok and no_ok:
            analysis.best_side = (
                "no" if (analysis.fade_ev or 0) > (analysis.ev_per_contract or 0)
                else "yes"
            )
        elif yes_ok:
            analysis.best_side = "yes"
        elif no_ok:
            analysis.best_side = "no"
        analysis.tradeable = analysis.best_side is not None
        analysis.notes.append(
            f"Best side: {analysis.best_side or 'NONE'} → "
            f"{'TRADE' if analysis.tradeable else 'NO TRADE'} "
            f"(min edge {cfg.KALSHI_MIN_EDGE:.2f})."
        )

    @staticmethod
    def _side_metrics(
        prob: float, price: float, bankroll: float
    ) -> tuple[float, float, float, int, float]:
        """Edge, fee, EV, quarter-Kelly contracts and dollars for one side.

        *prob* is our probability the purchased contract pays $1; *price*
        is what one contract costs (YES ask, or 1 − yes_bid for NO).
        """
        edge = round(prob - price, 4)
        fee = round(cfg.KALSHI_FEE_RATE * price * (1 - price), 4)
        ev = round(prob - price - fee, 4)
        b = (1 - price) / price
        kelly_f = max((b * prob - (1 - prob)) / b, 0.0) * cfg.KELLY_FRACTION
        bet = round(bankroll * kelly_f, 2)
        contracts = min(int(bet / price), cfg.KALSHI_MAX_POSITION)
        return edge, fee, ev, contracts, bet

    # -- cache -------------------------------------------------------------

    @staticmethod
    def _load_cache() -> dict:
        try:
            return json.loads(_CACHE_FILE.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return {}

    def _save_cache(self, category: str, series_ticker: str) -> None:
        cache = self._load_cache()
        if cache.get(category) == series_ticker:
            return
        cache[category] = series_ticker
        try:
            _CACHE_FILE.write_text(json.dumps(cache, indent=2), encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass


def _cents(value: Any) -> Optional[float]:
    """Kalshi prices arrive as integer cents; convert to 0-1 dollars."""
    if value is None:
        return None
    try:
        return round(float(value) / 100.0, 4)
    except (TypeError, ValueError):
        return None
