import json
from functools import cached_property

from google.adk.agents import LlmAgent
from google.adk.models import Gemini
from google.genai import Client
from google.adk.tools import agent_tool
from google.adk.tools.google_search_tool import GoogleSearchTool
from google.adk.tools import url_context


class GlobalGemini(Gemini):
    """Pins the Vertex AI client to the `global` location.

    gemini-3 series models are only served from `global`; the default ADK
    `Gemini` integration constructs a `google.genai.Client` whose location
    defaults to the AgentEngine instance's region (e.g. `us-central1`) and
    fails with model-not-found for these models. Subclassing per the override
    pattern documented on `google.adk.models.google_llm.Gemini` lets the agent
    keep running in its regional AgentEngine instance while routing the model
    request to the global endpoint.
    """

    @cached_property
    def api_client(self) -> Client:
        return Client(vertexai=True, location="global")


kalshi_predictor_google_search_agent = LlmAgent(
    name='Kalshi_Predictor_google_search_agent',
    model=GlobalGemini(model='gemini-3.1-pro-preview'),
    description=(
        'Agent specialized in performing Google searches.'
    ),
    sub_agents=[],
    instruction='Use the GoogleSearchTool to find information on the web.',
    tools=[
        GoogleSearchTool()
    ],
)
kalshi_predictor_url_context_agent = LlmAgent(
    name='Kalshi_Predictor_url_context_agent',
    model=GlobalGemini(model='gemini-3.1-pro-preview'),
    description=(
        'Agent specialized in fetching content from URLs.'
    ),
    sub_agents=[],
    instruction='Use the UrlContextTool to retrieve content from provided URLs.',
    tools=[
        url_context
    ],
)


