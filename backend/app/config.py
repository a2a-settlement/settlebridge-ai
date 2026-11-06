from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    APP_NAME: str = "SettleBridge"
    APP_URL: str = "http://localhost:8000"
    SECRET_KEY: str = "change-me"
    DEBUG: bool = True

    DATABASE_URL: str = "postgresql+asyncpg://settlebridge:settlebridge@localhost:5432/settlebridge"

    A2A_EXCHANGE_URL: str = "https://exchange.a2a-settlement.org"
    A2A_DASHBOARD_API_KEY: str = ""

    MEDIATOR_URL: str = "https://mediator.a2a-settlement.org"
    MEDIATOR_WEBHOOK_SECRET: str = ""

    JWT_SECRET: str = "change-me"
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRATION_HOURS: int = 24

    ANTHROPIC_API_KEY: str = ""
    ASSIST_MODEL: str = "claude-sonnet-4-5-20250929"
    ASSIST_MAX_TURNS: int = 10
    ASSIST_MAX_TOKENS: int = 4096

    RESEND_API_KEY: str = ""
    CONTACT_EMAIL_TO: str = ""
    CONTACT_EMAIL_FROM: str = "contact@settlebridge.ai"

    MARKETPLACE_ENABLED: bool = False

    # Gateway
    REDIS_URL: str = "redis://localhost:6379"
    GATEWAY_EXCHANGE_URL: str = ""
    GATEWAY_EXCHANGE_API_KEY: str = ""
    POLICY_RELOAD_INTERVAL_S: int = 30
    REPUTATION_CACHE_TTL_S: int = 300
    HEALTH_CHECK_INTERVAL_S: int = 60
    GATEWAY_ENABLED: bool = True

    # Shared secret for POST /api/gateway/agents/register (utility agents / provisioning).
    # If empty, that route rejects all requests.
    UTILITY_AGENT_REGISTRATION_SECRET: str = ""

    # Default TTL for escrows created by SettleBridge (minutes). 1440 = 24 hours.
    ESCROW_TTL_MINUTES: int = 1440

    # GitHub App bounty bridge
    GITHUB_APP_ID: str = ""
    GITHUB_APP_PRIVATE_KEY: str = ""
    GITHUB_WEBHOOK_SECRET: str = ""
    # GitHub drops webhook deliveries larger than 25 MiB.
    GITHUB_WEBHOOK_MAX_BODY_BYTES: int = 26_214_400
    GITHUB_API_URL: str = "https://api.github.com"
    GITHUB_SETTLEMENT_ENABLED: bool = False
    # Lets a signed-in repo admin onboard their own repo through the API.
    GITHUB_SELF_SERVE_ONBOARDING: bool = False
    # The App's public slug, used to build its install link.
    GITHUB_APP_SLUG: str = "settlebridge-bounties"
    # Sign in with GitHub, creating the user and an exchange account on first sign-in.
    GITHUB_LOGIN_ENABLED: bool = False
    # The setup page's Add funds button deposits test ATE into the caller's own
    # exchange account. Off unless enabled; refused once the balance reaches the cap.
    GITHUB_SETUP_FUNDING_ENABLED: bool = False
    GITHUB_SETUP_FUND_ATE: int = 1000
    GITHUB_SETUP_FUND_MAX_BALANCE_ATE: int = 2000
    # Test ATE deposited into the exchange account a first GitHub sign-in creates,
    # when GITHUB_SETUP_FUNDING_ENABLED is on. 0 disables it.
    GITHUB_LOGIN_STARTER_ATE: int = 1000
    # Security-alert auto-bounties. Each repo must also opt in (auto_dispatch_alerts).
    GITHUB_AUTO_DISPATCH_ALERTS: bool = False
    GITHUB_AUTO_BOUNTY_DEPENDABOT_USD: int = 15
    GITHUB_AUTO_BOUNTY_CODEQL_USD: int = 25
    # Per repo, over the trailing 7 days.
    GITHUB_MAX_WEEKLY_AUTO_BOUNTY_ATE: int = 10000
    # How long settlement of an alert bounty waits for GitHub to mark the alert fixed.
    GITHUB_ALERT_FIXED_TIMEOUT_HOURS: int = 24
    # Bounded auto-merge. Each repo must also opt in (auto_merge_acceptable).
    GITHUB_AUTO_MERGE_ACCEPTABLE: bool = False
    GITHUB_AUTO_MERGE_MIN_EMA: float = 0.90
    GITHUB_AUTO_MERGE_MIN_KYA: int = 2
    GITHUB_AUTO_MERGE_MAX_DIFF_LINES: int = 100
    # Create the funder's escrow when a lease is claimed; refund it if the lease ends unpaid.
    GITHUB_LOCK_ESCROW_ON_LEASE_CLAIM: bool = False
    # Extra escrow lifetime beyond the lease and alert windows.
    GITHUB_HOLD_TTL_MARGIN_MINUTES: int = 360
    # Refuse leases where the solver is the funder, by user, GitHub account or exchange principal.
    GITHUB_ENFORCE_KYA_LINEAGE_SEPARATION: bool = True
    # A house solver may claim a bounty only after it has been advertised this long.
    GITHUB_HOUSE_CLAIM_DELAY_HOURS: int = 24
    GITHUB_DEFAULT_REQUIRED_EMA: float = 0.75
    # Comma-separated owner/repo=ema pairs that replace the default for that repo.
    GITHUB_REPO_REQUIRED_EMA: str = ""
    GITHUB_DEFAULT_KYA_LEVEL: int = 1
    GITHUB_ATE_PER_USD: int = 100
    GITHUB_CLEARING_FEE_BPS: int = 0
    # Mirror the exchange's A2A_EXCHANGE_FEE_PERCENT and A2A_EXCHANGE_MIN_FEE.
    # The funder pays this fee on top of the escrow amount.
    GITHUB_EXCHANGE_FEE_PERCENT: str = "0.25"
    GITHUB_EXCHANGE_MIN_FEE: int = 1
    GITHUB_JOB_MAX_ATTEMPTS: int = 8
    GITHUB_JOB_POLL_INTERVAL_S: int = 5
    MARKET_URL: str = "https://market.settlebridge.ai"
    SETTLEBRIDGE_HOSTED_MCP_URL: str = "https://mcp.a2a-settlement.org/mcp"

    # GitHub user OAuth (identity verification through the GitHub App)
    GITHUB_OAUTH_CLIENT_ID: str = ""
    GITHUB_OAUTH_CLIENT_SECRET: str = ""
    GITHUB_OAUTH_URL: str = "https://github.com"

    # mcp-trust-gateway JWT verification. TRUST_GATEWAY_KEY is the HMAC secret or PEM public key.
    TRUST_GATEWAY_ISSUER: str = ""
    TRUST_GATEWAY_AUDIENCE: str = "https://gateway.a2a-settlement.org"
    TRUST_GATEWAY_KEY: str = ""
    TRUST_GATEWAY_ALGORITHMS: str = "RS256"
    TRUST_GATEWAY_TOKEN_URL: str = ""

    # Bounty leases
    LEASE_WORK_MINUTES: int = 15
    LEASE_HARD_CAP_MINUTES: int = 120
    LEASE_SUBMITTED_HOLD_HOURS: int = 72
    LEASE_MAX_ACTIVE_PER_AGENT: int = 2
    LEASE_COOLDOWN_EXPIRIES: int = 3
    LEASE_COOLDOWN_MINUTES: int = 60
    WORKER_HEARTBEAT_TTL_S: int = 90

    @property
    def effective_exchange_url(self) -> str:
        return self.GATEWAY_EXCHANGE_URL or self.A2A_EXCHANGE_URL

    model_config = {"env_file": "../.env", "env_file_encoding": "utf-8", "extra": "ignore"}


settings = Settings()
