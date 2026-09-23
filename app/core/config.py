# app/core/config.py
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    mongo_url: str = "mongodb://localhost:27017"
    mongo_db: str = "esg_score_calculator"
    esg_openai_api_key: str = ""
    esg_openai_model: str = "gpt-4.1-mini"
    # The model each provider is asked for. Bedrock names the same models differently, so
    # they are separate settings: whichever provider an admin picks on the dashboard
    # (app/core/llm_settings.py), the right id is sent to the right endpoint.
    esg_bedrock_model: str = "openai.gpt-5.6-luna"
    bfsi_bedrock_model: str = "openai.gpt-5.6-luna"
    # Where Bedrock runs. ap-south-1 keeps inference in India, which matters for a
    # SEBI-licensed rating: the report text never leaves the country.
    bedrock_region: str = "ap-south-1"
    bfsi_openai_api_key: str = ""
    bfsi_openai_model: str = "gpt-4.1-mini"
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
    # With trust_proxy: the client IP is the X-Forwarded-For entry this many places from
    # the right (the address our own proxy appended), not the spoofable leftmost one.
    trusted_proxy_hops: int = 1
    # With trust_proxy: read CF-Connecting-IP. Only safe when the origin accepts traffic
    # from Cloudflare alone; otherwise any client can set that header.
    trust_cloudflare: bool = False
    # Every blog post's byline is this fixed value -- never per-post, never
    # accepted from a client (docs/specs/2026-09-11-blog-feature-design.md).
    blog_author_name: str = "Chawla"
    blog_author_title: str = ""
    blog_author_avatar_url: str = ""


settings = Settings()
