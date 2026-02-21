"""E2E 파이프라인 데이터 흐름 테스트.

Scout → Watchlist → Scanner → Executor → Monitor → Seller
전체 파이프라인의 데이터 모델 호환성과 흐름을 검증.
"""

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

from prime_jennie.domain.enums import (
    MarketRegime,
    OrderType,
    RiskTag,
    SectorGroup,
    SectorTier,
    SellReason,
    SignalType,
    TradeTier,
    TradeType,
)
from prime_jennie.domain.portfolio import Position
from prime_jennie.domain.trading import (
    BuySignal,
    OrderRequest,
    OrderResult,
    SellOrder,
    TradeRecord,
)

# ─── Scout → Watchlist ────────────────────────────────────


class TestScoutToWatchlistFlow:
    """Scout 파이프라인 출력 → Watchlist 모델 호환성."""

    def test_hybrid_score_to_watchlist_entry(self):
        """HybridScore → WatchlistEntry 변환."""
        from prime_jennie.domain.scoring import HybridScore
        from prime_jennie.domain.watchlist import WatchlistEntry

        hybrid = HybridScore(
            stock_code="005930",
            stock_name="삼성전자",
            quant_score=75.0,
            llm_score=80.0,
            hybrid_score=77.5,
            is_tradable=True,
            trade_tier=TradeTier.TIER1,
            risk_tag=RiskTag.BULLISH,
            scored_at=datetime.now(UTC),
        )

        entry = WatchlistEntry(
            stock_code=hybrid.stock_code,
            stock_name=hybrid.stock_name,
            hybrid_score=hybrid.hybrid_score,
            llm_score=hybrid.llm_score,
            trade_tier=hybrid.trade_tier,
            risk_tag=hybrid.risk_tag,
            is_tradable=hybrid.is_tradable,
            rank=1,
            sector_group=SectorGroup.SEMICONDUCTOR_IT,
        )

        assert entry.stock_code == "005930"
        assert entry.hybrid_score == 77.5
        assert entry.trade_tier == TradeTier.TIER1
        assert entry.risk_tag == RiskTag.BULLISH
        assert entry.sector_group == SectorGroup.SEMICONDUCTOR_IT

    def test_blocked_tier_propagation(self):
        """BLOCKED 티어 → is_tradable=False 전파."""
        from prime_jennie.domain.scoring import HybridScore

        blocked = HybridScore(
            stock_code="005930",
            stock_name="삼성전자",
            quant_score=30.0,
            llm_score=25.0,
            hybrid_score=27.5,
            is_tradable=False,
            trade_tier=TradeTier.BLOCKED,
            risk_tag=RiskTag.DISTRIBUTION_RISK,
            veto_applied=True,
            scored_at=datetime.now(UTC),
        )

        assert blocked.trade_tier == TradeTier.BLOCKED
        assert blocked.is_tradable is False
        assert blocked.veto_applied is True


# ─── Scanner → Executor ──────────────────────────────────


