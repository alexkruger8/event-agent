from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Application configuration loaded from environment variables.
    """

    # ----- App -----

    app_name: str = "ai-event-intelligence"
    environment: str = "development"
    debug: bool = True

    # ----- Database -----

    database_url: str = "postgresql+psycopg://postgres:postgres@localhost:5432/events"

    # ----- LLM -----

    anthropic_api_key: str | None = None
    anthropic_model: str = "claude-opus-4-6"

    openai_api_key: str | None = None
    openai_model: str = "gpt-4o"

    @property
    def llm_configured(self) -> bool:
        return bool(self.anthropic_api_key or self.openai_api_key)

    # ----- Slack -----

    slack_bot_token: str | None = None
    slack_signing_secret: str | None = None
    slack_alert_channel: str | None = None

    # ----- Twilio / SMS -----

    twilio_account_sid: str | None = None
    twilio_auth_token: str | None = None
    twilio_from_number: str | None = None

    # ----- ngrok -----

    ngrok_authtoken: str | None = None
    ngrok_domain: str | None = None

    # ----- Security / API -----

    api_key: str | None = None           # set this to enable auth; if unset, all routes are open
    api_key_header: str = "X-API-Key"

    # ----- Worker Config -----

    metric_window_minutes: int = 60
    anomaly_threshold_stddev: float = 3.0
    baseline_lookback_days: int = 28   # 4 weeks — enough for 4 samples per weekly slot
    baseline_min_samples: int = 4      # minimum samples per seasonal slot
    anomaly_cooldown_hours: int = 24   # suppress repeat alerts for the same metric within this window

    trend_window_hours: int = 6                  # how many hours of metric history to regress over
    trend_min_samples: int = 3                   # minimum data points needed to fit a trend
    trend_change_threshold_pct: float = 10.0     # minimum |% change per hour| relative to mean to flag

    # ----- Kafka / Redpanda -----

    kafka_bootstrap_servers: str | None = None
    kafka_consumer_group_prefix: str | None = None  # defaults to "ai-events" in consumer
    kafka_topic_refresh_interval_seconds: int = 60
    kafka_session_timeout_ms: int = 10_000
    kafka_auto_offset_reset: str = "latest"
    kafka_credential_encryption_key: str | None = None

    # ----- MCP -----

    mcp_database_url: str | None = None   # read-only DB user; falls back to database_url if unset
    mcp_api_key: str | None = None
    mcp_default_tenant_id: str | None = None
    mcp_max_results: int = 50

    # ----- Scheduler -----

    pipeline_schedule_minutes: int = 15          # how often to run the full pipeline across all tenants

    # ----- Seed / demo data -----

    include_demo_tenant: bool = True
    publish_demo_kafka_events: bool = False

    # ----- Pydantic config -----

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )


@lru_cache
def get_settings() -> Settings:
    """
    Cached settings instance.
    Prevents reloading env variables repeatedly.
    """
    return Settings()


settings = get_settings()
