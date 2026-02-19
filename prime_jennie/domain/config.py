"""통합 설정 모델 — Pydantic Settings 기반.

모든 설정값은 환경 변수로 주입. 우선순위:
  1. 환경 변수 (docker-compose env, .env)
  2. Pydantic Settings 기본값

기존 my-prime-jennie의 분산 설정 (registry.py 109키, env-vars-wsl.yaml, DB CONFIG 테이블)을
단일 계층으로 통합.
"""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings

from .enums import MarketRegime


class DatabaseConfig(BaseSettings):
    """데이터베이스 설정."""

    host: str = "localhost"
    port: int = 3307
    user: str = "prime"
    password: str = ""
    name: str = "prime_jennie"

    model_config = {"env_prefix": "DB_"}

    @property
    def url(self) -> str:
        return f"mysql+pymysql://{self.user}:{self.password}@{self.host}:{self.port}/{self.name}"

    @property
    def async_url(self) -> str:
        return f"mysql+aiomysql://{self.user}:{self.password}@{self.host}:{self.port}/{self.name}"


class RedisConfig(BaseSettings):
    """Redis 설정."""

    host: str = "localhost"
    port: int = 6379
    db: int = 0
    password: str = ""

    model_config = {"env_prefix": "REDIS_"}

    @property
    def url(self) -> str:
        if self.password:
            return f"redis://:{self.password}@{self.host}:{self.port}/{self.db}"
        return f"redis://{self.host}:{self.port}/{self.db}"


class KISConfig(BaseSettings):
    """한국투자증권 API 설정."""

    app_key: str = ""
    app_secret: str = ""
    account_no: str = ""
    account_product_code: str = "01"
    base_url: str = "https://openapi.koreainvestment.com:9443"
    is_paper: bool = False
    token_file_path: str = "/app/config/kis_token.json"
    gateway_url: str = "http://kis-gateway:8080"

    model_config = {"env_prefix": "KIS_"}


class LLMConfig(BaseSettings):
    """LLM 설정."""

    tier_fast_provider: str = "ollama"
    tier_reasoning_provider: str = "deepseek_cloud"
    tier_thinking_provider: str = "deepseek_cloud"
    vllm_llm_url: str = "http://localhost:8001/v1"
    vllm_embed_url: str = "http://localhost:8002/v1"
    vllm_max_model_len: int = 4096

    model_config = {"env_prefix": "LLM_"}


class RiskConfig(BaseSettings):
    """리스크 관리 설정."""

    max_portfolio_size: int = 10
    max_sector_stocks: int = 3
    portfolio_guard_enabled: bool = True
    dynamic_sector_budget_enabled: bool = True
    max_buy_count_per_day: int = 6
    max_position_value_pct: float = 10.0
    stoploss_cooldown_days: int = 3
    # 국면별 현금 하한선
    cash_floor_strong_bull_pct: float = 5.0
    cash_floor_bull_pct: float = 10.0
    cash_floor_sideways_pct: float = 15.0
    cash_floor_bear_pct: float = 25.0

    model_config = {"env_prefix": "RISK_"}

    def get_cash_floor(self, regime: MarketRegime) -> float:
        return {
            MarketRegime.STRONG_BULL: self.cash_floor_strong_bull_pct,
            MarketRegime.BULL: self.cash_floor_bull_pct,
            MarketRegime.SIDEWAYS: self.cash_floor_sideways_pct,
            MarketRegime.BEAR: self.cash_floor_bear_pct,
            MarketRegime.STRONG_BEAR: self.cash_floor_bear_pct,
        }.get(regime, self.cash_floor_sideways_pct)


class ScoringConfig(BaseSettings):
    """스코어링 설정."""

    quant_scorer_version: str = "v2"
    unified_analyst_enabled: bool = True
    llm_clamp_range: int = 15
    hard_floor_score: float = 40.0

    model_config = {"env_prefix": "SCORING_"}


