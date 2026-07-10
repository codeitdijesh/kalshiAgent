import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), 'src')))

import argparse
import logging
from kalshi_client import KalshiMarketAnalyzer

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(message)s")
logger = logging.getLogger("whale_tracker")

def track_event_whales(category: str, limit_per_market: int = 1000, top_n: int = 25):
    analyzer = KalshiMarketAnalyzer()
    logger.info(f"Looking for active Netflix {category} event...")
    event = analyzer.find_netflix_event(category)
    if not event:
        logger.error(f"No active Kalshi event found for {category}")
        return
        
    markets = event.get("markets", [])
    if not markets:
        data = analyzer.client.get_markets(event.get("event_ticker", ""))
        markets = (data or {}).get("markets", [])
        
    if not markets:
        logger.error("No markets found in event.")
        return
        
    logger.info(f"Found event: {event.get('title')} ({len(markets)} contenders)")
    logger.info("Scanning recent trades across all contenders... this might take a moment.")
    
    all_trades = []
    
    for m in markets:
        ticker = m.get("ticker")
        title = m.get("yes_sub_title") or m.get("subtitle") or m.get("title") or ticker
        
        cursor = None
        fetched = 0
        
        while fetched < limit_per_market:
            batch_limit = min(100, limit_per_market - fetched)
            resp = analyzer.client.get_trades(ticker, limit=batch_limit, cursor=cursor)
            
            if not resp or "trades" not in resp:
                break
                
            batch_trades = resp["trades"]
            if not batch_trades:
                break
                
            for t in batch_trades:
                count = float(t.get("count_fp") or t.get("count") or 0)
                price_cents = t.get("price")
                if price_cents is None:
                    price_dollars = float(t.get("yes_price_dollars") or 0)
                    price_cents = price_dollars * 100
                
                dollars_spent = (count * price_cents) / 100.0
                all_trades.append({
                    "ticker": ticker,
                    "title": title,
                    "count": count,
                    "price_cents": price_cents,
                    "dollars_spent": dollars_spent,
                    "side": t.get("taker_side", "unknown"),
                    "time": t.get("created_time", "Unknown Time")
                })
                
            fetched += len(batch_trades)
            cursor = resp.get("cursor")
            if not cursor:
                break
                
    if not all_trades:
        logger.info("No trades found.")
        return
        
    # Sort by dollars spent to find the real whales
    all_trades_sorted = sorted(all_trades, key=lambda x: x["dollars_spent"], reverse=True)
    
    print("\n" + "="*95)
    print(f"  TOP {top_n} WHALE TRADES FOR THIS WEEK'S {category.upper()} MARKET")
    print("="*95)
    print(f"{'RANK':<5} | {'SHOW/MOVIE':<35} | {'SIDE':<4} | {'CONTRACTS':>9} | {'PRICE':>5} | {'DOLLARS SPENT':>13}")
    print("-" * 95)
    for i, w in enumerate(all_trades_sorted[:top_n], 1):
        title = w['title'][:32] + "..." if len(w['title']) > 35 else w['title']
        print(f"#{i:<4} | {title:<35} | {w['side'].upper():<4} | {w['count']:>9.0f} | {w['price_cents']:>4.0f}¢ | ${w['dollars_spent']:>12,.2f}")
    print("="*95 + "\n")

    # --- NEW: SAVE TO DATABASE ---
    try:
        from src.db import supabase, get_or_create_source
        from datetime import datetime, timezone

        logger.info("Saving results to Supabase...")
        source_id = get_or_create_source(
            name="whale_tracker_agent",
            type_str="ai_agent",
            description="Agent that finds large volume trades across Kalshi markets."
        )

        # 1. Save the raw fetch
        raw_payload = {"category": category, "top_n": top_n, "trades": all_trades_sorted[:top_n]}
        fetch_resp = supabase.table("raw_fetches").insert({
            "source_id": source_id,
            "status": "success",
            "raw_payload": raw_payload
        }).execute()
        fetch_id = fetch_resp.data[0]["id"]

        # 2. Save individual events for the frontend
        events_to_insert = []
        for w in all_trades_sorted[:top_n]:
            # Use current time as timestamp since Kalshi time string might need parsing, 
            # but we can try parsing their time if needed, or just use now.
            events_to_insert.append({
                "fetch_id": fetch_id,
                "market_ticker": w["ticker"],
                "event_type": "whale_trade",
                "price": w["price_cents"] / 100.0,
                "volume": int(w["count"]),
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "additional_metadata": {
                    "side": w["side"],
                    "dollars_spent": w["dollars_spent"],
                    "title": w["title"],
                    "kalshi_created_time": w["time"]
                }
            })

        if events_to_insert:
            supabase.table("market_events").insert(events_to_insert).execute()
            
        logger.info(f"✅ Successfully saved {len(events_to_insert)} whale trades to the database!")
    except Exception as e:
        logger.error(f"Failed to save to database: {e}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Track the biggest whale trades across an entire Netflix event.")
    parser.add_argument("--category", choices=["TV", "Films"], default="Films", help="Which category to scan (TV or Films)")
    parser.add_argument("--top", type=int, default=25, help="Number of top trades to show")
    args = parser.parse_args()
    
    track_event_whales(args.category, top_n=args.top)
