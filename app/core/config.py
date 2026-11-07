import os

from pydantic_settings import BaseSettings, SettingsConfigDict

_app_env = os.getenv("DIRECTHOME_APP_ENV", "development")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", f".env.{_app_env}"),
        env_prefix="DIRECTHOME_",
        extra="ignore",
    )

    database_url: str = "postgresql+psycopg://postgres:root@localhost:5432/directhome"
    app_env: str = "development"
    secret_key: str = "change-me-in-production"
    cors_origins: str = "http://localhost:3000,https://directhome-jet.vercel.app"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 30
    refresh_token_expire_days: int = 30


settings = Settings()
