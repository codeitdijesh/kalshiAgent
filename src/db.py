import os
from dotenv import load_dotenv
from supabase import create_client, Client

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SECRET_KEY") or os.getenv("SUPABASE_PUBLISHABLE_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    raise ValueError("Missing Supabase credentials in .env file (need SUPABASE_URL and SUPABASE_SECRET_KEY)")

# Initialize the Supabase client
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

def get_client() -> Client:
    return supabase

def get_or_create_source(name: str, type_str: str, description: str = "") -> str:
    # Check if exists
    resp = supabase.table("data_sources").select("id").eq("name", name).execute()
    if resp.data:
        return resp.data[0]["id"]
    
    # Otherwise create
    insert_resp = supabase.table("data_sources").insert({
        "name": name,
        "type": type_str,
        "description": description
    }).execute()
    return insert_resp.data[0]["id"]

