from supabase import create_client, Client
from api.config import settings

def get_supabase_client() -> Client:
    if not settings.supabase_url or not settings.supabase_service_role_key:
        raise ValueError("Supabase URL or Service Role Key missing in config")
    return create_client(settings.supabase_url, settings.supabase_service_role_key)
