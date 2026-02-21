"""백테스트 엔진 — 일간 시뮬레이션 루프.

매일:
  0. 일일 카운터 리셋
  1. 매크로 국면 + 워치리스트 로드
  2. [EXIT FIRST] 보유 포지션 매도 평가
  3. 워터마크 업데이트
  4. [ENTRY] 워치리스트 순회 → 전략 감지 → 매수
  5. 일별 스냅샷 기록
"""

from __future__ import annotations

import logging
import math
from datetime import date

from prime_jennie.domain.enums import MarketRegime, SellReason
from prime_jennie.domain.trading import PositionSizingRequest
from prime_jennie.services.buyer.position_sizing import (
    calculate_atr,
    calculate_position_size,
    calculate_rsi,
    clamp_atr,
)
from prime_jennie.services.monitor.exit_rules import PositionContext, evaluate_exit
from prime_jennie.services.monitor.indicators import (
    check_death_cross,
    check_macd_bearish_divergence,
)

from .daily_strategies import detect_strategies
from .models import (
    BacktestConfig,
    MacroDay,
    PriceCache,
    WatchlistEntry,
)
from .portfolio import SimulatedPortfolio

logger = logging.getLogger(__name__)


class BacktestEngine:
    """E2E 백테스트 엔진."""

    def __init__(
        self,
        config: BacktestConfig,
        price_cache: PriceCache,
        watchlists: dict[date, list[WatchlistEntry]],
        macro_days: dict[date, MacroDay],
        trading_dates: list[date],
    ) -> None:
        self.config = config
        self.prices = price_cache
        self.watchlists = watchlists
        self.macro_days = macro_days
        self.trading_dates = trading_dates
        self.portfolio = SimulatedPortfolio(config)

    def run(self) -> SimulatedPortfolio:
        """전체 시뮬레이션 실행."""
        logger.info(
            "Backtest: %s ~ %s (%d trading days, capital=%s)",
            self.config.start_date,
            self.config.end_date,
            len(self.trading_dates),
            f"{self.config.initial_capital:,}",
        )

        for i, day in enumerate(self.trading_dates):
            self._simulate_day(day)

            if (i + 1) % 20 == 0:
                snap = self.portfolio.daily_snapshots[-1]
                logger.info(
                    "  [%d/%d] %s | total=%s | positions=%d | cash=%s",
                    i + 1,
                    len(self.trading_dates),
                    day,
                    f"{snap.total_value:,}",
                    snap.position_count,
                    f"{snap.cash:,}",
                )

        # 종료 시 전체 청산
        if self.trading_dates:
            last_day = self.trading_dates[-1]
            regime = self._get_regime(last_day)
            self.portfolio.liquidate_all(self.prices, last_day, regime)

        return self.portfolio

    def _simulate_day(self, day: date) -> None:
        """하루 시뮬레이션."""
        # 0. 일일 카운터 리셋
        self.portfolio.reset_daily()

        # 1. 매크로 국면
        regime = self._get_regime(day)
        macro_stop_mult = self._get_macro_stop_mult(day)

        # 2. [EXIT FIRST] 매도 평가
        self._process_exits(day, regime, macro_stop_mult)

        # 3. 워터마크 업데이트
        self.portfolio.update_watermarks(self.prices, day)

        # 4. [ENTRY] 매수 평가
        wl = self._get_watchlist(day)
        if wl:
            self._process_entries(day, wl, regime)

        # 5. 스냅샷
        self.portfolio.take_snapshot(self.prices, day, regime)

    # --- EXIT ---

    def _process_exits(
        self,
        day: date,
        regime: MarketRegime,
        macro_stop_mult: float,
    ) -> None:
        """보유 포지션 매도 평가.

        손절: LOW 가격으로 체크 (보수적: 장중 저가 터치 가정)
        익절: HIGH 가격으로 체크
        나머지: CLOSE 가격으로 체크
        """
        codes = list(self.portfolio.positions.keys())

        for code in codes:
            pos = self.portfolio.positions.get(code)
            if not pos:
                continue

            ohlcv = self.prices.get(code, day)
            if not ohlcv:
                continue

            # 기술 지표 계산
            close_prices = self.prices.get_close_prices_until(code, day, n=60)
            atr = self._calc_atr(code, day)
            rsi = calculate_rsi(close_prices, period=14) if len(close_prices) >= 15 else None
            death_cross = check_death_cross(close_prices) if len(close_prices) >= 21 else False
            macd_bearish = (
                check_macd_bearish_divergence(close_prices) if len(close_prices) >= 36 else False
            )

            holding_days = pos.holding_days(day)

            # --- Phase 1: LOW 가격으로 손절 체크 ---
            ctx_low = PositionContext(
                stock_code=code,
                current_price=ohlcv.low_price,
                buy_price=pos.buy_price,
                quantity=pos.quantity,
                profit_pct=pos.profit_pct(ohlcv.low_price),
                high_watermark=pos.high_watermark,
                high_profit_pct=pos.high_profit_pct(),
                atr=atr,
                rsi=rsi,
                holding_days=holding_days,
                scale_out_level=pos.scale_out_level,
                rsi_sold=pos.rsi_sold,
                macd_bearish=macd_bearish,
                death_cross=death_cross,
                profit_floor_active=pos.profit_floor_active,
                profit_floor_level=pos.profit_floor_level,
            )

            signal_low = evaluate_exit(ctx_low, regime, macro_stop_mult)
            if signal_low and signal_low.should_sell and self._is_stop_loss_signal(signal_low):
                sell_qty = self._calc_sell_quantity(pos.quantity, signal_low.quantity_pct)
                self.portfolio.execute_sell(
                    code, ohlcv.low_price, sell_qty, day, signal_low.reason, regime
                )
                if code not in self.portfolio.positions:
                    continue  # 전량 매도

            # --- Phase 2: HIGH 가격으로 익절 체크 ---
            pos = self.portfolio.positions.get(code)
            if not pos:
                continue

            ctx_high = PositionContext(
                stock_code=code,
                current_price=ohlcv.high_price,
                buy_price=pos.buy_price,
                quantity=pos.quantity,
                profit_pct=pos.profit_pct(ohlcv.high_price),
                high_watermark=max(pos.high_watermark, ohlcv.high_price),
                high_profit_pct=max(
                    pos.high_profit_pct(),
                    pos.profit_pct(ohlcv.high_price),
                ),
                atr=atr,
                rsi=rsi,
                holding_days=holding_days,
                scale_out_level=pos.scale_out_level,
                rsi_sold=pos.rsi_sold,
                macd_bearish=macd_bearish,
                death_cross=death_cross,
                profit_floor_active=pos.profit_floor_active,
                profit_floor_level=pos.profit_floor_level,
            )

            signal_high = evaluate_exit(ctx_high, regime, macro_stop_mult)
            if signal_high and signal_high.should_sell and self._is_take_profit_signal(signal_high):
                sell_qty = self._calc_sell_quantity(pos.quantity, signal_high.quantity_pct)
                self.portfolio.execute_sell(
                    code, ohlcv.high_price, sell_qty, day, signal_high.reason, regime
                )
                if code not in self.portfolio.positions:
                    continue

            # --- Phase 3: CLOSE 가격으로 나머지 체크 ---
            pos = self.portfolio.positions.get(code)
            if not pos:
                continue

            ctx_close = PositionContext(
                stock_code=code,
                current_price=ohlcv.close_price,
                buy_price=pos.buy_price,
                quantity=pos.quantity,
                profit_pct=pos.profit_pct(ohlcv.close_price),
                high_watermark=max(pos.high_watermark, ohlcv.high_price),
                high_profit_pct=max(
                    pos.high_profit_pct(),
                    pos.profit_pct(ohlcv.high_price),
                ),
                atr=atr,
                rsi=rsi,
                holding_days=holding_days,
                scale_out_level=pos.scale_out_level,
                rsi_sold=pos.rsi_sold,
                macd_bearish=macd_bearish,
                death_cross=death_cross,
                profit_floor_active=pos.profit_floor_active,
                profit_floor_level=pos.profit_floor_level,
            )

            signal_close = evaluate_exit(ctx_close, regime, macro_stop_mult)
            if signal_close and signal_close.should_sell:
                sell_qty = self._calc_sell_quantity(pos.quantity, signal_close.quantity_pct)
                self.portfolio.execute_sell(
                    code, ohlcv.close_price, sell_qty, day, signal_close.reason, regime
                )

    # --- ENTRY ---

    def _process_entries(
        self,
        day: date,
        watchlist: list[WatchlistEntry],
        regime: MarketRegime,
    ) -> None:
        """워치리스트 순회 → 전략 감지 → 매수."""
        # hybrid_score 내림차순 정렬
        sorted_wl = sorted(watchlist, key=lambda e: e.hybrid_score, reverse=True)

        for entry in sorted_wl:
            code = entry.stock_code

            # 리스크 가드
            ok, reason = self.portfolio.can_buy(code, entry.sector_group, day, regime)
            if not ok:
                continue

            # 전략 감지
            signals = detect_strategies(entry, self.prices, day, regime)
            if not signals:
                continue

            # 첫 번째 시그널 사용 (우선순위순)
            signal_type = signals[0]

            # 가격 데이터
            ohlcv = self.prices.get(code, day)
            if not ohlcv or ohlcv.close_price <= 0:
                continue

            # ATR 계산
            atr = self._calc_atr(code, day)

            # 포지션 사이징 (production 함수 재사용)
            pv = self.portfolio.total_portfolio_value(self.prices, day)
            macro_day = self.macro_days.get(day)
            pos_mult = (macro_day.position_size_pct / 100) if macro_day else 1.0
            pos_mult = max(0.3, min(pos_mult, 2.0))

            sizing_req = PositionSizingRequest(
                stock_code=code,
                stock_price=ohlcv.close_price,
                atr=atr,
                available_cash=self.portfolio.cash,
                portfolio_value=pv,
                llm_score=min(entry.llm_score, 100.0),
                trade_tier=entry.trade_tier,
                sector_group=entry.sector_group,
                held_sector_groups=self.portfolio.held_sector_groups(),
                portfolio_risk_pct=0.0,
                position_multiplier=pos_mult,
                stale_days=max(0, (day - entry.snapshot_date).days),
            )

            result = calculate_position_size(sizing_req)
            if result.quantity <= 0:
                continue

            self.portfolio.execute_buy(
                stock_code=code,
                stock_name=entry.stock_name,
                quantity=result.quantity,
                price=ohlcv.close_price,
                trade_date=day,
                signal_type=signal_type,
                trade_tier=entry.trade_tier,
                llm_score=entry.llm_score,
                hybrid_score=entry.hybrid_score,
                sector_group=entry.sector_group,
                regime=regime,
            )

    # --- Helpers ---

    def _get_regime(self, day: date) -> MarketRegime:
        macro = self.macro_days.get(day)
        if macro:
            return macro.regime
        # 가장 최근 매크로 데이터 사용
        for d in sorted(self.macro_days.keys(), reverse=True):
            if d <= day:
                return self.macro_days[d].regime
        return MarketRegime.SIDEWAYS

    def _get_macro_stop_mult(self, day: date) -> float:
        macro = self.macro_days.get(day)
        if macro:
            return macro.stop_loss_adjust_pct / 100
        return 1.0

    def _get_watchlist(self, day: date) -> list[WatchlistEntry]:
        """당일 또는 최근 워치리스트 반환."""
        if day in self.watchlists:
            return self.watchlists[day]
        # 최근 3일 이내 워치리스트
        for offset in range(1, 4):
            from datetime import timedelta

            d = day - timedelta(days=offset)
            if d in self.watchlists:
                return self.watchlists[d]
        return []

    def _calc_atr(self, stock_code: str, day: date) -> float:
        """해당 날짜까지의 ATR 계산."""
        history = self.prices.get_history_until(stock_code, day, n=30)
        if len(history) < 2:
            return 0.0
        price_dicts = [
            {"high": p.high_price, "low": p.low_price, "close": p.close_price} for p in history
        ]
        atr = calculate_atr(price_dicts, period=14)
        # 주가 대비 클램프
        close = history[-1].close_price
        return clamp_atr(atr, close)

    @staticmethod
    def _is_stop_loss_signal(signal) -> bool:
        """손절 관련 시그널인지 판별."""
        return signal.reason in (
            SellReason.STOP_LOSS,
            SellReason.PROFIT_FLOOR,
        )

    @staticmethod
    def _is_take_profit_signal(signal) -> bool:
        """익절 관련 시그널인지 판별."""
        return signal.reason in (
            SellReason.PROFIT_TARGET,
            SellReason.TRAILING_STOP,
            SellReason.BREAKEVEN_STOP,
            SellReason.RSI_OVERBOUGHT,
        )

    @staticmethod
    def _calc_sell_quantity(total_qty: int, sell_pct: float) -> int:
        """매도 비율로 수량 계산."""
        if sell_pct >= 100.0:
            return total_qty
        qty = max(1, int(math.ceil(total_qty * sell_pct / 100)))
        return min(qty, total_qty)
