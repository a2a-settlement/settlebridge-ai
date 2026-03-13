from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    APP_NAME: str = "SettleBridge"
    APP_URL: str = "http://localhost:8000"
    SECRET_KEY: str = "change-me"
    DEBUG: bool = True

    DATABASE_URL: str = "postgresql+asyncpg://settlebridge:settlebridge@localhost:5432/settlebridge"

    A2A_EXCHANGE_URL: str = "https://exchange.a2a-settlement.org"

    MEDIATOR_URL: str = "https://mediator.a2a-settlement.org"
    MEDIATOR_WEBHOOK_SECRET: str = ""

    JWT_SECRET: str = "change-me"
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRATION_HOURS: int = 24

    ANTHROPIC_API_KEY: str = ""
    ASSIST_MODEL: str = "claude-opus-4-0-20250514"
    ASSIST_MAX_TURNS: int = 10
    ASSIST_MAX_TOKENS: int = 4096

    MARKETPLACE_ENABLED: bool = False

    # Gateway
    REDIS_URL: str = "redis://localhost:6379"
    GATEWAY_EXCHANGE_URL: str = ""
    POLICY_RELOAD_INTERVAL_S: int = 30
    REPUTATION_CACHE_TTL_S: int = 300
    HEALTH_CHECK_INTERVAL_S: int = 60
    GATEWAY_ENABLED: bool = True

    @property
    def effective_exchange_url(self) -> str:
        return self.GATEWAY_EXCHANGE_URL or self.A2A_EXCHANGE_URL

    model_config = {"env_file": "../.env", "env_file_encoding": "utf-8", "extra": "ignore"}


settings = Settings()
