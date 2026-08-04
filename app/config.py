from functools import cached_property, lru_cache
from ipaddress import IPv4Network, IPv6Network, ip_network
from pathlib import Path
import re
from typing import Literal
from urllib.parse import unquote, urlparse

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "Opinion Onion"
    environment: Literal["development", "test", "production"] = "development"
    database_url: str = "postgresql+psycopg://opinion:opinion@db:5432/opinion"
    secret_key: str = Field(min_length=32)
    session_cookie: str = "opinion_session"
    cookie_secure: bool = False
    allowed_hosts: str = "localhost,127.0.0.1"
    admin_allowed_networks: str = ""
    trusted_proxy_networks: str = ""
    official_clearnet_url: str = ""
    official_onion_url: str = ""
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

    @cached_property
    def admin_networks(self) -> tuple[IPv4Network | IPv6Network, ...]:
        return self._parse_networks(self.admin_allowed_networks, "ADMIN_ALLOWED_NETWORKS")

    @cached_property
    def trusted_proxy_networks_list(self) -> tuple[IPv4Network | IPv6Network, ...]:
        return self._parse_networks(self.trusted_proxy_networks, "TRUSTED_PROXY_NETWORKS")

    @property
    def official_addresses(self) -> list[tuple[str, str]]:
        addresses: list[tuple[str, str]] = []
        if self.official_clearnet_url:
            addresses.append(("Clearnet", self.official_clearnet_url))
        if self.official_onion_url:
            addresses.append(("Onion", self.official_onion_url))
        return addresses

    @staticmethod
    def _parse_networks(
        value: str, label: str
    ) -> tuple[IPv4Network | IPv6Network, ...]:
        try:
            return tuple(
                ip_network(item.strip(), strict=False)
                for item in value.split(",")
                if item.strip()
            )
        except ValueError as exc:
            raise ValueError(f"{label} должен содержать корректные IP/CIDR") from exc

    @model_validator(mode="after")
    def validate_security_configuration(self) -> "Settings":
        if not self.allowed_hosts_list:
            raise ValueError("ALLOWED_HOSTS не может быть пустым")
        admin_networks = self._parse_networks(
            self.admin_allowed_networks, "ADMIN_ALLOWED_NETWORKS"
        )
        trusted_networks = self._parse_networks(
            self.trusted_proxy_networks, "TRUSTED_PROXY_NETWORKS"
        )
        if any(network.prefixlen == 0 for network in trusted_networks):
            raise ValueError("TRUSTED_PROXY_NETWORKS не должен доверять всему интернету")
        self._validate_official_url(self.official_clearnet_url, onion=False)
        self._validate_official_url(self.official_onion_url, onion=True)

        weak_secrets = {
            "replace-with-at-least-32-random-characters",
            "test-secret-key-that-is-at-least-32-characters",
            "change-me-change-me-change-me-change-me",
        }
        if self.environment == "production":
            if self.secret_key.lower() in weak_secrets or len(set(self.secret_key)) < 12:
                raise ValueError("Production SECRET_KEY должен быть случайным и уникальным")
            if not self.cookie_secure:
                raise ValueError("COOKIE_SECURE обязателен в production")
            if any(
                "*" in host or host in {"localhost", "127.0.0.1"}
                for host in self.allowed_hosts_list
            ):
                raise ValueError("Production ALLOWED_HOSTS должен содержать реальные hostnames")
            if any(network.prefixlen == 0 for network in admin_networks):
                raise ValueError("Production ADMIN_ALLOWED_NETWORKS не должен разрешать весь интернет")
            parsed_database = urlparse(self.database_url)
            database_password = unquote(parsed_database.password or "")
            if (
                not parsed_database.scheme.startswith("postgresql")
                or not parsed_database.hostname
                or not parsed_database.username
                or len(database_password) < 12
                or database_password.lower()
                in {"opinion", "postgres", "password", "changeme"}
            ):
                raise ValueError(
                    "Production DATABASE_URL должен указывать на PostgreSQL "
                    "с отдельным сильным паролем"
                )
        return self

    @staticmethod
    def _validate_official_url(value: str, *, onion: bool) -> None:
        if not value:
            return
        parsed = urlparse(value)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
        ):
            raise ValueError("Официальный адрес должен быть абсолютным HTTP(S) URL без credentials")
        if onion:
            if not re.fullmatch(r"[a-z2-7]{56}\.onion", parsed.hostname):
                raise ValueError("OFFICIAL_ONION_URL должен использовать v3 hostname .onion")
        elif parsed.scheme != "https" or parsed.hostname.endswith(".onion"):
            raise ValueError("OFFICIAL_CLEARNET_URL должен использовать HTTPS clearnet hostname")


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
