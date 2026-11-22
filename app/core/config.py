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

    # --- KYC module ---
    kyc_enc_key: str = ""            # base64 32-byte AES key; empty → derive from secret_key (dev)
    kyc_hmac_key: str = ""           # HMAC pepper for dedup hashes; empty → derive from secret_key
    kyc_storage_dir: str = "./var/kyc-docs"   # encrypted local storage root (swap to S3 backend later)
    kyc_max_doc_mb: int = 8
    kyc_notice_version: str = "1.0"
    kyc_grievance_email: str = "deepshikapillai@gmail.com"


settings = Settings()