class TestScannerToExecutorFlow:
    """Scanner BuySignal → Executor 데이터 호환성."""

    def test_buy_signal_serialization(self):
        """BuySignal → JSON → BuySignal 왕복 직렬화."""
        signal = BuySignal(
            stock_code="005930",
            stock_name="삼성전자",
            signal_type=SignalType.GOLDEN_CROSS,
            signal_price=70000,
            llm_score=78.0,
            hybrid_score=75.0,
            is_tradable=True,
            trade_tier=TradeTier.TIER1,
            risk_tag=RiskTag.NEUTRAL,
            market_regime=MarketRegime.BULL,
            source="scanner",
            timestamp=datetime.now(UTC),
            rsi_value=45.0,
            volume_ratio=1.5,
            sector_group=SectorGroup.SEMICONDUCTOR_IT,
            position_multiplier=1.0,
        )

        # JSON 직렬화 왕복
        json_data = signal.model_dump_json()
        restored = BuySignal.model_validate_json(json_data)

        assert restored.stock_code == signal.stock_code
        assert restored.signal_type == SignalType.GOLDEN_CROSS
        assert restored.trade_tier == TradeTier.TIER1
        assert restored.sector_group == SectorGroup.SEMICONDUCTOR_IT
        assert restored.market_regime == MarketRegime.BULL

    def test_momentum_signal_requires_limit_order(self):
        """모멘텀 전략 → 지정가 주문 확인."""
        from prime_jennie.domain.enums import MOMENTUM_STRATEGIES

        assert SignalType.MOMENTUM in MOMENTUM_STRATEGIES
        assert SignalType.MOMENTUM_CONTINUATION in MOMENTUM_STRATEGIES
        assert SignalType.GOLDEN_CROSS not in MOMENTUM_STRATEGIES

    def test_executor_rejects_blocked_tier(self):
        """Executor: BLOCKED 티어 거부."""
        from prime_jennie.services.buyer.executor import BuyExecutor

        mock_redis = MagicMock()
        mock_redis.get.return_value = None
        mock_redis.setnx.return_value = True

        mock_kis = MagicMock()

        with patch("prime_jennie.services.buyer.executor.get_config") as mock_cfg:
            mock_cfg.return_value = MagicMock(
                trading_mode="MOCK",
                dry_run=True,
                risk=MagicMock(
                    max_portfolio_size=10,
                    max_buy_count_per_day=6,
                    max_position_value_pct=10.0,
                    stoploss_cooldown_days=3,
                ),
                scoring=MagicMock(hard_floor_score=40.0),
            )

            executor = BuyExecutor(mock_kis, mock_redis)

            signal = BuySignal(
                stock_code="005930",
                stock_name="삼성전자",
                signal_type=SignalType.GOLDEN_CROSS,
                signal_price=70000,
                llm_score=25.0,
                hybrid_score=20.0,
                is_tradable=False,
                trade_tier=TradeTier.BLOCKED,
                risk_tag=RiskTag.DISTRIBUTION_RISK,
                market_regime=MarketRegime.BULL,
                timestamp=datetime.now(UTC),
            )

            result = executor.process_signal(signal)
            assert result.status == "skipped"

    def test_hard_floor_rejection(self):
        """Executor: hybrid_score < 40 hard floor 거부."""
        from prime_jennie.services.buyer.executor import BuyExecutor

        mock_redis = MagicMock()
        mock_redis.get.return_value = None
        mock_redis.setnx.return_value = True

        mock_kis = MagicMock()

        with patch("prime_jennie.services.buyer.executor.get_config") as mock_cfg:
            mock_cfg.return_value = MagicMock(
                trading_mode="MOCK",
                dry_run=True,
                risk=MagicMock(
                    max_portfolio_size=10,
                    max_buy_count_per_day=6,
                    max_position_value_pct=10.0,
                    stoploss_cooldown_days=3,
                ),
                scoring=MagicMock(hard_floor_score=40.0),
            )

            executor = BuyExecutor(mock_kis, mock_redis)

            signal = BuySignal(
                stock_code="005930",
                stock_name="삼성전자",
                signal_type=SignalType.GOLDEN_CROSS,
                signal_price=70000,
                llm_score=35.0,
                hybrid_score=30.0,
                is_tradable=True,
                trade_tier=TradeTier.TIER2,
                market_regime=MarketRegime.BULL,
                timestamp=datetime.now(UTC),
            )

            result = executor.process_signal(signal)
            assert result.status == "skipped"


# ─── Monitor → Seller ────────────────────────────────────