class ScannerConfig(BaseSettings):
    """Scanner 설정."""

    min_required_bars: int = 20
    signal_cooldown_seconds: int = 600
    rsi_guard_max: float = 75.0
    volume_ratio_warning: float = 2.0
    vwap_deviation_warning: float = 0.02
    no_trade_window_start: str = "09:00"
    no_trade_window_end: str = "09:15"
    danger_zone_start: str = "14:00"
    danger_zone_end: str = "15:00"
    # Conviction Entry
    conviction_entry_enabled: bool = True
    conviction_min_hybrid_score: float = 70.0
    conviction_min_llm_score: float = 72.0
    conviction_max_gain_pct: float = 3.0
    conviction_window_start: str = "09:15"
    conviction_window_end: str = "10:30"
    # Momentum
    momentum_limit_order_enabled: bool = True
    momentum_limit_premium: float = 0.003
    momentum_limit_timeout_sec: int = 10
    momentum_confirmation_bars: int = 1
    momentum_max_gain_pct: float = 7.0

    model_config = {"env_prefix": "SCANNER_"}


class ScoutConfig(BaseSettings):
    """Scout 설정."""

    max_watchlist_size: int = 20
    universe_size: int = 200
    enable_news_analysis: bool = True

    model_config = {"env_prefix": "SCOUT_"}


class SellConfig(BaseSettings):
    """매도 설정."""

    rsi_overbought_threshold: float = 75.0
    trailing_enabled: bool = True
    trailing_activation_pct: float = 5.0
    trailing_atr_mult: float = 1.5
    profit_target_pct: float = 8.0
    stop_loss_pct: float = 5.0
    time_exit_bull_days: int = 20
    time_exit_sideways_days: int = 35

    model_config = {"env_prefix": "SELL_"}


class SignalConfig(BaseSettings):
    """매수 시그널 파라미터."""

    golden_cross_short: int = 5
    golden_cross_long: int = 20
    rsi_oversold: int = 30
    rsi_oversold_bull: int = 40

    model_config = {"env_prefix": "SIGNAL_"}


class TelegramConfig(BaseSettings):
    """텔레그램 설정."""

    bot_token: str = ""
    chat_id: str = ""

    model_config = {"env_prefix": "TELEGRAM_"}


class InfraConfig(BaseSettings):
    """인프라 서비스 설정."""

    qdrant_host: str = "localhost"
    qdrant_port: int = 6333
    dart_api_key: str = ""

    model_config = {"env_prefix": "INFRA_"}


class AppConfig(BaseSettings):
    """최상위 설정 — 서브 설정 객체를 조합.

    Usage:
        from prime_jennie.domain.config import get_config
        config = get_config()
        print(config.db.url)
        print(config.risk.get_cash_floor(MarketRegime.BULL))
    """

    env: str = Field(default="production", description="development | staging | production")
    debug: bool = False
    log_level: str = "INFO"
    timezone: str = "Asia/Seoul"
    trading_mode: str = "REAL"
    dry_run: bool = False

    db: DatabaseConfig = Field(default_factory=DatabaseConfig)
    redis: RedisConfig = Field(default_factory=RedisConfig)
    kis: KISConfig = Field(default_factory=KISConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    risk: RiskConfig = Field(default_factory=RiskConfig)
    scoring: ScoringConfig = Field(default_factory=ScoringConfig)
    scanner: ScannerConfig = Field(default_factory=ScannerConfig)
    scout: ScoutConfig = Field(default_factory=ScoutConfig)
    sell: SellConfig = Field(default_factory=SellConfig)
    signal: SignalConfig = Field(default_factory=SignalConfig)
    telegram: TelegramConfig = Field(default_factory=TelegramConfig)
    infra: InfraConfig = Field(default_factory=InfraConfig)

    model_config = {"env_prefix": "APP_"}

    @property
    def is_mock(self) -> bool:
        return self.trading_mode == "MOCK"


@lru_cache
def get_config() -> AppConfig:
    """싱글턴 설정 인스턴스.

    프로세스 내에서 한 번만 환경 변수를 읽고 캐싱.
    테스트에서는 get_config.cache_clear()로 초기화.
    """
    return AppConfig()
