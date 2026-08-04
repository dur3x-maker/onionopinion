from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "Opinion Onion"
    database_url: str = "postgresql+psycopg://opinion:opinion@db:5432/opinion"
    secret_key: str = Field(min_length=32)
    session_cookie: str = "opinion_session"
    cookie_secure: bool = False
    allowed_hosts: str = "localhost,127.0.0.1"
    posts_per_page: int = 20
    post_max_length: int = 2000
    comment_max_length: int = 1500
    avatar_storage_dir: Path = Path("data/avatars")
    avatar_max_bytes: int = 2 * 1024 * 1024
    avatar_max_pixels: int = 16_000_000
    avatar_size: int = 256
    max_comment_depth: int = 64
    rate_limit_count: int = 8
    rate_limit_window_seconds: int = 60

    @property
    def allowed_hosts_list(self) -> list[str]:
        return [item.strip() for item in self.allowed_hosts.split(",") if item.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