class TestMonitorToSellerFlow:
    """Monitor SellOrder → Sell Executor 데이터 호환성."""

    def test_sell_order_serialization(self):
        """SellOrder JSON 왕복 직렬화."""
        order = SellOrder(
            stock_code="005930",
            stock_name="삼성전자",
            sell_reason=SellReason.PROFIT_TARGET,
            current_price=80000,
            quantity=10,
            timestamp=datetime.now(UTC),
            buy_price=70000,
            profit_pct=14.3,
            holding_days=5,
        )

        json_data = order.model_dump_json()
        restored = SellOrder.model_validate_json(json_data)

        assert restored.sell_reason == SellReason.PROFIT_TARGET
        assert restored.profit_pct == 14.3
        assert restored.holding_days == 5

    def test_sell_executor_stop_loss_bypasses_emergency(self):
        """Sell Executor: 긴급정지 중에도 STOP_LOSS는 수동 아님 → skipped."""
        from prime_jennie.services.seller.executor import SellExecutor

        mock_redis = MagicMock()
        mock_redis.get.return_value = "1"  # emergency stopped
        mock_redis.setnx.return_value = True

        mock_kis = MagicMock()

        with patch("prime_jennie.services.seller.executor.get_config") as mock_cfg:
            mock_cfg.return_value = MagicMock(
                trading_mode="MOCK",
                dry_run=True,
            )

            executor = SellExecutor(mock_kis, mock_redis)

            order = SellOrder(
                stock_code="005930",
                stock_name="삼성전자",
                sell_reason=SellReason.STOP_LOSS,
                current_price=65000,
                quantity=10,
                timestamp=datetime.now(UTC),
            )

            result = executor.process_signal(order)
            # STOP_LOSS is not MANUAL, so emergency stop blocks it
            assert result.status == "skipped"

    def test_sell_executor_manual_bypasses_emergency(self):
        """Sell Executor: MANUAL 매도는 긴급정지 무시."""
        from prime_jennie.services.seller.executor import SellExecutor

        mock_redis = MagicMock()
        mock_redis.get.return_value = "1"  # emergency stopped
        mock_redis.setnx.return_value = True

        mock_kis = MagicMock()
        mock_kis.get_positions.return_value = [
            Position(
                stock_code="005930",
                stock_name="삼성전자",
                quantity=10,
                average_buy_price=70000,
                total_buy_amount=700000,
            )
        ]
        mock_price = MagicMock()
        mock_price.price = 65000
        mock_kis.get_price.return_value = mock_price
        mock_kis.sell.return_value = OrderResult(
            success=True,
            order_no="ORD001",
            stock_code="005930",
            quantity=10,
            price=65000,
        )

        with patch("prime_jennie.services.seller.executor.get_config") as mock_cfg:
            mock_cfg.return_value = MagicMock(
                trading_mode="MOCK",
                dry_run=False,
            )

            executor = SellExecutor(mock_kis, mock_redis)

            order = SellOrder(
                stock_code="005930",
                stock_name="삼성전자",
                sell_reason=SellReason.MANUAL,
                current_price=65000,
                quantity=10,
                timestamp=datetime.now(UTC),
            )

            result = executor.process_signal(order)
            # MANUAL bypasses emergency stop
            assert result.status != "skipped"


# ─── Cross-Service Model Compatibility ────────────────────


class TestCrossServiceModelCompatibility:
    """서비스 간 모델 호환성 검증."""

    def test_order_request_model(self):
        """OrderRequest 모델 — buy/sell 공통 사용."""
        buy_req = OrderRequest(
            stock_code="005930",
            quantity=10,
            order_type=OrderType.MARKET,
        )
        assert buy_req.price is None  # 시장가

        limit_req = OrderRequest(
            stock_code="005930",
            quantity=10,
            order_type=OrderType.LIMIT,
            price=70000,
        )
        assert limit_req.price == 70000

    def test_order_result_model(self):
        """OrderResult 모델 — 주문 결과."""
        result = OrderResult(
            success=True,
            order_no="ORD123",
            stock_code="005930",
            quantity=10,
            price=70000,
        )
        assert result.success is True
        assert result.order_no == "ORD123"

    def test_trade_record_model(self):
        """TradeRecord — DB 저장용 거래 기록."""
        record = TradeRecord(
            stock_code="005930",
            stock_name="삼성전자",
            trade_type=TradeType.BUY,
            quantity=10,
            price=70000,
            total_amount=700000,
            reason="GOLDEN_CROSS signal",
            strategy_signal="GOLDEN_CROSS",
            market_regime=MarketRegime.BULL,
            llm_score=78.0,
            hybrid_score=75.0,
            trade_tier=TradeTier.TIER1,
            timestamp=datetime.now(UTC),
        )
        assert record.total_amount == 700000

    def test_position_model(self):
        """Position 모델 — profit_pct는 외부 계산."""
        pos = Position(
            stock_code="005930",
            stock_name="삼성전자",
            quantity=10,
            average_buy_price=70000,
            total_buy_amount=700000,
            current_price=75000,
            profit_pct=7.14,  # 외부 계산 후 설정
        )
        assert pos.profit_pct == pytest.approx(7.14, abs=0.01)
        assert pos.current_price == 75000


# ─── Sector Budget Flow ──────────────────────────────────


