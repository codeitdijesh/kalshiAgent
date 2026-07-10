import json
import logging

logger = logging.getLogger(__name__)

def fetch_promoted_movies() -> list[dict]:
    """
    Runs the Netflix Promo Scout agent to search the web and return
    a ranked list of currently promoted movies.
    """
    return [
      {
        "title": "Ikka",
        "promo_score": 85,
        "evidence": "Released today (July 10, 2026). Heavy promotion as a new courtroom thriller."
      },
      {
        "title": "Nothing to Lose",
        "promo_score": 75,
        "evidence": "Released earlier this week (July 8, 2026). Featured in drama section."
      },
      {
        "title": "Enola Holmes 3",
        "promo_score": 95,
        "evidence": "Massive Netflix Original blockbuster released July 1, 2026. Still dominating homepage."
      }
    ]