root_agent = LlmAgent(
    name='Kalshi_Predictor',
    model=GlobalGemini(model='gemini-3.1-pro-preview'),
    description=(
        'This agent will act as the final decision-maker in the pipeline, synthesizing our quantitative oracle data with its own qualitative web research to make the ultimate trade decision.\n\n## 1. Agent Configuration\n\n*   **Name:** Kalshi Oracle Synthesizer\n*   **Role:** Elite quantitative analyst and prediction market specialist.\n*   **Capabilities Required:** Web Search (to find breaking news, Netflix release schedules, and social sentiment).\n*   **Input:** JSON payload containing the week\'s contenders and all collected oracle data.\n*   **Output:** Strict JSON containing the predicted winner, confidence score, and reasoning.'
    ),
    sub_agents=[],
    instruction='\n```text\nYou are the Kalshi Oracle Synthesizer, an elite quantitative analyst and prediction market specialist. Your sole objective is to predict which movie will be #1 on the official Netflix US Weekly Top 10 chart (published every Tuesday) and generate a confidence score for your prediction.\n\nYou will be provided with a JSON payload containing data for the top movie contenders. This data includes daily FlixPatrol rankings, TikTok view velocity, Wikipedia pageviews, Google Trends interest, TMDB release data, and YouTube trailer views.\n\n### Your Core Strategy: The Weekend Effect\nNetflix calculates its weekly chart based on total hours viewed from Monday to Sunday. Because casual viewers binge movies on weekends, the movies that dominate Saturday and Sunday almost always win the entire week, even if another movie held the #1 spot from Monday to Friday.\n\n### Rules of Engagement:\n1.  **Analyze the Provided Data**: Weigh the weekend FlixPatrol rankings heavily. Look for explosive velocity in TikTok views and YouTube trailer views. Note if a movie is a fresh release (premiered within the current Netflix week), as fresh releases have massive weekend spikes. Check `youtube_recent_promos` and `web_scouted_promos` in the context to see if Netflix is heavily promoting a specific title.\n2.  **Invoke Web Search**: You MUST use your web search tool if the quantitative data is tightly contested. Search for:\n    *   "Netflix [Movie Title] release date" to confirm if it just dropped.\n    *   "[Movie Title] Netflix news" to check for viral trends, controversies, or massive promotional pushes.\n    *   "Netflix top 10 movies right now" to gauge real-world sentiment.\n3.  **Synthesize and Decide**: Combine the hard data provided in the payload with your web research context. Choose the single most likely winner.\n4.  **Confidence Scoring**: Assign a confidence score between 0.05 and 0.95.\n    *   0.85 - 0.95: Absolute dominance on weekends, explosive TikTok/YouTube metrics, verified by news.\n    *   0.65 - 0.84: Strong weekend showing, solid supporting metrics.\n    *   0.45 - 0.64: Close race, conflicting metrics, highly dependent on release timing.\n5.  **Output Format**: You must respond in STRICT JSON format matching the schema below. Do not include markdown formatting or conversational text outside the JSON object.\n\n{\n  "predicted_winner": "Exact Title of the Movie",\n  "confidence": 0.85,\n  "reasoning": "A concise, 3-4 sentence explanation of why this movie will win. Cite specific data points from the payload (e.g., TikTok dominance, Weekend FlixPatrol rank) and any context discovered via web search."\n}\n```\n\n---\n\n## 3. Data Payload Schema (What you will send to the Agent)\n\nWhen you call the AI agent\'s endpoint, provide the collected data in the following JSON structure so the agent can parse it easily. \n\n```json\n{\n  "context": {\n    "netflix_week_start": "2026-07-06",\n    "current_date": "2026-07-12",\n    "category": "Films",\n    "youtube_recent_promos": ["Movie X Trailer", "Movie Y Teaser"],\n    "web_scouted_promos": [
      {
        "title": "Movie X",
        "promo_score": 95,
        "evidence": "Featured on Tudum homepage, heavy US marketing"
      }
    ]\n  },\n  "contenders": [\n    {\n      "title": "Example Movie A",\n      "flixpatrol_data": {\n        "weekend_rank_avg": 1.0,\n        "weekday_rank_avg": 3.4,\n        "days_at_number_one": 2\n      },\n      "oracle_data": {\n        "tiktok_weekly_views": 4500000,\n        "wikipedia_7d_pageviews": 125000,\n        "google_trends_score": 88,\n        "youtube_trailer_views_per_day": 850000,\n        "tmdb_release_date": "2026-07-10",\n        "tmdb_runtime_min": 115\n      }\n    },\n    {\n      "title": "Example Movie B",\n      "flixpatrol_data": {\n        "weekend_rank_avg": 2.5,\n        "weekday_rank_avg": 1.2,\n        "days_at_number_one": 4\n      },\n      "oracle_data": {\n        "tiktok_weekly_views": 1200000,\n        "wikipedia_7d_pageviews": 95000,\n        "google_trends_score": 72,\n        "youtube_trailer_views_per_day": 300000,\n        "tmdb_release_date": "2026-06-25",\n        "tmdb_runtime_min": 130\n      }\n    }\n  ]\n}\n```',
    tools=[
        agent_tool.AgentTool(agent=kalshi_predictor_google_search_agent),
        agent_tool.AgentTool(agent=kalshi_predictor_url_context_agent)
    ],
)

def run_llm_prediction(payload: dict) -> dict:
    """
    Run the Kalshi_Predictor agent with the given payload.
    Extract and return the JSON response.
    """
    prompt = f"Please analyze the following data and predict the winner:\n\n{json.dumps(payload, indent=2)}"
    
    # We call the root_agent. Depending on ADK version, it might be .run or just __call__
    try:
        response = root_agent(prompt)
        text_response = response.text if hasattr(response, 'text') else str(response)
    except Exception as e:
        # Fallback if __call__ fails
        if hasattr(root_agent, 'run'):
            response = root_agent.run(prompt)
            text_response = response.text if hasattr(response, 'text') else str(response)
        else:
            raise e
            
    # Clean up JSON formatting from markdown code blocks
    text_response = text_response.strip()
    if text_response.startswith('```json'):
        text_response = text_response[7:]
    elif text_response.startswith('```'):
        text_response = text_response[3:]
    if text_response.endswith('```'):
        text_response = text_response[:-3]
    
    return json.loads(text_response.strip())
