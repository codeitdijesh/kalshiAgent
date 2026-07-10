from src.db import supabase

def test_connection():
    try:
        print("Testing Supabase connection...")
        # Just query the data_sources table to see if it responds without error
        response = supabase.table("data_sources").select("*").limit(1).execute()
        print("SUCCESS! Connection successful!")
        print(f"Data returned (if any): {response.data}")
    except Exception as e:
        print(f"FAILED: Connection failed: {e}")

if __name__ == "__main__":
    test_connection()
