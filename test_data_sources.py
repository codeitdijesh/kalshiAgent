import sys
import os
sys.path.insert(0, './src')
import config as cfg
from data_collector import *
from kalshi_client import KalshiClient
from promo_agent import fetch_promoted_movies
import logging

logging.basicConfig(level=logging.ERROR)

def test_source(name, func, *args):
    print(f"\n--- Testing {name} ---")
    try:
        res = func(*args)
        if res:
            print(f"SUCCESS: {res}")
            return True
        else:
            print(f"WARNING: Returned empty or None")
            return False
    except Exception as e:
        print(f"ERROR: {e}")
        return False

print("1. FlixPatrol Scraper")
scraper = FlixPatrolScraper()
test_source("FlixPatrol", scraper.scrape_daily_top10, __import__('datetime').date.today(), 'united-states', 'Films')

print("\n2. Wikipedia Oracle")
wiki = WikipediaOracle()
test_source("Wikipedia", wiki.get_pageviews, "Old Henry", "Films")

print("\n3. Google Trends Oracle")
trends = GoogleTrendsOracle()
test_source("Google Trends", trends.get_interest, ["Old Henry", "Enola Holmes 3"])

print("\n4. TMDB Oracle")
tmdb = TMDBOracle()
if tmdb.enabled:
    test_source("TMDB", tmdb.get_metadata, "Old Henry", "Films")
else:
    print("WARNING: TMDB is Disabled (No API Key)")

print("\n5. YouTube Oracle")
yt = YouTubeOracle()
if yt.enabled:
    test_source("YouTube", yt.get_trailer_velocity, "Old Henry")
else:
    print("WARNING: YouTube is Disabled (No API Key)")

print("\n6. TikTok Oracle")
tiktok = TikTokOracle()
test_source("TikTok", tiktok.get_tiktok_volume, "Old Henry")

print("\n7. Promo Scout Agent")
test_source("Promo Scout", fetch_promoted_movies)

print("\n8. Kalshi API (Client)")
kalshi = KalshiClient()
# Wait, get_markets doesn't take event_ticker directly, it takes a category or we use find_netflix_event.
# Let's just test get_events
test_source("Kalshi Markets", kalshi.get_events)
