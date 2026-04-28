"""Centralized configuration. Reads from environment + .env file."""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Mode — "paper" or "live". The bot manager honors per-user setting,
    # but this acts as a hard global ceiling for the executor.
    mode: str = "paper"

    # Auth
    jwt_secret: str = "change-me"
    jwt_algorithm: str = "HS256"
    jwt_expires_minutes: int = 60

    # Database
    database_url: str = "sqlite:///./copytrade.db"

    # Bot loop
    bot_poll_interval_seconds: int = 5

    # Polymarket
    polymarket_base_url: str = "https://clob.polymarket.com"
    polymarket_data_api_url: str = "https://data-api.polymarket.com"
    polygon_chain_id: int = 137
    polygon_rpc_url: str = "https://polygon-rpc.com"

    # Wallet encryption — REQUIRED for live mode. Generate with:
    #   python -m src.wallet.crypto generate
    # Refuses to load wallets if unset or left at default.
    master_encryption_key: str = ""

    # Live-trading kill switch. Even if a user's settings.mode == "live",
    # the executor refuses to place orders unless this is True. Flip to False
    # in .env to halt all live trading instantly across all users.
    live_trading_enabled: bool = False

    # Execution
    execution_max_retries: int = 3
    execution_retry_backoff_seconds: float = 1.0

    # Logging
    log_level: str = "INFO"


settings = Settings()