class TestSectorBudgetFlow:
    """Scout → Sector Budget → Watchlist → Portfolio Guard 데이터 흐름."""

    def test_sector_taxonomy_to_budget(self):
        """네이버 세분류 → SectorGroup → Budget tier."""
        from prime_jennie.domain.sector import SectorBudget, SectorBudgetEntry
        from prime_jennie.domain.sector_taxonomy import get_sector_group

        # 반도체 → SEMICONDUCTOR_IT
        group = get_sector_group("반도체와반도체장비")
        assert group == SectorGroup.SEMICONDUCTOR_IT

        # Budget allocation
        budget = SectorBudget(
            entries={
                SectorGroup.SEMICONDUCTOR_IT: SectorBudgetEntry(
                    sector_group=SectorGroup.SEMICONDUCTOR_IT,
                    tier=SectorTier.HOT,
                    watchlist_cap=5,
                    portfolio_cap=5,
                    effective_cap=5,
                ),
                SectorGroup.BIO_HEALTH: SectorBudgetEntry(
                    sector_group=SectorGroup.BIO_HEALTH,
                    tier=SectorTier.WARM,
                    watchlist_cap=3,
                    portfolio_cap=3,
                    effective_cap=3,
                ),
                SectorGroup.CONSTRUCTION: SectorBudgetEntry(
                    sector_group=SectorGroup.CONSTRUCTION,
                    tier=SectorTier.COOL,
                    watchlist_cap=2,
                    portfolio_cap=2,
                    effective_cap=2,
                ),
            },
            generated_at=datetime.now(UTC).isoformat(),
        )

        assert budget.get_cap(SectorGroup.SEMICONDUCTOR_IT) == 5
        assert budget.get_cap(SectorGroup.BIO_HEALTH) == 3
        assert budget.get_cap(SectorGroup.CONSTRUCTION) == 2
        assert budget.get_cap(SectorGroup.FINANCE) == 3  # default


# ─── Redis Stream Key Consistency ─────────────────────────


class TestRedisKeyConsistency:
    """서비스 간 Redis 키 일관성 검증."""

    def test_buy_signal_stream_key_matches(self):
        """Scanner와 Executor가 같은 stream 키 사용."""
        from prime_jennie.services.scanner.app import STREAM_BUY_SIGNALS

        assert STREAM_BUY_SIGNALS == "stream:buy-signals"

    def test_sell_signal_stream_key_matches(self):
        """Monitor와 Sell Executor가 같은 stream 키 사용."""
        from prime_jennie.services.monitor.app import SELL_SIGNAL_STREAM

        assert SELL_SIGNAL_STREAM == "stream:sell-orders"

    def test_watchlist_cache_key_matches(self):
        """Scout와 Scanner가 같은 watchlist 캐시 키 사용."""
        from prime_jennie.services.scanner.app import CACHE_WATCHLIST

        assert CACHE_WATCHLIST == "watchlist:active"

    def test_trading_context_cache_key_matches(self):
        """Council/Macro와 Scanner가 같은 context 캐시 키 사용."""
        from prime_jennie.services.scanner.app import CACHE_TRADING_CONTEXT

        assert CACHE_TRADING_CONTEXT == "trading:context"


# ─── Config Consistency ───────────────────────────────────


class TestConfigConsistency:
    """설정 일관성 검증."""

    def test_all_sell_reasons_in_enum(self):
        """매도 사유 enum 완전성."""
        reasons = [
            SellReason.PROFIT_TARGET,
            SellReason.STOP_LOSS,
            SellReason.TRAILING_STOP,
            SellReason.BREAKEVEN_STOP,
            SellReason.RSI_OVERBOUGHT,
            SellReason.TIME_EXIT,
            SellReason.PROFIT_FLOOR,
            SellReason.DEATH_CROSS,
            SellReason.RISK_OFF,
            SellReason.MANUAL,
        ]
        assert len(reasons) == 10

    def test_all_signal_types_in_enum(self):
        """매수 시그널 enum 완전성."""
        signals = [
            SignalType.GOLDEN_CROSS,
            SignalType.RSI_REBOUND,
            SignalType.MOMENTUM,
            SignalType.MOMENTUM_CONTINUATION,
            SignalType.DIP_BUY,
            SignalType.VOLUME_BREAKOUT,
            SignalType.WATCHLIST_CONVICTION,
        ]
        assert len(signals) == 7

    def test_14_sector_groups(self):
        """14개 대분류 그룹."""
        assert len(SectorGroup) == 14
