from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # localhost = depuis ton PC ; dans Docker, DATABASE_URL pointe vers "db"
    database_url: str = "postgresql+psycopg://emission:emission@localhost:5432/emission"

    # En production, JWT_SECRET doit venir d'une variable d'environnement secrète
    jwt_secret: str = "dev-only-secret-change-me-in-production-0123456789"
    jwt_algorithm: str = "HS256"
    access_token_minutes: int = 15
    refresh_token_days: int = 7


settings = Settings()