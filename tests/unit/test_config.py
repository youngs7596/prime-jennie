"""Configuration system unit tests."""

import os

import pytest

from prime_jennie.domain.config import AppConfig, get_config
from prime_jennie.domain.enums import MarketRegime


class TestAppConfig:
    def test_defaults(self):
        config = AppConfig()
        assert config.env == "production"
        assert config.trading_mode == "REAL"
        assert config.is_mock is False
        assert config.dry_run is False

    def test_db_url(self):
        config = AppConfig()
        assert "pymysql" in config.db.url
        assert config.db.host in config.db.url

    def test_redis_url_no_password(self):
        config = AppConfig()
        url = config.redis.url
        assert url.startswith("redis://")
        assert "localhost" in url

    def test_risk_cash_floor(self):
        config = AppConfig()
        assert config.risk.get_cash_floor(MarketRegime.BULL) == 10.0
        assert config.risk.get_cash_floor(MarketRegime.BEAR) == 25.0
        assert config.risk.get_cash_floor(MarketRegime.STRONG_BEAR) == 25.0

    def test_sub_config_count(self):
        config = AppConfig()
        # 12 sub-configs
        assert hasattr(config, "db")
        assert hasattr(config, "redis")
        assert hasattr(config, "kis")
        assert hasattr(config, "llm")
        assert hasattr(config, "risk")
        assert hasattr(config, "scoring")
        assert hasattr(config, "scanner")
        assert hasattr(config, "scout")
        assert hasattr(config, "sell")
        assert hasattr(config, "signal")
        assert hasattr(config, "telegram")
        assert hasattr(config, "infra")

    def test_mock_mode(self):
        config = AppConfig(trading_mode="MOCK")
        assert config.is_mock is True


class TestGetConfig:
    def test_singleton(self):
        get_config.cache_clear()
        c1 = get_config()
        c2 = get_config()
        assert c1 is c2
        get_config.cache_clear()

    def test_env_override(self, monkeypatch):
        get_config.cache_clear()
        monkeypatch.setenv("DB_HOST", "testhost")
        monkeypatch.setenv("DB_PORT", "3308")
        config = get_config()
        assert config.db.host == "testhost"
        assert config.db.port == 3308
        get_config.cache_clear()
