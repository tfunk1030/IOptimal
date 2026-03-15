import os
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    supabase_url: str = os.getenv("SUPABASE_URL", "")
    supabase_anon_key: str = os.getenv("SUPABASE_ANON_KEY", "")
    supabase_service_role_key: str = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
    ioptimal_api_port: int = int(os.getenv("IOPTIMAL_API_PORT", 8000))
    ioptimal_solver_path: str = os.getenv("IOPTIMAL_SOLVER_PATH", "../")

    class Config:
        env_file = ".env"

settings = Settings()
