# app/core/config.py
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    mongo_url: str = "mongodb://localhost:27017"
    mongo_db: str = "esg_score_calculator"
    esg_openai_api_key: str = ""
    esg_openai_model: str = "gpt-3.5-turbo-1106"
    bfsi_openai_api_key: str = ""
    bfsi_openai_model: str = "gpt-4o-mini"
    session_secret: str = "dev-insecure-change-me"
    session_ttl_hours: int = 8
    upload_dir: Path = Path("./uploads")
    smtp_host: str = ""
    smtp_port: int = 465
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_ssl: bool = True
    mail_from: str = "info@esgratings.co.in"
    team_email: str = "info@esgratings.co.in"
    web_origin: str = "http://localhost:3000"
    trust_proxy: bool = False


settings = Settings()
